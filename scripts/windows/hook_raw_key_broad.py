#!/usr/bin/env python3
"""Resident WeChat 4.x raw-key hook (broad version).

Changes vs the previous attempt, all driven by what the narrow version showed:
  * the narrow hook DID capture genuine HMAC ipad blocks (the 96 trailing 0x36
    bytes are the giveaway) but none of them was the SQLCipher key -> the
    SHA-512 implementation used by PBKDF2 is probably NOT the one in Weixin.dll.
    So we now scan EVERY loaded module for SHA-512 K-tables, not just Weixin.dll.
  * a captured candidate must never be lost, so every ipad is appended to
    candidates.txt immediately, before any (slow) verification.
  * verification runs against every db in the account, because each db has its
    own salt and therefore its own K1.

Usage: python hook_raw_key_broad.py [seconds]
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY_FILE = os.path.join(SKILL_DIR, "key_windows.txt")
CAND_FILE = os.path.join(SKILL_DIR, "candidates.txt")
RVA_CACHE = os.path.join(SKILL_DIR, "sha512_rvas_all.json")

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
var hooked = Object.create(null);
var rvas = %s;

function hookAddress(entry, label) {
  var key = entry.toString();
  if (hooked[key]) return;
  try {
    Interceptor.attach(entry, {onEnter: function () {
      var ip = checkPad(this.context.rdx, 0x36);
      if (ip && !seen[ip]) { seen[ip] = true; send("KEY:" + ip); }
    }});
    hooked[key] = true;
    send("HOOKED " + label + " @ " + entry);
  } catch (e) { send("WARN: hook failed @ " + entry + ": " + e.message); }
}

function installForModule(m) {
  var name = m.name.toLowerCase();
  var cached = rvas[name];
  if (cached) {
    cached.forEach(function (rva) {
      try { hookAddress(m.base.add(rva), name + "+0x" + rva.toString(16)); } catch (e) {}
    });
    if (Object.keys(hooked).length) return;
  }
  var kh = scanRanges(m, "r--", "%s");
  if (!kh.length) return;
  var tables = Object.create(null);
  kh.forEach(function (hit) { tables[hit.address.toString()] = true; });
  var leas = scanRanges(m, "r-x", "48 8d 05 : fb ff c7");
  var found = [];
  leas.forEach(function (hit) {
    try {
      if (hit.address.add(7).compare(hit.range.base.add(hit.range.size)) > 0) return;
      var target = hit.address.add(7).add(hit.address.add(3).readS32());
      if (!tables[target.toString()]) return;
      var entry = findEntry(hit.address, hit.range.base);
      if (entry) found.push(entry);
    } catch (e) {}
  });
  if (!found.length) return;
  var list = [];
  found.forEach(function (entry) {
    hookAddress(entry, name);
    try { list.push(entry.sub(m.base).toString()); } catch (e) {}
  });
  rvas[name] = list;
  send("MODULE " + m.name + " -> " + list.length + " sha512 entries");
}

Process.attachModuleObserver({onAdded: function (m) {
  try { installForModule(m); } catch (e) { send("ERR " + m.name + ": " + e.message); }
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
    return pids


def load_rvas():
    try:
        with open(RVA_CACHE, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def save_rvas(rvas):
    try:
        with open(RVA_CACHE, "w", encoding="utf-8") as handle:
            json.dump(rvas, handle, indent=2)
    except Exception:
        pass


def account_dbs():
    root = os.environ.get("WECHAT_DATA_ROOT") or r"D:\WeChatData\xwechat_files"
    out = []
    for storage in glob.glob(os.path.join(root, "*", "db_storage")):
        out.extend(glob.glob(os.path.join(storage, "**", "*.db"), recursive=True))
    return out


def header_ok(pt):
    return len(pt) >= 8 and pt[0] == 0x10 and pt[1] == 0x00 and pt[4] == 0x50 and pt[5] == 0x40 and pt[7] == 0x20


def verify(candidate, dbs):
    """Try the candidate as device-wide raw key and as a per-db K1."""
    from Crypto.Cipher import AES

    for db in dbs:
        try:
            page1 = open(db, "rb").read(4096)
        except Exception:
            continue
        salt, iv, ct = page1[:16], page1[4016:4032], page1[16:32]
        try:
            derived = hashlib.pbkdf2_hmac("sha512", candidate, salt, 256000, 32)
            if header_ok(AES.new(derived, AES.MODE_CBC, iv).decrypt(ct)):
                return ("raw", db)
        except Exception:
            pass
        try:
            if header_ok(AES.new(candidate, AES.MODE_CBC, iv).decrypt(ct)):
                return ("k1", db)
        except Exception:
            pass
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seconds", nargs="?", type=int, default=900)
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
    print(f"databases under test: {len(dbs)}")

    pids = wechat_pids()
    if not pids:
        print("ERR: WeChat is not running")
        return 1
    print(f"pids: {pids}")

    rvas = load_rvas()
    device = frida.get_local_device()
    found = None
    seen = set()
    pending = []

    def on_message(message, data):
        nonlocal found
        if message.get("type") == "error":
            print("ERR: " + str(message.get("description", "")), flush=True)
            return
        payload = message.get("payload")
        if not isinstance(payload, str):
            return
        if payload.startswith("KEY:"):
            candidate = payload[4:].lower()
            if candidate in seen:
                return
            seen.add(candidate)
            # Persist first -- never lose a captured ipad.
            with open(CAND_FILE, "a", encoding="ascii") as handle:
                handle.write(candidate + "\n")
            pending.append(candidate)
            print(f"captured ipad candidate: {candidate}", flush=True)
            return
        print(payload, flush=True)

    sessions = []
    script_src = JS % (json.dumps(rvas), K_TABLE_SIG)
    attached = set()

    def attach_all():
        """WeChat 4.x respawns its main process; hook every new one as it appears."""
        for pid in wechat_pids():
            if pid in attached:
                continue
            try:
                session = device.attach(pid)
                script = session.create_script(script_src)
                script.on("message", on_message)
                script.load()
                sessions.append(session)
                attached.add(pid)
                print(f"attached pid {pid}", flush=True)
            except Exception as exc:
                print(f"WARN: attach pid {pid} failed: {exc}", flush=True)

    attach_all()

    if not sessions:
        print("ERR: could not attach")
        return 1

    print("\n>>> Hooks live across all modules. In WeChat, trigger db opens:", flush=True)
    print(">>>   scroll back to old history / use 搜索 / open 朋友圈 / 收藏 / 小程序", flush=True)
    print(f">>> window: {args.seconds}s\n", flush=True)

    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline and not found:
            time.sleep(0.2)
            attach_all()
            while pending:
                candidate = pending.pop(0)
                result = verify(bytes.fromhex(candidate), dbs)
                if result:
                    kind, db = result
                    found = candidate
                    print(f"\n*** {kind.upper()} KEY VERIFIED against {db} ***\n{candidate}", flush=True)
                    break
                else:
                    print(f"  rejected: {candidate[:20]}...", flush=True)
    finally:
        save_rvas(rvas)
        for session in sessions:
            try:
                session.detach()
            except Exception:
                pass

    if not found:
        print(f"ERR: no key verified; {len(seen)} candidates captured in {CAND_FILE}")
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
