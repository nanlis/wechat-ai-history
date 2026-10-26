"""Account-scope helpers for WeChat 4.x local databases.

One WeChat install can hold several accounts, each under
``xwechat_files/<wxid>_<device>/db_storage``.  The raw SQLCipher key is
derived *per account*, so a key captured from one account cannot open another
one's databases.  These helpers select the account a key belongs to instead of
guessing from directory timestamps -- which silently picks the wrong account on
a multi-account machine.

Nothing in this module knows any particular account id or path.
"""
from __future__ import annotations

import glob
import hashlib
import hmac
import os
import re
import struct

PAGE_SIZE = 4096
SALT_SIZE = 16
RESERVE = 80
KDF_ITER = 256000


def default_data_root() -> str:
    """Best-effort location of the ``xwechat_files`` directory.

    Honours ``WECHAT_DATA_ROOT`` (set by install.sh / the MCP config) and
    otherwise falls back to the default WeChat 4.x location for this platform.
    """
    override = os.environ.get("WECHAT_DATA_ROOT")
    if override:
        return os.path.expandvars(os.path.expanduser(override))
    return os.path.expanduser("~/Documents/xwechat_files")


def list_account_roots(data_root: str | None = None) -> list[str]:
    """Return every account's ``db_storage`` directory under the data root."""
    root = data_root or default_data_root()
    return sorted(
        path
        for path in glob.glob(os.path.join(root, "*", "db_storage"))
        if os.path.isdir(path)
    )


def account_name(storage_dir: str) -> str:
    """``.../wxid_abc_1fb3/db_storage`` -> ``wxid_abc_1fb3``.

    Walks up from the ``db_storage`` directory to the nearest parent that
    looks like a WeChat account directory, so a nested decrypted layout still
    resolves to the right name.
    """
    current = os.path.dirname(os.path.abspath(storage_dir))
    for _ in range(4):
        name = os.path.basename(current)
        if name.startswith("wxid_"):
            return name
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.basename(os.path.dirname(os.path.abspath(storage_dir)))


def account_wxid(storage_dir: str) -> str:
    """Strip the device suffix: ``wxid_abc_1fb3`` -> ``wxid_abc``.

    WeChat 4.x stores accounts as ``{wxid}_{device}``.  A WeChat wxid is
    ``wxid_`` followed by lowercase alphanumerics and never contains another
    underscore, so the wxid is whatever follows the ``wxid_`` prefix; the
    device suffix is only removed when one is actually present.
    """
    name = account_name(storage_dir)
    match = re.match(r"^(wxid_[a-z0-9]+)(?:_.+)?$", name)
    if match:
        return match.group(1)
    # Unknown naming scheme: fall back to dropping a trailing device token.
    return re.sub(r"_[^_]+$", "", name)


def _page1(db_path: str) -> bytes:
    try:
        with open(db_path, "rb") as handle:
            return handle.read(PAGE_SIZE)
    except OSError:
        return b""


def key_opens_db(raw_key: bytes, db_path: str) -> bool:
    """True when ``raw_key`` is the key that opens this database.

    Every database carries its own salt, so its key is
    ``PBKDF2-HMAC-SHA512(raw_key, salt)``.  The page-1 HMAC-SHA512
    authentication tag proves the key without ever touching plaintext.
    """
    page = _page1(db_path)
    if len(page) != PAGE_SIZE or page[:15] == b"SQLite format 3":
        return False
    salt = page[:SALT_SIZE]
    derived = hashlib.pbkdf2_hmac("sha512", raw_key, salt, KDF_ITER, 32)
    mac_salt = bytes(value ^ 0x3A for value in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", derived, mac_salt, 2, 32)
    expected = hmac.new(
        mac_key,
        page[SALT_SIZE:PAGE_SIZE - RESERVE + 16] + struct.pack("<I", 1),
        hashlib.sha512,
    ).digest()
    return hmac.compare_digest(expected, page[PAGE_SIZE - 64:PAGE_SIZE])


def account_key_matches(raw_key: bytes, storage_dir: str) -> bool:
    """True when ``raw_key`` belongs to this account.

    Any message shard is enough -- each shard has its own salt but they all
    derive from the same account key.
    """
    for db_path in sorted(
        glob.glob(os.path.join(storage_dir, "message", "message_[0-9].db"))
    ):
        if key_opens_db(raw_key, db_path):
            return True
    return False


def select_accounts(
    raw_key: bytes,
    data_root: str | None = None,
    explicit: str | None = None,
) -> list[str]:
    """Account storage directories that ``raw_key`` can open.

    ``explicit`` pins one account by directory name or wxid (case
    insensitive, substring match).  Without it every account is probed, so a
    key captured from any account selects itself -- no account id is baked in.

    An explicit filter never bypasses the key check: if the named account
    cannot be opened with this key, nothing is returned.
    """
    roots = list_account_roots(data_root)
    if explicit:
        wanted = explicit.strip().lower()
        picked = [
            root
            for root in roots
            if wanted in account_name(root).lower() or wanted in account_wxid(root).lower()
        ]
        if not picked:
            raise FileNotFoundError(
                f"No account matching {explicit!r} under the WeChat data root"
            )
        return [root for root in picked if account_key_matches(raw_key, root)]
    return [root for root in roots if account_key_matches(raw_key, root)]


def decrypted_account_roots(decrypted_dir: str) -> list[tuple[str, str]]:
    """Return ``[(account_name, storage_dir)]`` for an already-decrypted tree.

    The decrypted mirror keeps WeChat's ``<account>/db_storage`` layout, but
    the search is recursive so a nested layout still resolves.
    """
    found = []
    for storage in sorted(
        glob.glob(os.path.join(decrypted_dir, "**", "db_storage"), recursive=True)
    ):
        if os.path.isdir(storage):
            found.append((account_name(storage), storage))
    return found


def selected_account() -> str:
    """The account pinned via ``WECHAT_ACCOUNT`` (empty string when unset)."""
    return os.environ.get("WECHAT_ACCOUNT", "").strip()
