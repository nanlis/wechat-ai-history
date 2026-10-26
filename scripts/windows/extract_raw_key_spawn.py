#!/usr/bin/env python3
"""Spawn-gating WeChat 4.x raw-key extractor for Windows.

Why this instead of the attach-based extractor: WeChat derives the SQLCipher db key
the moment it opens its databases, which happens within a second of process start.
Attaching after the process is already running loses that window. Spawn gating lets
frida intercept the process creation, inject the hook first, then resume the process,
so the SHA-512 HMAC ipad block is caught on the very first PBKDF2 call.

The raw key is protected in memory (AES-NI round keys / wiped after use / secure
heap), but the PBKDF2 HMAC ipad block is the plaintext `key XOR 0x36` at construction
time -- bypassing every protection.

Usage: python extract_raw_key_spawn.py [seconds]  -> writes key_windows.txt
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

# Hook payload: locate the SHA-512 K-tables inside Weixin.dll, resolve the
# sha512_block entry points through RIP-relative LEA xrefs, and read the
# plaintext key out of the HMAC ipad block (key XOR 0x36) when PBKDF2 runs.
JS = r"""
function scanRanges(m, protection, pattern) {
  var hits = [];
  m.enumerateRanges(protection).forEach(function(range) {
    try {
      Memory.scanSync(range.base, range.size, pattern).forEach(function(hit) {
        hits.push({address: hit.address, range: range});
      });
    } catch (e) {}
  });
  return hits;
}

function findEntry(lea, rangeBase) {
  for (var b = 0; b < 8192; b++) {
    var a = lea.sub(b);
    if (a.compare(rangeBase) <= 0) break;
    try {
      if (a.sub(1).readU8() === 0xCC) return a;
    } catch (e) { break; }
  }
  return null;
}

function checkPad(p, pad) {
  try {
    var b = new Uint8Array(p.readByteArray(128));
    for (var j = 32; j < 128; j++) if (b[j] !== pad) return null;
    var s = "";
    for (var k = 0; k < 32; k++) s += ('0' + (b[k] ^ pad).toString(16)).slice(-2);
    return s;
  } catch (e) { return null; }
}

var seen = Object.create(null);
var installed = false;

function installHooks(m) {
  if (installed) return;
  var kh = scanRanges(m, "r--", "22 ae 28 d7 98 2f 8a 42");
  if (!kh.length) { send("WARN: SHA512 K-table not found yet"); return; }
  var tables = Object.create(null);
  kh.forEach(function(hit) { tables[hit.address.toString()] = true; });
  send("SHA-512 K-tables found: " + Object.keys(tables).length);

  var leas = scanRanges(m, "r-x", "48 8d 05 : fb ff c7");
  var entries = Object.create(null);
  leas.forEach(function(hit) {
    try {
      if (hit.address.add(7).compare(hit.range.base.add(hit.range.size)) > 0) return;
      var target = hit.address.add(7).add(hit.address.add(3).readS32());
      if (!tables[target.toString()]) return;
      var entry = findEntry(hit.address, hit.range.base);
      if (entry) entries[entry.toString()] = entry;
    } catch (e) {}
  });
  send("SHA-512 entries resolved: " + Object.keys(entries).length);
  if (!Object.keys(entries).length) { send("WARN: SHA512 entries not resolved yet"); return; }

  var attached = 0;
  Object.keys(entries).forEach(function(address) {
    var entry = entries[address];
    try {
      Interceptor.attach(entry, {onEnter: function() {
        var ip = checkPad(this.context.rdx, 0x36);
        if (ip && !seen[ip]) { seen[ip] = true; send("KEY:" + ip); }
      }});
      attached++;
      send("sha512 entry @ " + entry + " (rva " + entry.sub(m.base) + ")");
    } catch (e) { send("WARN: hook failed @ " + entry + ": " + e.message); }
  });
  if (attached) {
    installed = true;
    send("hooked " + attached + " SHA-512 entries; waiting for startup PBKDF2 (raw key)...");
  }
}

send("waiting for Weixin.dll...");
Process.attachModuleObserver({onAdded: function(m) {
  if (m.name.toLowerCase() !== "weixin.dll") return;
  try { installHooks(m); } catch (e) { send("ERR: " + e.message); }
}});
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


def verify_candidate(key, page1):
    from Crypto.Cipher import AES

    salt, iv, ct = page1[:16], page1[4016:4032], page1[16:32]

    def hdr_ok(pt):
        return len(pt) >= 8 and pt[0] == 0x10 and pt[1] == 0x00 and pt[4] == 0x50 and pt[5] == 0x40 and pt[7] == 0x20

    derived = hashlib.pbkdf2_hmac("sha512", key, salt, 256000, 32)
    if hdr_ok(AES.new(derived, AES.MODE_CBC, iv).decrypt(ct)):
        return "raw"
    if hdr_ok(AES.new(key, AES.MODE_CBC, iv).decrypt(ct)):
        return "k1"
    return None


class Capture:
    def __init__(self, page1):
        self.page1 = page1
        self.seen = set()
        self.raw_key = None
        self.error = False

    def on_message(self, message, data):
        if message.get("type") == "error":
            self.error = True
            print("ERR: frida script failed: " + message.get("description", "unknown"))
            return
        if message.get("type") != "send":
            return
        payload = message.get("payload", "")
        if not isinstance(payload, str):
            return
        if not payload.startswith("KEY:"):
            if payload.startswith("ERR:"):
                self.error = True
            print(payload, flush=True)
            return
        candidate = payload[4:].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", candidate) or candidate in self.seen:
            return
        self.seen.add(candidate)
        kind = verify_candidate(bytes.fromhex(candidate), self.page1)
        if kind == "raw":
            self.raw_key = candidate
            print("\n*** RAW KEY captured and verified ***", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seconds", nargs="?", type=int, default=120)
    args = parser.parse_args(argv)
    if args.seconds <= 0:
        parser.error("seconds must be positive")
    if os.name != "nt":
        print("ERR: this extractor requires Windows")
        return 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    try:
        import frida

        dbs = find_message_dbs()
        if not dbs:
            raise FileNotFoundError("message_0.db not found (set WECHAT_DATA_ROOT)")
        with open(dbs[0], "rb") as source:
            page1 = source.read(4096)
        if len(page1) != 4096:
            raise ValueError("message_0.db has an incomplete first page")

        device = frida.get_local_device()
        capture = Capture(page1)

        # Kill WeChat, then gate every Weixin.exe spawn so frida injects the hook
        # before the process can touch its databases.
        subprocess.run(["taskkill", "/F", "/IM", "Weixin.exe"], capture_output=True, timeout=15)
        time.sleep(2)

        def on_spawn(spawn):
            ident = spawn.identifier
            if not ident.lower().endswith("weixin.exe"):
                return
            print(f"[gated spawn] {ident} pid={spawn.pid}", flush=True)
            try:
                session = device.attach(spawn.pid)
                script = session.create_script(JS)
                script.on("message", capture.on_message)
                script.load()
                device.resume(spawn.pid)
                print(f"[gated spawn] resumed pid {spawn.pid}", flush=True)
            except Exception as exc:
                print(f"WARN: spawn {spawn.pid} inject failed: {exc}", flush=True)
                try:
                    device.resume(spawn.pid)
                except Exception:
                    pass

        device.on("spawn-added", on_spawn)
        device.enable_spawn_gating()

        print(">>> Now RESTART WeChat from the desktop (double-click). <<<", flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline and not capture.raw_key and not capture.error:
            time.sleep(0.1)

        try:
            device.disable_spawn_gating()
        except Exception:
            pass

        if not capture.raw_key:
            print("ERR: raw key not captured; check spawn gating and desktop restart timing")
            return 1

        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as target:
            target.write(capture.raw_key + "\n")
        try:
            os.chmod(KEY_FILE, 0o600)
        except OSError:
            pass
    except Exception as exc:
        print(f"ERR: {exc}")
        return 1
    print(f"\nDONE. verified key saved to {KEY_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
