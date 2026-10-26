"""End-to-end rehearsal for an account this codebase has never seen.

Builds a synthetic SQLCipher v4 database under an account directory name and
a key that appear nowhere in the source, then drives the real decrypt_all.py
main() over it.  If the implementation ever regains an assumption about a
specific account id, key, or data path, this test fails.
"""
import hashlib
import hmac
import importlib.util
import os
import sqlite3
import struct
from pathlib import Path

import pytest

from Crypto.Cipher import AES

ROOT = Path(__file__).parents[1]
PAGE, SALT, RESERVE = 4096, 16, 80
RSTART = PAGE - RESERVE

# Deliberately unlike anything the repository mentions.
UNKNOWN_ACCOUNT = "wxid_zqx9k2m4newcomer_a1b2"
UNKNOWN_KEY = bytes([0xA7, 0x13, 0x5E, 0x9B, 0x22, 0x7F, 0xC4, 0x08]) * 4
UNKNOWN_SALT = bytes([0x31, 0x72, 0xE5, 0x0C, 0x99, 0x46, 0xAB, 0x12,
                      0x5D, 0x88, 0xF1, 0x34, 0x6E, 0xBD, 0x20, 0xC7])

# WeChat's page-1 plaintext starts with this, not "SQLite format 3".
WECHAT_PAGE1_HEADER = bytes([0x10, 0x00, 1, 1, 0x50, 0x40, 0x20, 0x20])


def _encrypt_page(plain: bytes, page_number: int, raw_key: bytes, salt: bytes) -> bytes:
    derived = hashlib.pbkdf2_hmac("sha512", raw_key, salt, 256000, 32)
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", derived, mac_salt, 2, 32)
    iv = bytes([page_number % 256]) * 16
    offset = SALT if page_number == 1 else 0
    body = plain[:RSTART - offset]
    cipher = AES.new(derived, AES.MODE_CBC, iv).encrypt(body)
    page = bytearray(PAGE)
    if page_number == 1:
        page[0:SALT] = salt
    page[offset:RSTART] = cipher
    page[RSTART:RSTART + 16] = iv
    tag = hmac.new(
        mac_key, bytes(page[offset:PAGE - 64]) + struct.pack("<I", page_number), hashlib.sha512
    ).digest()
    page[PAGE - 64:PAGE] = tag
    return bytes(page)


def _build_encrypted_db(path: Path, raw_key: bytes, salt: bytes) -> bytes:
    """Write an encrypted 2-page DB; return the page-1 plaintext for comparison."""
    plain = bytearray(PAGE * 2)
    plain[0:8] = WECHAT_PAGE1_HEADER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        _encrypt_page(bytes(plain[0:PAGE]), 1, raw_key, salt)
        + _encrypt_page(bytes(plain[PAGE:PAGE * 2]), 2, raw_key, salt)
    )
    return bytes(plain[:RSTART - SALT])


def _decrypt_all_module():
    spec = importlib.util.spec_from_file_location(
        "decrypt_all_e2e", ROOT / "scripts" / "windows" / "decrypt_all.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unseen_account_and_key_survive_the_full_pipeline(tmp_path, monkeypatch):
    data_root = tmp_path / "xwechat_files"
    db_path = data_root / UNKNOWN_ACCOUNT / "db_storage" / "message" / "message_0.db"
    page1_plain = _build_encrypted_db(db_path, UNKNOWN_KEY, UNKNOWN_SALT)

    module = _decrypt_all_module()
    module.DECRYPTED_DIR = str(tmp_path / "decrypted")
    monkeypatch.setenv("WECHAT_DATA_ROOT", str(data_root))

    # The account is recognised without anyone naming it.
    module.main([UNKNOWN_KEY.hex()])

    out = Path(module.DECRYPTED_DIR) / UNKNOWN_ACCOUNT / "db_storage" / "message" / "message_0.db"
    assert out.exists(), "no plaintext produced for an unknown account"
    data = out.read_bytes()
    assert data[:16] == b"SQLite format 3\x00"
    assert data[16:16 + len(page1_plain)] == page1_plain


def test_unknown_account_is_not_silently_skipped(tmp_path, monkeypatch, capsys):
    data_root = tmp_path / "xwechat_files"
    db_path = data_root / UNKNOWN_ACCOUNT / "db_storage" / "message" / "message_0.db"
    _build_encrypted_db(db_path, UNKNOWN_KEY, UNKNOWN_SALT)

    module = _decrypt_all_module()
    module.DECRYPTED_DIR = str(tmp_path / "decrypted")
    monkeypatch.setenv("WECHAT_DATA_ROOT", str(data_root))

    # A key that belongs to nobody here must fail loudly, not silently pass.
    with pytest.raises(SystemExit):
        module.main([("ff" * 32)])

    output = capsys.readouterr().out
    assert "does not open any account" in output
