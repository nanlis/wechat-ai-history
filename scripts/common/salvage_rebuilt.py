#!/usr/bin/env python3
"""从修复后的 message 库导出留底：明文 Sqlite 备份 + 分会话可读文本。

修复库由 repair_factory/salvage_btree 的页级抢救产物重建（page1 及坏页从归档 WAL 补回）。
本脚本只读该修复库，不解密、不写微信目录。

用法:
    python3 salvage_rebuilt.py <rebuilt.db> [-o 输出目录] [--transcribe] [--no-plaintext]
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SKILL_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import db
import crypto
import contacts
import message
import export_chat as ec

MSG_TABLE_RE = re.compile(r"^Msg_[0-9a-f]{32}$")


def make_plaintext(src: str, dst: str) -> None:
    key = crypto.load_key()
    der = crypto.derive_key(key, src)
    cmd = (
        f"PRAGMA key = \"x'{der}'\";\n"
        "PRAGMA kdf_iter = 1;\n"
        "PRAGMA cipher_compatibility = 4;\n"
        "PRAGMA cipher_page_size = 4096;\n"
        f"ATTACH DATABASE '{dst}' AS plain KEY '';\n"
        "SELECT sqlcipher_export('plain');\n"
        "DETACH DATABASE plain;\n"
    )
    r = subprocess.run(
        [config.SQLCIPHER_PATH, src],
        input=cmd.encode(), capture_output=True, timeout=1800,
    )
    if r.returncode != 0 or not os.path.exists(dst):
        sys.exit(f"明文导出失败: {r.stderr.decode('utf-8', 'replace')[:400]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("-o", "--output")
    ap.add_argument("--transcribe", action="store_true")
    ap.add_argument("--no-transcribe", action="store_true")
    ap.add_argument("--no-plaintext", action="store_true")
    a = ap.parse_args()

    src = os.path.abspath(a.db)
    tables = [t.strip() for t in db.query_raw(src, "SELECT name FROM sqlite_master WHERE type='table';")]
    msg_tables = sorted(t for t in tables if MSG_TABLE_RE.match(t))
    print(f"修复库: {src}\n会话表: {len(msg_tables)}", file=sys.stderr)

    name2id = dict(db.get_name2id())
    for r in db.query(src, "SELECT user_name FROM Name2Id;"):
        un = r.get("user_name", "")
        if un:
            name2id[f"Msg_{hashlib.md5(un.encode()).hexdigest()}"] = un

    since = datetime(2010, 1, 1)
    until = datetime.now() + timedelta(days=1)
    my_name = contacts.resolve_nickname(db.get_my_wxid())
    model_ok = ec.model_cached()

    index = []
    total = 0
    all_rows = []
    for table in msg_tables:
        wxid = name2id.get(table, table)
        rows = ec.fetch(table, [src], since, until)
        if not rows:
            continue
        seen = set()
        ded = []
        for r in rows:
            k = r.get("local_id")
            if k and k not in seen:
                seen.add(k)
                ded.append(r)
        ded.sort(key=lambda r: int(r.get("create_time", "0") or "0"))
        all_rows.append((table, wxid, ded))
        total += len(ded)

    voice_map = {}
    voice_ids = []
    for _, _, rows in all_rows:
        for r in rows:
            if message.normalize_type(r.get("local_type", "")) == "34":
                sid = str(r.get("server_id", ""))
                if sid and sid != "0":
                    voice_ids.append({"server_id": sid})
    if voice_ids:
        print(f"语音消息 {len(voice_ids)} 条", file=sys.stderr)
        if not a.no_transcribe and (a.transcribe or model_ok):
            print("转写中（复用 media_0.db + whisper）…", file=sys.stderr)
            voice_map = ec.transcribe_voices(voice_ids)

    if all_rows:
        stamps = [int(r.get("create_time", "0") or "0")
                  for _, _, rows in all_rows for r in rows]
        first, last = min(stamps), max(stamps)
    else:
        first = last = int(datetime.now().timestamp())
    default_out = (f"~/Desktop/wechat-salvage_"
                   f"{datetime.fromtimestamp(first).strftime('%Y%m%d')}-"
                   f"{datetime.fromtimestamp(last).strftime('%Y%m%d')}")
    outdir = os.path.expanduser(a.output or default_out)
    os.makedirs(outdir, exist_ok=True)

    for table, wxid, rows in all_rows:
        is_group = "@chatroom" in wxid or "@openim" in wxid
        display = contacts.resolve_nickname(wxid) if not is_group else wxid
        safe = re.sub(r"[^\w@.\-]", "_", wxid)[:80]
        path = os.path.join(outdir, f"{safe}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"=== {display} ({wxid}) ===\n")
            f.write(f"来源: 抢救库 {os.path.basename(src)}  |  共 {len(rows)} 条\n\n")
            for r in rows:
                peer = contacts.resolve_nickname(wxid) if not is_group else "对方"
                f.write(ec.format_row(r, voice_map, is_group, my_name, peer) + "\n")
        index.append((len(rows), display, wxid, os.path.basename(path)))

    index.sort(reverse=True)
    with open(os.path.join(outdir, "_索引.txt"), "w", encoding="utf-8") as f:
        f.write(f"微信抢救留底  共 {len(index)} 个会话 / {total:,} 条消息\n")
        f.write(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"修复库: {src}\n\n")
        for c, display, wxid, fn in index:
            f.write(f"{c:>7,}  {display}  ({wxid})  -> {fn}\n")

    if not a.no_plaintext:
        plain = os.path.join(outdir, "_明文全库.db")
        print("导出明文 Sqlite 备份（供随时查询）…", file=sys.stderr)
        make_plaintext(src, plain)

    print(f"\n完成: {outdir}\n会话 {len(index)} 个 | 消息 {total:,} 条", file=sys.stderr)


if __name__ == "__main__":
    main()
