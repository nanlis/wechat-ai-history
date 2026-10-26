#!/usr/bin/env python3
"""Decrypt EVERY WeChat db under db_storage into the plaintext DECRYPTED_DIR (Windows sqlite3 backend).

After extract_raw_key.py yields the raw key, this rebuilds a full plaintext mirror so server.py /
export_chat.py / transcribe_db.py (config.DB_BACKEND='sqlite3') can read it like Mac reads the
encrypted store directly. Preserves the {wxid}_{device}/db_storage/... layout that db.py expects.

ACCOUNT SCOPE: the raw key belongs to exactly one WeChat account. This script therefore probes
every account directory under the data root and decrypts the one(s) the key actually opens, instead
of guessing from directory timestamps. Pass --account <name-or-wxid> to pin one explicitly.

Pure pycryptodome (the VM has no `cryptography`). Usage: python decrypt_all.py [raw_key_hex]
[--account <name>] (raw key defaults to ../../key_windows.txt).
"""
import sys, os, glob, hashlib, tempfile
import hmac, struct

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
from crypto_backend import aes_cbc_decrypt as _aes_cbc_dec
from wal_snapshot import read_snapshot

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import accounts  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(_HERE))
DECRYPTED_DIR = os.path.join(SKILL_DIR, "decrypted")
PAGE, RESERVE, SALT_SZ = 4096, 80, 16


def decrypted_dir() -> str:
    """Where the plaintext mirror is written.

    Reads the module attribute at call time so tests can redirect it without
    ever touching the real decrypted store.
    """
    return DECRYPTED_DIR


def decrypt_db(raw: bytes, src: str, dst: str) -> bool:
    """Decrypt one SQLCipher v4 db to plaintext sqlite. Returns False if key/format mismatch."""
    try:
        data = read_snapshot(src)
    except ValueError:
        return False
    if len(data) < PAGE or len(data) % PAGE:
        return False
    salt = data[:SALT_SZ]
    enc = hashlib.pbkdf2_hmac("sha512", raw, salt, 256000, 32)
    mac_salt = bytes(value ^ 0x3A for value in salt)
    mac_key = hashlib.pbkdf2_hmac('sha512', enc, mac_salt, 2, 32)
    for number in range(1, len(data) // PAGE + 1):
        page = data[(number - 1) * PAGE:number * PAGE]
        offset = SALT_SZ if number == 1 else 0
        expected = hmac.new(mac_key, page[offset:PAGE - 64] + struct.pack('<I', number), hashlib.sha512).digest()
        if not hmac.compare_digest(expected, page[PAGE - 64:PAGE]):
            return False
    rstart = PAGE - RESERVE
    # verify page 1 header before committing
    iv0 = data[rstart:rstart + 16]
    pt0 = _aes_cbc_dec(enc, iv0, data[SALT_SZ:rstart])
    if not (pt0[0] == 0x10 and pt0[1] == 0x00 and pt0[4] == 0x50 and pt0[5] == 0x40 and pt0[7] == 0x20):
        return False
    out = bytearray(b"SQLite format 3\x00")
    out += pt0 + data[rstart:PAGE]
    for i in range(1, len(data) // PAGE):
        page = data[i * PAGE:(i + 1) * PAGE]
        iv = page[rstart:rstart + 16]
        out += _aes_cbc_dec(enc, iv, page[:rstart]) + page[rstart:]
    parent = os.path.dirname(dst)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass
    fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(dst) + ".", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(out)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, dst)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return True


def find_storage_roots():
    root = os.environ.get('WECHAT_DATA_ROOT')
    if root:
        root = os.path.expandvars(os.path.expanduser(root))
        return glob.glob(os.path.join(root, '*', 'db_storage'))
    return glob.glob(os.path.expanduser(r"~/Documents/xwechat_files/*/db_storage"))


def parse_args(argv):
    """Accept: [raw_key_hex] [--account <name-or-wxid>]."""
    raw_hex = None
    account = None
    rest = list(argv)
    while rest:
        item = rest.pop(0)
        if item in ("--account", "-a"):
            if not rest:
                raise SystemExit("ERR: --account needs a value")
            account = rest.pop(0)
        elif item.startswith("--account="):
            account = item.split("=", 1)[1]
        elif raw_hex is None:
            raw_hex = item
        else:
            raise SystemExit(f"ERR: unexpected argument {item!r}")
    return raw_hex, account


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    raw_hex, account_filter = parse_args(argv)
    if raw_hex:
        raw_hex = raw_hex.strip()
    else:
        key_path = os.path.join(SKILL_DIR, "key_windows.txt")
        try:
            with open(key_path, encoding="ascii") as f:
                raw_hex = f.read().strip()
        except OSError as exc:
            raise SystemExit(f"ERR: missing {key_path}; run extract_raw_key.py first") from exc
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError as exc:
        raise SystemExit("ERR: raw key must be hexadecimal") from exc
    if len(raw) != 32 or len(raw_hex) != 64:
        raise SystemExit("ERR: raw key must be exactly 64 hexadecimal characters")

    src_roots = find_storage_roots()
    if not src_roots:
        print("ERR: no encrypted db_storage found; set WECHAT_DATA_ROOT to the xwechat_files directory if moved"); sys.exit(1)

    # The key identifies the account. Probe every account directory and keep
    # the ones this key actually opens -- never guess from timestamps.
    matched = []
    for src_root in src_roots:
        name = accounts.account_name(src_root)
        if account_filter and account_filter.lower() not in name.lower():
            continue
        if accounts.account_key_matches(raw, src_root):
            matched.append(src_root)
        else:
            print("skip", name, "(key does not open this account)")

    if not matched:
        print("ERR: this key does not open any account under the data root.")
        print("     If you just switched WeChat accounts, log the new one in and re-extract the key.")
        sys.exit(1)

    total_ok = total_skip = 0
    for src_root in matched:
        account = accounts.account_name(src_root)  # {wxid}_{device}
        dst_root = os.path.join(decrypted_dir(), account, "db_storage")
        print(f"\n=== account {account} ===")

        ok = skip = 0
        for src in sorted(glob.glob(os.path.join(src_root, "**", "*.db"), recursive=True)):
            rel = os.path.relpath(src, src_root)
            dst = os.path.join(dst_root, rel)
            try:
                if decrypt_db(raw, src, dst):
                    ok += 1; print("OK  ", rel)
                else:
                    skip += 1; print("skip", rel, "(key/format mismatch or empty)")
            except Exception as e:
                skip += 1; print("ERR ", rel, e)
        print(f"{ok} decrypted, {skip} skipped -> {dst_root}")
        total_ok += ok; total_skip += skip

    if total_ok:
        print("\n>>> plaintext store ready; server.py / export_chat.py can now read it <<<")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
