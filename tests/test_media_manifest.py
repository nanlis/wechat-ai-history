"""Synthetic fixtures mirror the read-only verified macOS v4 index schema."""
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

import pytest

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aK1sAAAAASUVORK5CYII=')
STAMP = int(datetime(2026, 1, 2, 12).timestamp())


def packed(identifier):
    return b'\x12\x22\x0a\x20' + identifier.encode()


@pytest.fixture
def indexed(win_backend, tmp_path, monkeypatch):
    import config, db, contacts
    from conftest import SAMPLE_WXID, SAMPLE_TABLE
    contacts.invalidate_cache()
    storage = Path(db.find_data_dir())
    source = tmp_path / 'original' / storage.parent.name
    (source / 'db_storage').mkdir(parents=True)
    monkeypatch.setattr(config, 'WECHAT_DATA_GLOB', str(source.parent / '*' / 'db_storage'))
    resource = storage / 'message/message_resource.db'
    link_db = storage / 'hardlink/hardlink.db'
    link_db.parent.mkdir()
    with sqlite3.connect(resource) as con:
        con.execute('CREATE TABLE ChatName2Id(user_name TEXT,update_time INTEGER)')
        con.executemany('INSERT INTO ChatName2Id VALUES (?,0)', [(SAMPLE_WXID,), ('wxid_other',)])
        con.execute('CREATE TABLE MessageResourceInfo(message_id INTEGER PRIMARY KEY,chat_id INTEGER,'
                    'message_local_id INTEGER,message_svr_id INTEGER,message_create_time INTEGER,'
                    'message_local_type INTEGER,packed_info BLOB)')
    with sqlite3.connect(link_db) as con:
        con.execute('CREATE TABLE dir2id(username TEXT)')
        con.executemany('INSERT INTO dir2id VALUES (?)',
                        [(hashlib.md5(SAMPLE_WXID.encode()).hexdigest(),), ('2026-01',), ('../outside',)])
        con.execute('CREATE TABLE image_hardlink_info_v4(file_name TEXT,dir1 INTEGER,dir2 INTEGER)')

    class Fixture:
        conversation = SAMPLE_WXID
        table = SAMPLE_TABLE

        def add(self, local_id=10, server_id=1010, identifier='a'*32, stamp=STAMP,
                shard='message_0.db', content=PNG, chat_id=1, month_id=2, name=None):
            filename = name or identifier + '.dat'
            with sqlite3.connect(resource) as con:
                con.execute('INSERT INTO MessageResourceInfo VALUES (NULL,?,?,?,?,?,?)',
                            (chat_id, local_id, server_id, stamp, 3, packed(identifier)))
            with sqlite3.connect(link_db) as con:
                con.execute('INSERT INTO image_hardlink_info_v4 VALUES (?,1,?)', (filename, month_id))
            location = source / 'msg/attach' / hashlib.md5(SAMPLE_WXID.encode()).hexdigest() / '2026-01/Img' / filename
            location.parent.mkdir(parents=True, exist_ok=True)
            if content is not None:
                location.write_bytes(content)
            return {'_db': str(storage / 'message' / shard), 'local_id': local_id,
                    'server_id': server_id, 'create_time': stamp, 'local_type': 3,
                    'msg_hex': ('<msg><img md5="' + 'f'*32 + '" aeskey="SECRET" cdnurl="https://example.invalid/private"/></msg>').encode().hex()}, location

        def export(self, rows, name='chat.txt'):
            import media_manifest
            return media_manifest.export_images(rows, SAMPLE_WXID, SAMPLE_TABLE,
                                                 tmp_path / name, storage, source)

    fixture = Fixture()
    fixture.storage, fixture.source = storage, source
    fixture.resource, fixture.hardlink, fixture.output = resource, link_db, tmp_path
    return fixture


def test_index_chain_resolves_registered_name_without_xml_md5_guess(indexed):
    row, _ = indexed.add()
    manifest = indexed.export([row])
    entry = manifest['entries'][0]
    assert entry['status'] == 'resolved'
    path = indexed.output / entry['files'][0]['path']
    assert path.read_bytes() == PNG
    assert not Path(entry['files'][0]['path']).is_absolute()
    assert entry['message']['shard'] == 'message/message_0.db'
    assert entry['message']['local_id'] == 10
    serialized = json.dumps(manifest)
    assert all(value not in serialized for value in ['SECRET', 'cdnurl', 'packed_hex', 'example.invalid', str(indexed.source)])
    if os.name != 'nt':
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE((indexed.output/manifest['assets_directory']).stat().st_mode) == 0o700


def test_missing_server_id_and_cross_shard_local_ids_remain_distinct(indexed):
    first, _ = indexed.add(server_id=0)
    second, _ = indexed.add(server_id=0, identifier='b'*32, stamp=STAMP+1, shard='message_1.db')
    entries = indexed.export([first, second])['entries']
    assert all(e['status'] == 'resolved' for e in entries)
    assert all('server_id' not in e['message'] for e in entries)
    assert entries[0]['message'] != entries[1]['message']
    assert entries[0]['files'][0]['path'] != entries[1]['files'][0]['path']


def test_equal_basename_for_distinct_messages_never_overwrites(indexed):
    first, _ = indexed.add()
    second, _ = indexed.add(local_id=11, server_id=1011, shard='message_1.db')
    entries = indexed.export([first, second])['entries']
    paths = [indexed.output / e['files'][0]['path'] for e in entries]
    assert len(set(paths)) == 2
    assert all(p.read_bytes() == PNG for p in paths)


@pytest.mark.parametrize('content,expected,reason', [
    (None, 'not_found', 'file_missing_or_unreadable'),
    (b'\x07\x08V2\x08\x07encrypted', 'encrypted_unsupported', 'encrypted_unsupported'),
])
def test_missing_and_encrypted_assets_are_explicit(indexed, content, expected, reason):
    row, _ = indexed.add(content=content)
    entry = indexed.export([row])['entries'][0]
    assert (entry['status'], entry['reason'], entry['files']) == (expected, reason, [])
    assert not list(indexed.output.glob('chat.assets-*/**/*'))


def test_duplicate_resource_mapping_does_not_choose_first(indexed):
    row, _ = indexed.add()
    indexed.add(identifier='b'*32)
    entry = indexed.export([row])['entries'][0]
    assert entry['status'] == 'not_found' and entry['reason'] == 'ambiguous_resource'


def test_duplicate_location_does_not_choose_first(indexed):
    row, _ = indexed.add()
    with sqlite3.connect(indexed.hardlink) as con:
        con.execute("INSERT INTO dir2id VALUES ('2026-02')")
        con.execute('INSERT INTO image_hardlink_info_v4 VALUES (?,1,4)', ('a'*32+'.dat',))
    assert indexed.export([row])['entries'][0]['reason'] == 'ambiguous_location'


@pytest.mark.parametrize('change', ['server', 'chat', 'date', 'profile', 'schema'])
def test_wrong_or_unsupported_relationship_is_not_resolved(indexed, change):
    row, _ = indexed.add()
    with sqlite3.connect(indexed.resource) as con:
        if change == 'server':
            con.execute('UPDATE MessageResourceInfo SET message_svr_id=999')
        elif change == 'chat':
            con.execute('UPDATE MessageResourceInfo SET chat_id=2')
        elif change == 'date':
            con.execute('UPDATE MessageResourceInfo SET message_create_time=0')
        elif change == 'profile':
            con.execute('UPDATE MessageResourceInfo SET packed_info=?', (b'unrelated '+b'a'*32,))
        else:
            con.execute('DROP TABLE ChatName2Id')
    assert indexed.export([row])['entries'][0]['status'] == 'not_found'


def test_only_selected_message_assets_are_copied(indexed):
    selected, _ = indexed.add()
    indexed.add(local_id=20, server_id=1020, identifier='b'*32)
    manifest = indexed.export([selected])
    entries = manifest['entries']
    assert len(entries) == 1
    assert len([p for p in (indexed.output/manifest['assets_directory']).rglob('*') if p.is_file()]) == 1


def test_equal_local_ids_in_different_conversations_remain_distinct(indexed):
    import media_manifest
    first, _ = indexed.add()
    second, _ = indexed.add(chat_id=2)
    other_directory = hashlib.md5(b'wxid_other').hexdigest()
    with sqlite3.connect(indexed.hardlink) as con:
        con.execute('INSERT INTO dir2id VALUES (?)', (other_directory,))
        con.execute('INSERT INTO image_hardlink_info_v4 VALUES (?,4,2)', ('a'*32+'.dat',))
    path = indexed.source/'msg/attach'/other_directory/'2026-01/Img'/('a'*32+'.dat')
    path.parent.mkdir(parents=True)
    path.write_bytes(PNG)
    one = indexed.export([first])['entries'][0]
    two = media_manifest.export_images([second], 'wxid_other', 'Msg_'+other_directory,
                                      indexed.output/'chat.txt', indexed.storage, indexed.source)['entries'][0]
    assert one['status'] == two['status'] == 'resolved'
    assert one['message'] != two['message']
    assert one['files'][0]['path'] != two['files'][0]['path']


def test_two_registered_extensions_cannot_collapse_to_one_export(indexed):
    row, _ = indexed.add()
    with sqlite3.connect(indexed.hardlink) as con:
        con.execute('INSERT INTO image_hardlink_info_v4 VALUES (?,1,2)', ('a'*32+'.png',))
    source = indexed.source/'msg/attach'/hashlib.md5(indexed.conversation.encode()).hexdigest()/'2026-01/Img'/('a'*32+'.png')
    source.write_bytes(PNG+b'distinct payload')
    files = indexed.export([row])['entries'][0]['files']
    assert len({f['path'] for f in files}) == 2
    assert sorted((indexed.output/f['path']).read_bytes() for f in files) == sorted([PNG,PNG+b'distinct payload'])


def test_empty_selection_writes_empty_manifest_without_database_access(monkeypatch):
    import db, media_manifest
    monkeypatch.setattr(db, 'find_data_dir', lambda: pytest.fail('no image means no discovery'))
    assert media_manifest.export_images([], 'contact', 'table', 'chat.txt')['entries'] == []


def test_changing_source_does_not_publish_partial_image(indexed, monkeypatch):
    import media_manifest
    row, source = indexed.add()
    original = media_manifest.shutil.copyfileobj
    def changing_copy(reader, writer):
        original(reader, writer)
        with source.open('ab') as handle:
            handle.write(b'changed during copy')
    monkeypatch.setattr(media_manifest.shutil, 'copyfileobj', changing_copy)
    entry = indexed.export([row])['entries'][0]
    assert entry['status'] == 'not_found' and entry['reason'] == 'resource_changed'
    assert not [p for p in indexed.output.glob('chat.assets-*/**/*') if p.is_file()]


def test_index_path_cannot_escape_account(indexed):
    row, _ = indexed.add(month_id=3)
    assert indexed.export([row])['entries'][0]['reason'] == 'unsafe_index_path'


def test_source_and_destination_symlinks_cannot_escape(indexed, tmp_path, monkeypatch):
    row, location = indexed.add()
    outside = tmp_path/'elsewhere.png'; outside.write_bytes(PNG)
    location.unlink()
    try:
        location.symlink_to(outside)
    except OSError:
        pytest.skip('Host does not support unprivileged symlinks')
    assert indexed.export([row])['entries'][0]['reason'] == 'unsafe_index_path'
    location.unlink(); location.write_bytes(PNG)
    # Fix the generated name to exercise an existing hostile export-root link.
    import media_manifest
    from types import SimpleNamespace
    monkeypatch.setattr(media_manifest.uuid, 'uuid4', lambda: SimpleNamespace(hex='0'*32))
    (tmp_path/('chat.assets-'+'0'*16)).symlink_to(tmp_path.parent, target_is_directory=True)
    assert indexed.export([row])['entries'][0]['reason'] == 'unsafe_export_path'


def test_repeated_export_isolates_selected_assets_without_deleting_old_files(indexed):
    first, _ = indexed.add()
    second, _ = indexed.add(local_id=20, server_id=1020, identifier='b'*32, stamp=STAMP+86400)
    old = indexed.export([first])
    new = indexed.export([second])
    assert old['assets_directory'] != new['assets_directory']
    old_file = indexed.output / old['entries'][0]['files'][0]['path']
    new_file = indexed.output / new['entries'][0]['files'][0]['path']
    assert old_file.read_bytes() == new_file.read_bytes() == PNG
    current_files = [p for p in (indexed.output/new['assets_directory']).rglob('*') if p.is_file()]
    assert current_files == [new_file]
    assert old_file not in current_files


@pytest.mark.parametrize('extension,marker', [('txt','[resolved] ->'), ('md','![Image]('), ('html','<img src=')])
def test_cli_exports_selected_window_manifest_and_relative_links(indexed, monkeypatch, extension, marker):
    import export_chat, contacts, db
    row, _ = indexed.add()
    indexed.add(local_id=11, server_id=1011, identifier='b'*32, stamp=STAMP+86400*3)
    path = Path(row['_db'])
    with sqlite3.connect(path) as con:
        con.execute(f'DELETE FROM {indexed.table}')
        con.execute(f'INSERT INTO {indexed.table} VALUES (?,?,?,?,?,?)',
                    (row['local_id'],row['server_id'],row['create_time'],3,1,bytes.fromhex(row['msg_hex'])))
        con.execute(f'INSERT INTO {indexed.table} VALUES (11,1011,?,3,1,?)',
                    (STAMP+86400*3, b'outside date window'))
    contacts.invalidate_cache(); db.reset_caches()
    output = indexed.output / ('聊天 '+extension+'.'+extension)
    monkeypatch.setattr(export_chat.message, 'has_zstd_decoder', lambda: True)
    monkeypatch.setattr(sys, 'argv', ['export_chat.py', indexed.conversation,
                        '--start','2026-01-02','--end','2026-01-02','--no-transcribe',
                        '--with-media','-o',str(output)])
    export_chat.main()
    data = json.loads(output.with_suffix('.media.json').read_text(encoding='utf-8'))
    assert len(data['entries']) == 1 and data['entries'][0]['status'] == 'resolved'
    content = output.read_text(encoding='utf-8')
    assert marker in content
    assert 'outside date window' not in content and 'SECRET' not in content
    for item in data['entries'][0]['files']:
        assert (output.parent / item['path']).read_bytes() == PNG
    if os.name != 'nt':
        assert stat.S_IMODE(output.with_suffix('.media.json').stat().st_mode) == 0o600


def test_html_and_markdown_do_not_interpret_message_markup():
    import transcript_render
    line='<script>alert(1)</script> ![remote](https://example.invalid/pixel)'
    html=''.join(transcript_render.render('<header>',[(line,None)],'html'))
    assert '<script>' not in html and '&lt;script&gt;' in html
    markdown=''.join(transcript_render.render('header',[(line,None)],'markdown'))
    assert '![remote](' not in markdown and '&lt;script&gt;' in markdown
