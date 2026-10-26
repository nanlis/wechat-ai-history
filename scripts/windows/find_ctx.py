#!/usr/bin/env python3
"""Locate the SQLCipher codec context in WeChat's memory and extract the per-db key.

Why this works where the salt-neighbourhood scan failed
-------------------------------------------------------
In SQLCipher v4 the codec context (sqlcipher_ctx) holds *pointers*, not inline
bytes:

    int  kdf_iter;      // == 256000  (0x3E800)
    int  kdf_key_sz;    // == 32
    u8  *kdf_salt;      // -> 16 bytes, the db's own salt
    u8  *key;           // -> 32 bytes, the derived key K1
    u8  *iv;
    u8  *hmac_key;

So the salt and the key do NOT live next to each other -- that is exactly why
scanning the neighbourhood of a salt hit never produced a key.

Strategy
--------
1. Scan every rw- range for the byte pattern of 256000 little-endian
   ("00 e8 03 00"). Each hit is (almost certainly) the kdf_iter field of a
   live codec context.
2. Around each hit, dump +-1 KB (the ctx struct lives there) and follow every
   8-byte aligned value as a candidate pointer, reading 32 bytes behind it.
3. Verify each 32-byte candidate with a single AES-CBC block against each
   known db:  K1 == key  ->  decrypts page 1 -> header must look right.
   Also try the device-wide raw key (PBKDF2 -> K1) for completeness.

A hit on the salt pointer additionally tells us *which* db the ctx belongs to.

Usage: python find_ctx.py [seconds]
"""
import argparse
import glob
import hashlib
import json
import os
import struct
import sys
import time

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAND_FILE = os.path.join(SKILL_DIR, "ctx_candidates.txt")
KEY_FILE = os.path.join(SKILL_DIR, "key_windows.txt")

KDF_ITER = 256000
ITER_PATTERN = "00 e8 03 00"  # 256000 little endian
PAGE_SZ = 4096
RESERVE_SZ = 80
SALT_SZ = 16

JS = r"""
function scanIter() {
  var hits = [];
  Process.enumerateRanges('rw-').forEach(function (rg) {
    if (rg.size > 400 * 1024 * 1024) return;
    try {
      Memory.scanSync(rg.base, rg.size, "%s").forEach(function (h) { hits.push(h.address); });
    } catch (e) {}
  });
  return hits;
}

function readPtr(p) {
  try {
    if (p.isNull()) return null;
    var b = p.readByteArray(32);
    if (!b) return null;
    return b;
  } catch (e) { return null; }
}

var hits = scanIter();
send("kdf_iter hits: " + hits.length);
var dumps = 0, ptrs = 0;
hits.forEach(function (addr) {
  // 1) the ctx struct neighbourhood
  try {
    var blob = addr.sub(1024).readByteArray(2048);
    if (blob) { send({t: 'dump'}, blob); dumps++; }
  } catch (e) {}
  // 2) every aligned pointer in that neighbourhood
  for (var off = -1024; off < 1024; off += 8) {
    try {
      var p = addr.add(off).readPointer();
      var kb = readPtr(p);
      if (kb) { send({t: 'ptr'}, kb); ptrs++; }
    } catch (e) {}
  }
});
send("dumps=" + dumps + " ptr_candidates=" + ptrs);
send("SCAN DONE");
""" % ITER_PATTERN


def account_dbs():
    root = os.environ.get("WECHAT_DATA_ROOT") or os.path.expanduser(
        "~/Documents/xwechat_files"
    )
    out = []
    for storage in glob.glob(os.path.join(root, "*", "db_storage")):
        out.extend(glob.glob(os.path.join(storage, "**", "*.db"), recursive=True))
    return out


def load_db_info(dbs):
    """salt / iv / first ciphertext block + mac salt for every db."""
    from Crypto.Cipher import AES  # noqa: F401  (import check)

    info = []
    for db in dbs:
        try:
            with open(db, "rb") as fh:
                page1 = fh.read(PAGE_SZ)
        except OSError:
            continue
        if len(page1) < PAGE_SZ:
            continue
        salt = page1[:SALT_SZ]
        rstart = PAGE_SZ - RESERVE_SZ
        info.append({
            "path": db,
            "salt": salt,
            "iv": page1[rstart:rstart + 16],
            "ct": page1[SALT_SZ:SALT_SZ + 16],
        })
    return info


def header_ok(pt):
    return (len(pt) >= 8 and pt[0] == 0x10 and pt[1] == 0x00
            and pt[4] == 0x50 and pt[5] == 0x40 and pt[7] == 0x20)


def verify(candidate, infos):
    """Try candidate as a per-db derived key (K1) or as the device-wide raw key."""
    from Crypto.Cipher import AES

    for info in infos:
        try:
            pt = AES.new(candidate, AES.MODE_CBC, info["iv"]).decrypt(info["ct"])
            if header_ok(pt):
                return ("k1", info["path"])
        except Exception:
            pass
    for info in infos:
        try:
            derived = hashlib.pbkdf2_hmac("sha512", candidate, info["salt"], KDF_ITER, 32)
            pt = AES.new(derived, AES.MODE_CBC, info["iv"]).decrypt(info["ct"])
            if header_ok(pt):
                return ("raw", info["path"])
        except Exception:
            pass
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seconds", nargs="?", type=int, default=120)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    try:
        import frida
    except Exception as exc:
        print(f"ERR: frida missing: {exc}")
        return 1

    dbs = account_dbs()
    if not dbs:
        print("ERR: no db found (set WECHAT_DATA_ROOT)")
        return 1
    infos = load_db_info(dbs)
    print(f"databases under test: {len(infos)}")
    salt_index = {info["salt"].hex(): info["path"] for info in infos}

    device = frida.get_local_device()
    pids = [p.pid for p in device.enumerate_processes() if p.name.lower() == "weixin.exe"]
    if not pids:
        print("ERR: WeChat is not running")
        return 1
    print(f"pids: {pids}")

    hits = []          # candidate 32-byte blobs
    seen = set()
    found = None
    scanned = 0

    def on_message(message, data):
        nonlocal found, scanned
        if message.get("type") == "error":
            print("ERR: " + str(message.get("description", "")), flush=True)
            return
        payload = message.get("payload")
        if isinstance(payload, str):
            print(payload, flush=True)
            return
        if not data:
            return
        kind = (payload or {}).get("t", "?")
        scanned += 1
        # a) is the blob itself a salt we know? then we located the ctx
        if len(data) >= 16:
            head = bytes(data[:16]).hex()
            if head in salt_index:
                print(f"  !! ctx holds salt of {salt_index[head]}", flush=True)
        # b) every 32-byte window inside the blob is a candidate
        for off in range(0, max(0, len(data) - 31)):
            chunk = bytes(data[off:off + 32])
            if not any(chunk) or chunk in seen:
                continue
            seen.add(chunk)
            hits.append((kind, off, chunk))
        if len(hits) > 400000:
            hits.clear()

    sessions = []
    script_src = JS
    for pid in pids:
        try:
            session = device.attach(pid)
            script = session.create_script(script_src)
            script.on("message", on_message)
            script.load()
            sessions.append(session)
            print(f"attached pid {pid}", flush=True)
        except Exception as exc:
            print(f"WARN: attach pid {pid} failed: {exc}", flush=True)
    if not sessions:
        print("ERR: could not attach to any Weixin.exe process")
        return 1

    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline and not found:
            time.sleep(0.3)
            for kind, off, chunk in list(hits):
                result = verify(chunk, infos)
                if result:
                    found = chunk
                    mode, db = result
                    print(f"\n*** {mode.upper()} KEY FOUND via {kind} (off={off}) against {db} ***\n{chunk.hex()}", flush=True)
                    break
    finally:
        for session in sessions:
            try:
                session.detach()
            except Exception:
                pass

    print(f"\nscanned blobs: {scanned}, distinct windows tried: {len(seen)}")
    if not found:
        # persist every candidate so an offline run can try other KDFs later
        with open(CAND_FILE, "w", encoding="ascii") as fh:
            for _kind, _off, chunk in hits:
                fh.write(chunk.hex() + "\n")
        print(f"no key verified; {len(hits)} windows saved to {CAND_FILE}")
        return 1

    with open(KEY_FILE, "w", encoding="ascii") as fh:
        fh.write(found.hex() + "\n")
    print(f"DONE. key saved to {KEY_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
