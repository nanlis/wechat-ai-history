#!/usr/bin/env python3
"""Verify a raw key against every WeChat database on this machine.

Answers two questions at once:
  1. Does this key open any database here?  (i.e. is it still valid)
  2. Which account does it belong to?        (the raw key is account-scoped)

Cross-platform: reads WECHAT_DATA_ROOT when set, otherwise probes the default
WeChat 4.x locations for this platform.  Nothing about any particular account
is baked in.
"""
import glob
import hashlib
import hmac
import os
import struct
import sys

PAGE_SZ, SALT_SZ, KEY_SZ = 4096, 16, 32
_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _SKILL_DIR)
import accounts  # noqa: E402

KEY_FILES = (
    os.path.join(_SKILL_DIR, "key_windows.txt"),
    os.path.join(_SKILL_DIR, "key.txt"),
)


def verify_enc_key(enc_key, db_page1):
    salt = db_page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = db_page1[SALT_SZ: PAGE_SZ - 80 + 16]
    stored_hmac = db_page1[PAGE_SZ - 64: PAGE_SZ]
    hm = hmac.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac


def candidate_roots():
    """Every account db_storage directory we can see on this machine."""
    roots = accounts.list_account_roots()
    if roots:
        return roots
    # Fall back to the other platform's default layout (macOS sandbox path).
    mac = glob.glob(os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
        "xwechat_files/*/db_storage"
    ))
    return sorted(mac)


def load_key(argv):
    if argv:
        return argv[0].strip()
    for path in KEY_FILES:
        if os.path.exists(path):
            with open(path, encoding="ascii") as fh:
                return fh.read().strip()
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    raw_hex = load_key(argv)
    if not raw_hex:
        print("Usage: verify_key.py <64-char-hex-key>")
        print("Or ensure key_windows.txt / key.txt exists in the skill directory")
        return 1
    try:
        raw_key = bytes.fromhex(raw_hex)
    except ValueError:
        print("ERR: key must be hexadecimal")
        return 1
    if len(raw_key) != 32:
        print("ERR: key must be 64 hex characters")
        return 1

    roots = candidate_roots()
    if not roots:
        print("ERR: no WeChat db_storage found; set WECHAT_DATA_ROOT to the xwechat_files directory")
        return 1

    matched_accounts = []
    total_ok = total_fail = 0
    for base in roots:
        name = accounts.account_name(base)
        dbs = glob.glob(os.path.join(base, "**", "*.db"), recursive=True)
        ok = fail = 0
        for db_path in dbs:
            with open(db_path, "rb") as fh:
                page1 = fh.read(PAGE_SZ)
            if len(page1) < PAGE_SZ or page1[:15] == b"SQLite format 3":
                continue  # not encrypted / truncated
            derived = hashlib.pbkdf2_hmac("sha512", raw_key, page1[:SALT_SZ], 256000, dklen=KEY_SZ)
            if verify_enc_key(derived, page1):
                ok += 1
            else:
                fail += 1
                print(f"  FAIL: {os.path.relpath(db_path, base)}")
        total_ok += ok
        total_fail += fail
        if ok:
            matched_accounts.append(name)
        print(f"{name}: {ok} OK, {fail} FAILED")

    print(f"\nVerified: {total_ok} OK, {total_fail} FAILED, {total_ok + total_fail} total")
    if matched_accounts:
        print("This key belongs to account(s): " + ", ".join(matched_accounts))
    else:
        print("This key does not open any account on this machine.")
    if total_fail > 0 or not matched_accounts:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
