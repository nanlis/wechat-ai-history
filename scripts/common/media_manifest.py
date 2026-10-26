"""Resolve image resources through WeChat's local indices; export no key material."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import uuid

import db
import message


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _fields(blob):
    """Read bounded protobuf fields without interpreting unrelated payloads."""
    offset = 0

    def varint():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(blob):
                raise ValueError('truncated protobuf')
            byte = blob[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError('oversized protobuf integer')

    result = []
    while offset < len(blob):
        key = varint()
        field, wire = key >> 3, key & 7
        if not field:
            raise ValueError('invalid protobuf field')
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else 8 if wire == 1 else 4
            value = blob[offset:offset + length]
            offset += length
            if len(value) != length:
                raise ValueError('truncated protobuf value')
        else:
            raise ValueError('unsupported protobuf wire type')
        result.append((field, wire, value))
    return result


def _image_resource_id(packed_hex):
    # Verified macOS v4 profile: MessageResourceInfo.packed_info field 2.1
    # names the hardlink file identifier, not the image XML's content MD5.
    try:
        blob = bytes.fromhex(packed_hex or '')
        if len(blob) > 4096:
            return None
        outer = [v for f, w, v in _fields(blob) if f == 2 and w == 2]
        if len(outer) != 1:
            return None
        inner = [v for f, w, v in _fields(outer[0]) if f == 1 and w == 2]
        if len(inner) == 1 and re.fullmatch(rb'[0-9a-fA-F]{32}', inner[0]):
            return inner[0].decode('ascii').lower()
    except (ValueError, TypeError):
        pass
    return None


def _columns(path, table):
    return {r['name'] for r in db.query(str(path), f'PRAGMA table_info("{table}");')}


def _has_schema(path, required):
    return path.is_file() and all(cols <= _columns(path, table)
                                 for table, cols in required.items())


class ImageIndex:
    """One conversation's resource rows and registered image locations."""
    def __init__(self, storage, conversation, rows):
        self.resources = {}
        self.links = {}
        self.error = None
        resource = storage / 'message' / 'message_resource.db'
        hardlink = storage / 'hardlink' / 'hardlink.db'
        required = {
            'ChatName2Id': {'user_name'},
            'MessageResourceInfo': {'message_id', 'chat_id', 'message_local_id',
                                    'message_svr_id', 'message_create_time',
                                    'message_local_type', 'packed_info'},
        }
        link_schema = {
            'dir2id': {'username'},
            'image_hardlink_info_v4': {'file_name', 'dir1', 'dir2'},
        }
        try:
            if not resource.is_file() or not hardlink.is_file():
                self.error = 'index_missing'
                return
            if not _has_schema(resource, required) or not _has_schema(hardlink, link_schema):
                self.error = 'unsupported_schema'
                return
            timestamps = [int(r['create_time']) for r in rows]
            found = db.query(str(resource),
                'SELECT i.message_id, i.message_local_id, i.message_svr_id, '
                'i.message_create_time, hex(i.packed_info) AS packed_hex '
                'FROM MessageResourceInfo i JOIN ChatName2Id c ON c.rowid=i.chat_id '
                f'WHERE c.user_name={_literal(conversation)} '
                'AND (i.message_local_type & 65535)=3 '
                f'AND i.message_create_time BETWEEN {min(timestamps)} AND {max(timestamps)};')
            wanted = {(str(r['local_id']), int(r['create_time'])) for r in rows}
            identifiers = set()
            for row in found:
                key = str(row['message_local_id']), int(row['message_create_time'])
                if key not in wanted:
                    continue
                self.resources.setdefault(key, []).append(row)
                identifier = _image_resource_id(row['packed_hex'])
                if identifier:
                    identifiers.add(identifier)
            # A conversation directory is independently registered in dir2id.
            directory = hashlib.md5(conversation.encode('utf-8')).hexdigest()
            ids = sorted(identifiers)
            for start in range(0, len(ids), 200):
                selected = ','.join(_literal(v) for v in ids[start:start + 200])
                links = db.query(str(hardlink),
                    'SELECT h.file_name, d1.username AS dir1_name, d2.username AS dir2_name '
                    'FROM image_hardlink_info_v4 h '
                    'JOIN dir2id d1 ON d1.rowid=h.dir1 '
                    'JOIN dir2id d2 ON d2.rowid=h.dir2 '
                    f'WHERE d1.username={_literal(directory)} '
                    f'AND lower(substr(h.file_name,1,32)) IN ({selected});')
                for link in links:
                    identifier = link['file_name'][:32].lower()
                    self.links.setdefault(identifier, []).append(link)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, sqlite3.DatabaseError):
            # Do not include database errors/paths or raw resource payloads in exports.
            self.error = 'index_unreadable'

    def resolve(self, row):
        if self.error:
            return [], self.error
        matches = self.resources.get((str(row['local_id']), int(row['create_time'])), [])
        sid = str(row.get('server_id') or '0')
        if sid != '0':
            matches = [r for r in matches if str(r['message_svr_id']) == sid]
        if len(matches) != 1:
            return [], 'ambiguous_resource' if matches else 'resource_missing'
        identifier = _image_resource_id(matches[0]['packed_hex'])
        if not identifier:
            return [], 'unsupported_resource_profile'
        unique = {}
        for link in self.links.get(identifier, []):
            name = link['file_name']
            if isinstance(name, str) and re.fullmatch(re.escape(identifier) + r'(?:_[th])?\.[a-zA-Z0-9]+', name, re.IGNORECASE):
                unique[(link['dir1_name'], link['dir2_name'], name)] = link
        links = list(unique.values())
        # Equal names in different directories are ambiguous, not a first-match choice.
        locations = {(r['dir1_name'], r['dir2_name']) for r in links}
        if len(locations) > 1:
            return [], 'ambiguous_location'
        return links, None if links else 'hardlink_missing'


def _contained(path, root):
    return path.resolve().is_relative_to(root.resolve())


def _image_suffix(header):
    if header.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if header.startswith((b'GIF87a', b'GIF89a')):
        return '.gif'
    if header.startswith(b'RIFF') and header[8:12] == b'WEBP':
        return '.webp'
    return None


def _copy_image(link, account, destination):
    parts = [link['dir1_name'], link['dir2_name'], link['file_name']]
    if any(not isinstance(p, str) or not p or p in ('.', '..') or re.search(r'[/\\:\x00]', p) for p in parts):
        return None, 'unsafe_index_path'
    source = account / 'msg' / 'attach' / parts[0] / parts[1] / 'Img' / parts[2]
    if not _contained(source, account):
        return None, 'unsafe_index_path'
    try:
        with source.open('rb') as handle:
            header = handle.read(12)
            suffix = _image_suffix(header)
            if not suffix:
                return None, 'encrypted_unsupported' if source.suffix.lower() == '.dat' else 'unsupported_image_format'
            target = destination / (source.name if source.suffix.lower() == suffix else source.name + suffix)
            if not _contained(target, destination.parent.parent):
                return None, 'unsafe_export_path'
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(destination.parent, 0o700)
            destination.mkdir(exist_ok=True, mode=0o700)
            os.chmod(destination, 0o700)
            before = os.fstat(handle.fileno())
            handle.seek(0)
            fd, temporary = tempfile.mkstemp(prefix='.image-', dir=destination)
            try:
                with os.fdopen(fd, 'wb') as output:
                    shutil.copyfileobj(handle, output)
                after = os.fstat(handle.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    return None, 'resource_changed'
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return target, None
    except OSError:
        return None, 'file_missing_or_unreadable'


def write_manifest(path, manifest):
    """Publish a private JSON sidecar atomically."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.manifest-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_images(rows, conversation, table, output_path, storage=None, account=None):
    """Return a sidecar containing only image messages in the selected transcript."""
    selected = [r for r in rows if message.normalize_type(r.get('local_type', '')) == '3']
    output = Path(output_path).absolute()
    manifest = {'version': 1, 'transcript': output.name, 'entries': []}
    if not selected:
        return manifest
    storage = Path(storage) if storage is not None else Path(db.find_data_dir())
    try:
        account = Path(account) if account is not None else Path(db.get_account_files_dir())
    except FileNotFoundError:
        account = None
    snapshot = hashlib.sha256(str(storage.resolve()).encode())
    for path in sorted({Path(r['_db']) for r in selected} | {
            storage / 'message/message_resource.db', storage / 'hardlink/hardlink.db'}):
        snapshot.update(str(path.relative_to(storage)).encode())
        if path.exists():
            info = path.stat()
            snapshot.update(f'{info.st_size}:{info.st_mtime_ns}'.encode())
    manifest['snapshot'] = snapshot.hexdigest()
    asset_root = output.parent / (output.stem + '.assets-' + uuid.uuid4().hex[:16])
    index = ImageIndex(storage, conversation, selected)
    for row in selected:
        identity = {'conversation': conversation, 'table': table,
                    'shard': Path(row['_db']).relative_to(storage).as_posix(),
                    'local_id': int(row['local_id'])}
        if str(row.get('server_id') or '0') != '0':
            identity['server_id'] = str(row['server_id'])
        entry = {'message': identity, 'kind': 'image', 'status': 'not_found', 'files': []}
        token = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        destination = asset_root / token
        links, reason = index.resolve(row)
        reasons = []
        if account is None:
            reason = 'original_account_missing'
            links = []
        for link in links:
            target, failure = _copy_image(link, account, destination)
            if target:
                entry['files'].append({'path': target.relative_to(output.parent).as_posix()})
            elif failure:
                reasons.append(failure)
        if entry['files']:
            entry['status'] = 'resolved'
            manifest['assets_directory'] = asset_root.name
        else:
            if 'encrypted_unsupported' in reasons:
                entry['status'] = 'encrypted_unsupported'
            entry['reason'] = reason or (reasons[0] if reasons else 'file_missing_or_unreadable')
        manifest['entries'].append(entry)
    return manifest
