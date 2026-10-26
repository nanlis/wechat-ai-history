#!/usr/bin/env python3
"""Extract the WeChat 4.x SQLCipher raw key from a RUNNING WeChat (no restart needed).

Two strategies, tried in order:

 1. Hook SQLCipher's `sqlite3_key` / `sqlite3_key_v2`. WeChat calls this whenever it
    opens a database, so hooking a live process is enough -- no need to win a
    startup race. If the symbol is exported, the raw key arrives as a plain
    argument.

 2. Scan the live process memory for a known db salt (page1[0:16] of
    message_0.db). SQLCipher keeps the salt next to the derived key inside the
    codec context, so a window around a salt hit can be AES-verified as K1
    (decrypts this db) and PBKDF2-verified as the raw key (works for every db).

Usage: python extract_key_live.py [--scan] [seconds]
"""
import argparse
import csv
import glob
import hashlib
import io
import os
import re
import subprocess
import sys
import time

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY_FILE = os.path.join(SKILL_DIR, "key_windows.txt")

HOOK_KEY_JS = r"""
function toHex(ptr, len) {
  var b = new Uint8Array(ptr.readByteArray(len));
  var s = "";
  for (var i = 0; i < b.length; i++) s += ("0" + b[i].toString(16)).slice(-2);
  return s;
}
var hooked = 0;
["sqlite3_key", "sqlite3_key_v2"].forEach(function (name) {
  var addr = Module.findExportByName(null, name);
  if (!addr) { send("[miss] export not found: " + name); return; }
  Interceptor.attach(addr, {
    onEnter: function (args) {
      var pKey = name === "sqlite3_key" ? args[1] : args[2];
      var nKey = name === "sqlite3_key" ? args[2].toInt32() : args[3].toInt32();
      if (nKey > 0 && nKey <= 64) send("KEY:" + toHex(pKey, nKey));
    },
  });
  hooked++;
  send("[hook] " + name + " @ " + addr);
});
if (!hooked) send("[miss] sqlite3_key not exported; falling back to salt scan");
send("hooks_ready");
"""

SCAN_SALT_JS = r"""
var saltHex = "%s";
var pat = saltHex.match(/../g).join(' ');
var ranges = Process.enumerateRanges('rw-').filter(function (r) { return r.size < 64 * 1024 * 1024; });
send("scanning " + ranges.length + " rw- ranges for salt...");
var found = 0;
ranges.forEach(function (rg) {
  try {
    Memory.scanSync(rg.base, rg.size, pat).forEach(function (r) {
      found++;
      for (var d = -8192; d <= 8192; d += 4096) {
        try { send({nb: 1}, r.address.add(d).readByteArray(4096)); } catch (e) {}
      }
      send("SALT @ " + r.address);
    });
  } catch (e) {}
});
send("salt_hits=" + found);
"""


def find_message_dbs():
    root = os.environ.get("WECHAT_DATA_ROOT")
    if root:
        root = os.path.expandvars(os.path.expanduser(root))
        dbs = glob.glob(os.path.join(root, "*", "db_storage", "message", "message_0.db"))
        if dbs:
            dbs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return dbs
    return glob.glob(r"C:\Users\*\Documents\xwechat_files\*\db_storage\message\message_0.db")


def wechat_pids():
    """Enumerate WeChat pids via frida (tasklist can be blocked by sandbox policy)."""
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
    except Exception:
        return []
    for row in csv.reader(io.StringIO(out)):
        if len(row) >= 2 and row[0].casefold() == "weixin.exe" and row[1].isdigit():
            pids.append(int(row[1]))
    return pids


def hdr_ok(pt):
    return len(pt) >= 8 and pt[0] == 0x10 and pt[1] == 0x00 and pt[4] == 0x50 and pt[5] == 0x40 and pt[7] == 0x20


def verify_candidate(key, page1):
    """Return 'raw' (device-wide key) or 'k1' (this db only) or None."""
    from Crypto.Cipher import AES

    salt, iv, ct = page1[:16], page1[4016:4032], page1[16:32]
    derived = hashlib.pbkdf2_hmac("sha512", key, salt, 256000, 32)
    if hdr_ok(AES.new(derived, AES.MODE_CBC, iv).decrypt(ct)):
        return "raw"
    if hdr_ok(AES.new(key, AES.MODE_CBC, iv).decrypt(ct)):
        return "k1"
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seconds", nargs="?", type=int, default=90)
    parser.add_argument("--scan", action="store_true", help="also run the salt-memory scan")
    parser.add_argument("--no-hook", action="store_true", help="skip the sqlite3_key hook")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    try:
        import frida
        from Crypto.Cipher import AES
    except Exception as exc:
        print(f"ERR: dependency missing: {exc}")
        return 1

    dbs = find_message_dbs()
    if not dbs:
        print("ERR: message_0.db not found (set WECHAT_DATA_ROOT)")
        return 1
    with open(dbs[0], "rb") as source:
        page1 = source.read(4096)
    if len(page1) != 4096:
        print("ERR: message_0.db first page incomplete")
        return 1

    pids = wechat_pids()
    if not pids:
        print("ERR: WeChat is not running. Start it first, then run this again.")
        return 1
    print(f"WeChat pids: {pids}")

    device = frida.get_local_device()
    found = {}
    seen = set()

    def check(candidate_hex):
        candidate_hex = candidate_hex.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", candidate_hex) or candidate_hex in seen:
            return
        seen.add(candidate_hex)
        kind = verify_candidate(bytes.fromhex(candidate_hex), page1)
        if kind == "raw":
            found["raw"] = candidate_hex
            print(f"\n*** RAW KEY captured and verified ***\n{candidate_hex}", flush=True)
        elif kind == "k1" and "k1" not in found:
            found["k1"] = candidate_hex
            print(f"*** K1 captured (this db only) ***\n{candidate_hex}", flush=True)

    sessions = []

    def on_hook_message(message, data):
        if message.get("type") == "error":
            print("ERR: frida hook script failed: " + message.get("description", ""))
            return
        payload = message.get("payload")
        if isinstance(payload, str):
            if payload.startswith("KEY:"):
                check(payload[4:])
            else:
                print(payload, flush=True)

    def on_scan_message(message, data):
        if message.get("type") == "send":
            payload = message.get("payload")
            if isinstance(payload, str):
                if payload.startswith("SALT"):
                    print(payload, flush=True)
                return
            # payload is a dict marker ({"nb":1}); the real bytes are in `data`
            if not data:
                return
        if data:
            # Sliding window over the dumped neighbourhood of a salt hit.
            for off in range(0, max(0, len(data) - 31)):
                chunk = bytes(data[off:off + 32])
                if not any(chunk):
                    continue
                check(chunk.hex())

    if not args.no_hook:
        for pid in pids:
            try:
                session = device.attach(pid)
                script = session.create_script(HOOK_KEY_JS)
                script.on("message", on_hook_message)
                script.load()
                sessions.append(session)
                print(f"attached pid {pid}")
            except Exception as exc:
                print(f"WARN: attach pid {pid} failed: {exc}")

    if args.scan:
        for pid in pids:
            try:
                session = device.attach(pid)
                script = session.create_script(SCAN_SALT_JS % page1[:16].hex())
                script.on("message", on_scan_message)
                script.load()
                sessions.append(session)
                print(f"salt-scan attached pid {pid}")
            except Exception as exc:
                print(f"WARN: scan attach pid {pid} failed: {exc}")

    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline and "raw" not in found:
            time.sleep(0.2)
    finally:
        for session in sessions:
            try:
                session.detach()
            except Exception:
                pass

    key = found.get("raw") or found.get("k1")
    if not key:
        print("ERR: no key captured")
        return 1
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as target:
        target.write(key + "\n")
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    print(f"DONE. key saved to {KEY_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
