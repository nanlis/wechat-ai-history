#!/usr/bin/env python3
"""Find a database's SQLCipher K1 (derived key) inside a RUNNING WeChat 4.x process.

Why K1 and not the raw key:
  SQLCipher 4 derives the per-db encryption key as
      enc  = PBKDF2-HMAC-SHA512(raw_key, db_salt, 256000, 32)
      mack = PBKDF2-HMAC-SHA512(enc, db_salt ^ 0x3A, 2, 32)
  The raw key is device-wide, but each open database keeps its own `enc` (K1)
  right next to that db's salt inside the codec context. Verifying a candidate
  K1 only needs ONE AES-CBC block (the db header), so the whole memory
  neighbourhood can be swept in milliseconds -- versus 256000 PBKDF2 rounds
  per window if you try to brute-force the raw key.

Steps:
  1. read the target db's page-1 salt
  2. scan the live WeChat processes for that salt
  3. dump the neighbourhood around every hit
  4. sweep every 32-byte window, AES-decrypt the db header, keep the winner

Usage: python find_k1.py <db_path> [seconds]
"""
import csv
import hashlib
import io
import os
import re
import subprocess
import sys
import time

from Crypto.Cipher import AES

PAGE_SZ = 4096
SALT_SZ = 16
RESERVE_SZ = 80


def wechat_pids():
    pids = []
    try:
        import frida
        for proc in frida.get_local_device().enumerate_processes():
            if proc.name.lower() == "weixin.exe":
                pids.append(proc.pid)
    except Exception:
        pass
    if pids:
        return pids
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace", timeout=10,
        ).stdout
        for row in csv.reader(io.StringIO(out)):
            if len(row) >= 2 and row[0].casefold() == "weixin.exe" and row[1].isdigit():
                pids.append(int(row[1]))
    except Exception:
        pass
    return pids


def header_ok(pt):
    """SQLite page-1 header sanity check on the first decrypted block."""
    return len(pt) >= 8 and pt[0] == 0x10 and pt[1] == 0x00 and pt[4] == 0x50 and pt[5] == 0x40 and pt[7] == 0x20


SCAN_JS = r"""
var saltHex = "%s";
var pat = saltHex.match(/../g).join(' ');
var ranges = Process.enumerateRanges('rw-').filter(function (r) { return r.size < 64 * 1024 * 1024; });
send("scanning " + ranges.length + " rw- ranges for salt...");
var found = 0;
ranges.forEach(function (rg) {
  try {
    Memory.scanSync(rg.base, rg.size, pat).forEach(function (r) {
      found++;
      for (var d = -4096; d <= 4096; d += 4096) {
        try { send({addr: r.address.toString()}, r.address.add(d).readByteArray(4096)); } catch (e) {}
      }
      send("SALT @ " + r.address);
    });
  } catch (e) {}
});
send("salt_hits=" + found);
"""


def wait_for_wechat(timeout=120):
    """WeChat may be closed; wait for it to come back."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pids = wechat_pids()
        if pids:
            return pids
        time.sleep(1)
    return []


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: find_k1.py <db_path> [seconds]")
        return 2
    db_path = argv[0]
    seconds = int(argv[1]) if len(argv) > 1 else 30

    if not os.path.exists(db_path):
        print(f"ERR: db not found: {db_path}")
        return 1
    with open(db_path, "rb") as source:
        page1 = source.read(PAGE_SZ)
    salt, iv, ct = page1[:SALT_SZ], page1[PAGE_SZ - RESERVE_SZ:PAGE_SZ - RESERVE_SZ + 16], page1[SALT_SZ:32]
    print(f"db   : {db_path}")
    print(f"salt : {salt.hex()}")

    pids = wechat_pids()
    if not pids:
        print("WeChat not running; waiting for it to start...")
        pids = wait_for_wechat()
    if not pids:
        print("ERR: WeChat is not running (and did not start)")
        return 1
    print(f"pids : {pids}")

    import frida

    device = frida.get_local_device()
    hits = []
    found_key = None

    def sweep(buf):
        nonlocal found_key
        if found_key:
            return
        for off in range(0, max(0, len(buf) - 31)):
            cand = bytes(buf[off:off + 32])
            if not any(cand):
                continue
            try:
                pt = AES.new(cand, AES.MODE_CBC, iv).decrypt(ct)
            except Exception:
                continue
            if header_ok(pt):
                found_key = cand.hex()
                print(f"\n*** K1 FOUND: {cand.hex()} (window offset {off}) ***", flush=True)
                return

    def on_message(message, data):
        if message.get("type") == "send":
            payload = message.get("payload")
            if isinstance(payload, str):
                if payload.startswith("SALT"):
                    print(payload, flush=True)
                return
            if not data:
                return
        if data:
            sweep(data)

    sessions = []
    for pid in pids:
        try:
            session = device.attach(pid)
            script = session.create_script(SCAN_JS % salt.hex())
            script.on("message", on_message)
            script.load()
            sessions.append(session)
            print(f"scanning pid {pid}")
        except Exception as exc:
            print(f"WARN: attach pid {pid} failed: {exc}")

    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline and not found_key:
            time.sleep(0.1)
    finally:
        for session in sessions:
            try:
                session.detach()
            except Exception:
                pass

    if not found_key:
        print("ERR: K1 not found in memory")
        return 1
    print(f"K1={found_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
