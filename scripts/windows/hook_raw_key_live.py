#!/usr/bin/env python3
"""Resident WeChat 4.x raw-key hook: attach to a RUNNING WeChat, hook SHA-512, and
wait for the moment SQLCipher derives a database key.

Why hooking (and not memory scanning):
  The raw key lives in protected memory (AES-NI round keys / wiped after use /
  secure heap), so a brute-force scan of process memory cannot find it. But when
  SQLCipher derives a db key it runs PBKDF2-HMAC-SHA512, and at that instant the
  HMAC ipad block is the plaintext `key XOR 0x36` -- one byte-pattern away from
  the key itself. Hooking the SHA-512 compression entry catches it.

Two things make this practical on WeChat 4.1.15.56:
  * the SHA-512 entry RVAs are cached in a state file, so a later run can hook in
    milliseconds instead of re-scanning the 200MB Weixin.dll (~20s) -- which
    used to lose the race against WeChat's own startup derivation;
  * the hook stays resident, so ANY later key derivation is caught: scrolling
    back through old history opens message_1.db, search opens message_fts.db,
    and so on. No restart required.

Usage: python hook_raw_key_live.py [seconds]
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY_FILE = os.path.join(SKILL_DIR, "key_windows.txt")
RVA_CACHE = os.path.join(SKILL_DIR, "sha512_rvas.json")

# SHA-512 K-table signature (first two round constants).
K_TABLE_SIG = "22 ae 28 d7 98 2f 8a 42"

JS = r"""
function scanRanges(m, protection, pattern) {
  var hits = [];
  m.enumerateRanges(protection).forEach(function (range) {
    try {
      Memory.scanSync(range.base, range.size, pattern).forEach(function (hit) {
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
var hookedCount = 0;
var rvas = %s;

function hookAddress(entry, label) {
  try {
    Interceptor.attach(entry, {onEnter: function () {
      var ip = checkPad(this.context.rdx, 0x36);
      if (ip && !seen[ip]) { seen[ip] = true; send("KEY:" + ip); }
    }});
    hookedCount++;
    send("hooked " + label + " @ " + entry);
  } catch (e) { send("WARN: hook failed @ " + entry + ": " + e.message); }
}

var installed = false;

function installHooks(m) {
  if (installed) return;
  // Fast path: cached RVAs from a previous run.
  if (rvas.length) {
    rvas.forEach(function (rva) {
      try {
        var addr = m.base.add(rva);
        hookAddress(addr, "cached rva 0x" + rva.toString(16));
      } catch (e) {}
    });
    if (hookedCount) { installed = true; send("hooks_ready via cached RVAs"); return; }
  }
  // Slow path: resolve the entries by scanning for the K-table and its xrefs.
  var kh = scanRanges(m, "r--", "%s");
  if (!kh.length) { send("WARN: SHA512 K-table not found"); return; }
  var tables = Object.create(null);
  kh.forEach(function (hit) { tables[hit.address.toString()] = true; });
  send("SHA-512 K-tables found: " + Object.keys(tables).length);

  var leas = scanRanges(m, "r-x", "48 8d 05 : fb ff c7");
  var entries = Object.create(null);
  leas.forEach(function (hit) {
    try {
      if (hit.address.add(7).compare(hit.range.base.add(hit.range.size)) > 0) return;
      var target = hit.address.add(7).add(hit.address.add(3).readS32());
      if (!tables[target.toString()]) return;
      var entry = findEntry(hit.address, hit.range.base);
      if (entry) entries[entry.toString()] = entry;
    } catch (e) {}
  });
  Object.keys(entries).forEach(function (address) {
    var entry = entries[address];
    hookAddress(entry, "resolved @ " + entry);
    try {
      var rva = entry.sub(m.base).toString();
      if (rvas.indexOf(rva) < 0) { rvas.push(rva); }
    } catch (e) {}
  });
  if (hookedCount) { installed = true; send("hooks_ready"); }
  else send("WARN: no SHA512 entry hooked");
}

send("waiting for Weixin.dll...");
Process.attachModuleObserver({onAdded: function (m) {
  if (m.name.toLowerCase() !== "weixin.dll") return;
  try { installHooks(m); } catch (e) { send("ERR: " + e.message); }
}});
"""


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


def load_rvas():
    try:
        with open(RVA_CACHE, encoding="utf-8") as handle:
            return [int(v, 16) if isinstance(v, str) else int(v) for v in json.load(handle)]
    except Exception:
        return []


def save_rvas(rvas):
    try:
        with open(RVA_CACHE, "w", encoding="utf-8") as handle:
            json.dump([hex(v) for v in rvas], handle, indent=2)
    except Exception:
        pass


def find_message_db():
    root = os.environ.get("WECHAT_DATA_ROOT") or r"D:\WeChatData\xwechat_files"
    import glob
    dbs = glob.glob(os.path.join(root, "*", "db_storage", "message", "message_0.db"))
    if not dbs:
        return None
    dbs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return dbs[0]


def header_ok(pt):
    return len(pt) >= 8 and pt[0] == 0x10 and pt[1] == 0x00 and pt[4] == 0x50 and pt[5] == 0x40 and pt[7] == 0x20


def verify_raw(candidate, page1):
    """True if candidate is the device-wide raw key (PBKDF2 -> K1 -> header)."""
    from Crypto.Cipher import AES

    salt = page1[:16]
    iv = page1[4016:4032]
    ct = page1[16:32]
    try:
        derived = hashlib.pbkdf2_hmac("sha512", candidate, salt, 256000, 32)
        pt = AES.new(derived, AES.MODE_CBC, iv).decrypt(ct)
        return header_ok(pt)
    except Exception:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seconds", nargs="?", type=int, default=600)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    try:
        import frida
    except Exception as exc:
        print(f"ERR: frida missing: {exc}")
        return 1

    db = find_message_db()
    if not db:
        print("ERR: message_0.db not found (set WECHAT_DATA_ROOT)")
        return 1
    page1 = open(db, "rb").read(4096)
    print(f"target db: {db}")

    pids = wechat_pids()
    if not pids:
        print("ERR: WeChat is not running")
        return 1
    print(f"pids: {pids}")

    rvas = load_rvas()
    if rvas:
        print(f"cached SHA-512 RVAs: {[hex(r) for r in rvas]}")
    device = frida.get_local_device()
    found = None
    seen = set()

    def on_message(message, data):
        nonlocal found
        if message.get("type") == "error":
            print("ERR: " + message.get("description", ""), flush=True)
            return
        payload = message.get("payload")
        if not isinstance(payload, str):
            return
        if payload.startswith("KEY:"):
            candidate = payload[4:].lower()
            if candidate in seen:
                return
            seen.add(candidate)
            if verify_raw(bytes.fromhex(candidate), page1):
                found = candidate
                print(f"\n*** RAW KEY captured and verified ***\n{candidate}", flush=True)
            else:
                print(f"candidate rejected: {candidate[:16]}...", flush=True)
            return
        print(payload, flush=True)

    sessions = []
    script_src = JS % (json.dumps([hex(r) for r in rvas]), K_TABLE_SIG)
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
        print("ERR: could not attach to any WeChat process")
        return 1

    print("\n>>> Hooks are live. Now, in WeChat, do things that open databases:", flush=True)
    print(">>>   1) scroll back through old chat history (opens message_1.db)", flush=True)
    print(">>>   2) use 搜索 in WeChat (opens message_fts.db)", flush=True)
    print(">>>   3) open 朋友圈 / 收藏 / 小程序", flush=True)
    print(f">>> Waiting up to {args.seconds}s ...\n", flush=True)

    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline and not found:
            time.sleep(0.2)
    finally:
        save_rvas(rvas)
        for session in sessions:
            try:
                session.detach()
            except Exception:
                pass

    if not found:
        print("ERR: raw key not captured")
        return 1
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as target:
        target.write(found + "\n")
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    print(f"DONE. key saved to {KEY_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
