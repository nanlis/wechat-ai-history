#!/usr/bin/env python3
"""从微信 factory 隔离归档重建可用的 SQLCipher 库。

微信把损坏的 message 分片移入 <name>.db.factory/<时间戳>/，主库 header(page1)
常已损坏，但绝大多数页完好；该目录内的 .db-wal 通常持有坏页的完好副本。
本脚本：material 取真 salt → 逐页 HMAC 校验找出坏页 → 用 WAL 帧补齐 →
可选校验重建库能被 sqlcipher 正常打开。只读输入，输出到指定路径。

用法:
    python3 rebuild_from_factory.py <factory目录或db路径> [-o out.db] [--verify]
"""
import argparse
import glob
import hashlib
import hmac as _hmac
import os
import struct
import subprocess
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SKILL_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import crypto
import repair_factory as rf
import salvage_btree as sb

PAGE = sb.PAGE
RESERVES = (80, 64, 48)


def hmac_key(salt: bytes, raw: bytes) -> bytes:
    """SQLCipher 页 HMAC key：PBKDF2(enc, salt^0x3a, 2)。"""
    enc = hashlib.pbkdf2_hmac("sha512", raw, salt, 256000, 32)
    return hashlib.pbkdf2_hmac("sha512", enc, bytes(b ^ 0x3A for b in salt), 2, 32)


def frame_ok(payload: bytes, pgno: int, hkey: bytes, reserve: int) -> bool:
    """校验一页/WAL 帧 payload 是否完好。"""
    if len(payload) < PAGE:
        return False
    start = 16 if pgno == 1 else 0
    ctend = PAGE - reserve
    if ctend <= start or ctend + 80 > PAGE:
        return False
    mac = _hmac.new(hkey, payload[start:ctend + 16] + struct.pack("<I", pgno), hashlib.sha512).digest()
    return mac == payload[ctend + 16:ctend + 80]


def pick_reserve(data: bytes, salt: bytes, raw: bytes) -> int:
    """用页面整体命中率挑 SQLCipher reserve。"""
    hkey = hmac_key(salt, raw)
    best, best_score = 80, -1
    npg = min(len(data) // PAGE, 200)
    for r in RESERVES:
        ok = sum(frame_ok(data[(pn - 1) * PAGE:pn * PAGE], pn, hkey, r)
                 for pn in range(2, npg + 1))
        if ok > best_score:
            best, best_score = r, ok
    return best


def wal_frames(wal: bytes, reserve: int, hkey: bytes):
    """返回 {pgno: payload}，只含 HMAC 通过的帧；后者覆盖前者。

    帧 payload 是页的完整磁盘镜像（page1 含 16 字节 salt 前缀），HMAC 用库 salt 派生。
    """
    if len(wal) < 32 or wal[:4] != b"\x37\x7f\x06\x82":
        return {}
    if struct.unpack(">I", wal[8:12])[0] != PAGE:
        return {}
    frames = {}
    off = 32
    while off + 24 + PAGE <= len(wal):
        pgno = struct.unpack(">I", wal[off:off + 4])[0]
        payload = wal[off + 24:off + 24 + PAGE]
        if pgno and frame_ok(payload, pgno, hkey, reserve):
            frames[pgno] = payload
        off += 24 + PAGE
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="factory 目录，或该目录下的 .db 路径")
    ap.add_argument("-o", "--output")
    ap.add_argument("--verify", action="store_true", help="重建后跑 sqlcipher integrity_check")
    a = ap.parse_args()

    src = os.path.abspath(a.src)
    if os.path.isdir(src):
        cands = [p for p in glob.glob(os.path.join(src, "*.db"))]
        if not cands:
            sys.exit("目录内无 .db")
        dbp = cands[0]
    else:
        dbp = src
    d = os.path.dirname(dbp)
    mats = sorted(glob.glob(os.path.join(d, "*-first.material"))) \
        or sorted(glob.glob(os.path.join(d, "*.material")))
    wals = sorted(glob.glob(os.path.join(d, "*.db-wal")))
    if not mats:
        sys.exit("找不到 .material（真 salt 来源）")
    salt = open(mats[0], "rb").read(16)
    raw = rf.load_raw_key()
    data = bytearray(open(dbp, "rb").read())
    npg = len(data) // PAGE
    reserve = pick_reserve(data, salt, raw)
    hkey = hmac_key(salt, raw)
    print(f"库: {dbp}\n真 salt: {salt.hex()}  reserve: {reserve}  页数: {npg}", file=sys.stderr)

    bad = []
    for pn in range(1, npg + 1):
        if not frame_ok(bytes(data[(pn - 1) * PAGE:pn * PAGE]), pn, hkey, reserve):
            bad.append(pn)
    print(f"坏页 {len(bad)} 个: {bad[:40]}", file=sys.stderr)

    applied = []
    for w in wals:
        fr = wal_frames(open(w, "rb").read(), reserve, hkey)
        print(f"WAL {os.path.basename(w)}: 可用帧 {len(fr)}", file=sys.stderr)
        for pgno, payload in fr.items():
            if pgno <= npg:
                data[(pgno - 1) * PAGE:pgno * PAGE] = payload
                applied.append(pgno)
    still = [pn for pn in bad
             if not frame_ok(bytes(data[(pn - 1) * PAGE:pn * PAGE]), pn, hkey, reserve)]
    print(f"WAL 补回 {len(set(applied))} 页；仍损坏 {len(still)} 页: {still[:40]}", file=sys.stderr)

    out = os.path.expanduser(a.output or dbp + ".rebuilt")
    with open(out, "wb") as f:
        f.write(bytes(data))
    print(f"输出: {out}", file=sys.stderr)

    if a.verify:
        der = crypto.derive_key(crypto.load_key(), out)
        cmd = (f"PRAGMA key = \"x'{der}'\";\nPRAGMA kdf_iter = 1;\n"
               "PRAGMA cipher_compatibility = 4;\nPRAGMA cipher_page_size = 4096;\n"
               "PRAGMA integrity_check;\nSELECT count(*) FROM sqlite_master;\n")
        r = subprocess.run([config.SQLCIPHER_PATH, out], input=cmd.encode(),
                           capture_output=True, timeout=1800)
        o = r.stdout.decode("utf-8", "replace").strip().replace("\n", " | ")
        e = r.stderr.decode("utf-8", "replace").strip()
        print(f"校验: {o}{('  STDERR: ' + e[:200]) if e else ''}", file=sys.stderr)


if __name__ == "__main__":
    main()
