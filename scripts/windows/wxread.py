#!/usr/bin/env python3
"""Read WeChat 4.x chat history out of the decrypted plaintext store.

The message tables are sharded per conversation (Msg_<md5 of username>) and the
row payload is protobuf-ish, so this module locates the shard for a target,
walks its rows and decodes the fields we care about (sender, timestamp, type,
text content).

Usage:
  python wxread.py list                      -- every chat room with message counts
  python wxread.py messages <username> [--limit N] [--db message_0.db]
  python wxread.py export <username> <out.txt> [--limit N]
  python wxread.py search <keyword> [--limit N]
"""
import argparse
import glob
import os
import re
import sqlite3
import sys
import zlib

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SKILL_DIR)
import accounts  # noqa: E402

DECRYPTED_DIR = os.path.join(SKILL_DIR, "decrypted")

MSG_DBS = ["message_1.db", "message_0.db"]  # oldest first


def resolve_store():
    """Locate the decrypted db_storage directory.

    Works for any account: honours WECHAT_ACCOUNT, otherwise picks the
    account with the most recently active message shard.
    """
    wanted = accounts.selected_account()
    roots = accounts.decrypted_account_roots(DECRYPTED_DIR)
    if wanted:
        for name, storage in roots:
            if wanted.lower() in name.lower():
                return storage
        raise SystemExit(
            f"ERR: no decrypted account matching {wanted!r}; available: "
            + ", ".join(n for n, _ in roots)
        )
    if not roots:
        raise SystemExit(
            f"ERR: no decrypted store under {DECRYPTED_DIR}; "
            "run scripts/windows/decrypt_all.py first"
        )
    best, best_time = roots[0], -1.0
    for name, storage in roots:
        shards = glob.glob(os.path.join(storage, "message", "message_[0-9].db"))
        newest = max((os.path.getmtime(p) for p in shards), default=0.0)
        if newest > best_time:
            best, best_time = storage, newest
    return best


STORE = resolve_store()


def connect(path):
    return sqlite3.connect("file:" + path + "?mode=ro", uri=True)


def load_name_map():
    """username -> human readable name, from session summaries and contact blobs."""
    names = {}

    session = os.path.join(STORE, "session", "session.db")
    if os.path.exists(session):
        con = connect(session)
        try:
            for username, summary, sender_name in con.execute(
                "select username, summary, last_sender_display_name from SessionTable"
            ):
                if username:
                    names[username] = (summary or sender_name or username)
        except sqlite3.Error:
            pass
        finally:
            con.close()

    contact = os.path.join(STORE, "contact", "contact.db")
    if os.path.exists(contact):
        con = connect(contact)
        try:
            for (username,) in con.execute("select username from chat_room"):
                if username and username not in names:
                    names[username] = username
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return names


def display_name(username, names=None):
    """First line of the session summary reads like a group name."""
    if names is None:
        names = load_name_map()
    raw = names.get(username) or username
    first = raw.split("\n")[0].strip()
    return first or username


def shard_table(username):
    """WeChat names the per-chat table Msg_<md5(username)>."""
    import hashlib
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()


def find_shard(username):
    """Return (db_path, table_name) for a conversation, searching all shards."""
    want = shard_table(username)
    for db in MSG_DBS:
        path = os.path.join(STORE, "message", db)
        if not os.path.exists(path):
            continue
        con = connect(path)
        try:
            row = con.execute(
                "select name from sqlite_master where type='table' and name=?", (want,)
            ).fetchone()
            if row:
                return path, row[0]
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return None, None


def parse_local_id(table):
    """Msg_<md5> tables usually carry a local_id -> rowid mapping column."""
    con = connect
    return


def read_messages(username, limit=None, db_filter=None):
    """Yield dicts with local_id, timestamp, sender, type and text."""
    path, table = find_shard(username)
    if not table:
        return None, None
    con = connect(path)
    try:
        cols = [r[1] for r in con.execute("pragma table_info(\"%s\")" % table)]
        rows = con.execute("select * from \"%s\"" % table).fetchall()
    except sqlite3.Error:
        con.close()
        return None, None
    finally:
        pass

    out = []
    for row in rows:
        rec = dict(zip(cols, row))
        content = rec.get("message_content")
        if content is None or content == "":
            content = rec.get("compress_content")
        item = {
            "local_id": rec.get("local_id"),
            "server_id": rec.get("server_id"),
            "timestamp": rec.get("create_time"),
            "sender": extract_wxid(rec.get("source")) or extract_wxid(rec.get("packed_info_data"))
                      or (rec.get("source") if isinstance(rec.get("source"), str) else ""),
            "sender_id": rec.get("real_sender_id"),
            "type": rec.get("local_type"),
            "content": content,
            "packed": rec.get("packed_info_data"),
            "db": os.path.basename(path),
        }
        out.append(item)
    con.close()

    out.sort(key=lambda r: (r["timestamp"] or 0))
    if limit:
        out = out[-limit:]
    return out, table


def try_decode(blob):
    """WCDB compresses some payloads; try zlib and plain utf-8."""
    if blob is None:
        return ""
    if isinstance(blob, str):
        return blob
    if isinstance(blob, (bytes, bytearray)):
        if blob[:2] == b"\x78":
            try:
                return zlib.decompress(bytes(blob)).decode("utf-8", "replace")
            except Exception:
                pass
        try:
            return bytes(blob).decode("utf-8", "replace")
        except Exception:
            return repr(blob[:64])
    return str(blob)


def extract_wxid(blob):
    """`source` and `packed_info_data` are binary blobs; the account id is
    embedded as a plain UTF-8 string inside them."""
    if blob is None:
        return ""
    if isinstance(blob, str):
        return blob
    raw = bytes(blob)
    found = re.findall(rb"wxid_[A-Za-z0-9_\-]{6,32}", raw)
    if found:
        return found[0].decode("utf-8", "replace")
    text = raw.decode("utf-8", "replace")
    found = re.findall(r"wxid_[A-Za-z0-9_\-]{6,32}", text)
    return found[0] if found else ""


def unpack_packed(blob):
    """`packed_info_data` holds a compressed structure; unzip when possible."""
    if not blob:
        return ""
    raw = bytes(blob)
    for attempt in (raw, raw[1:], raw[2:]):
        try:
            data = zlib.decompress(attempt, -15)
            return data.decode("utf-8", "replace")
        except Exception:
            pass
    try:
        return zlib.decompress(raw).decode("utf-8", "replace")
    except Exception:
        pass
    # not zlib -> just pull readable runs out of it
    return " ".join(re.findall(r"[\x20-\x7e一-鿿]{4,}", raw.decode("utf-8", "replace")))


MSG_TYPE_NAMES = {
    1: "文本", 3: "图片", 43: "视频", 47: "表情", 42: "名片", 48: "位置",
    49: "文件", 50: "语音", 10000: "系统提示", 10001: "撤回", 10002: "转账",
    8594229559345: "合并转发", 1094903714: "链接分享", 268435455: "小程序",
}


def split_sender_and_body(content):
    """WeChat prefixes the body with 'wxid_xxx: ' for group chats."""
    text = try_decode(content)
    if not text:
        return "", ""
    match = re.match(r"^((?:wxid_[A-Za-z0-9_\-]{6,32}|[A-Za-z0-9_\-]{4,32})\s*:\s*)(.*)$",
                      text, re.S)
    if match:
        return match.group(1).rstrip(": ").strip(), match.group(2)
    return "", text


def looks_binary(text):
    """True when the payload is real binary noise rather than readable text."""
    if not text:
        return True
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    return printable / len(text) < 0.85


def cmd_list(args):
    names = load_name_map()
    con = connect(os.path.join(STORE, "session", "session.db"))
    rows = con.execute(
        "select username, type, unread_count, summary, last_timestamp, last_msg_sender "
        "from SessionTable order by last_timestamp desc"
    ).fetchall()
    con.close()

    print(f"{'类型':<6}{'未读':<6}{'最后活跃':<22}{'群名/会话标识':<44}会话ID")
    print("-" * 130)
    for username, ctype, unread, summary, ts, sender in rows:
        # WeChat 4.x no longer fills SessionTable.type; group chats are the
        # ones whose username ends with @chatroom.
        if not username:
            continue
        kind = "群聊" if username.endswith("@chatroom") else "好友"
        if kind == "好友" and not args.all:
            continue
        import datetime
        when = ""
        if ts:
            try:
                when = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            except Exception:
                when = str(ts)
        label = display_name(username, names)
        print(f"{kind:<6}{unread or 0:<6}{when:<22}{label[:42]:<44}{username}")
    return 0


def cmd_messages(args):
    names = load_name_map()
    msgs, table = read_messages(args.username, args.limit)
    if msgs is None:
        print(f"ERR: no shard for {args.username}")
        return 1
    import datetime
    print(f"# 会话: {args.username}  ({names.get(args.username,'')})")
    print(f"# 表: {table}   条数: {len(msgs)}\n")
    for m in msgs:
        when = ""
        if m["timestamp"]:
            try:
                when = datetime.datetime.fromtimestamp(m["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                when = str(m["timestamp"])
        sender, body = split_sender_and_body(m["content"])
        if not body.strip() and m["packed"]:
            body = unpack_packed(m["packed"])
        kind = MSG_TYPE_NAMES.get(m["type"], f"type{m['type']}")
        if looks_binary(body):
            body = f"[{kind}消息，二进制内容未解码]"
        body = " ".join(body.split())
        print(f"[{when}] {kind:<8} {sender:<26} :: {body[:300]}")
    return 0


def cmd_export(args):
    names = load_name_map()
    msgs, table = read_messages(args.username, args.limit)
    if msgs is None:
        print(f"ERR: no shard for {args.username}")
        return 1
    import datetime
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(f"# 微信聊天记录导出\n")
        fh.write(f"# 会话: {args.username}\n")
        fh.write(f"# 显示名: {names.get(args.username,'')}\n")
        fh.write(f"# 条数: {len(msgs)}\n\n")
        for m in msgs:
            when = ""
            if m["timestamp"]:
                try:
                    when = datetime.datetime.fromtimestamp(m["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    when = str(m["timestamp"])
            sender, body = split_sender_and_body(m["content"])
            if not body.strip() and m["packed"]:
                body = unpack_packed(m["packed"])
            kind = MSG_TYPE_NAMES.get(m["type"], f"type{m['type']}")
            if looks_binary(body):
                body = f"[{kind}消息，二进制内容未解码]"
            body = " ".join(body.split())
            fh.write(f"[{when}] {kind:<8} {sender:<26} :: {body}\n")
    print(f"exported {len(msgs)} messages -> {args.out}")
    return 0


def cmd_search(args):
    """Search message text across every conversation shard.

    WeChat stores long messages in `compress_content` (plain text) and short
    ones in `message_content`, so both columns have to be checked -- searching
    only one of them silently misses most hits.
    """
    needle = args.keyword.lower()
    hits = []
    for db in MSG_DBS:
        path = os.path.join(STORE, "message", db)
        if not os.path.exists(path):
            continue
        con = connect(path)
        try:
            tables = [r[0] for r in con.execute(
                "select name from sqlite_master where type='table' and name like 'Msg\\_%' escape '\\'"
            )]
            for t in tables:
                cols = [r[1] for r in con.execute("pragma table_info(\"%s\")" % t)]
                colset = {c.lower(): c for c in cols}
                content_col = colset.get("message_content")
                compress_col = colset.get("compress_content")
                pick = [c for c in (content_col, compress_col) if c]
                if not pick:
                    continue
                sql = 'select create_time, source, local_type, %s from "%s"' % (
                    ", ".join(pick), t)
                for row in con.execute(sql):
                    ts, src, ltype = row[0], row[1], row[2]
                    for blob in row[3:]:
                        body = try_decode(blob)
                        if needle in body.lower():
                            sender, text = split_sender_and_body(blob)
                            hits.append((t, ts, sender or extract_wxid(src), ltype, text))
                            break
        except sqlite3.Error:
            pass
        finally:
            con.close()
    hits.sort(key=lambda h: h[1] or 0)
    print(f"found {len(hits)} hits for '{args.keyword}'")
    import datetime
    for t, ts, sender, ltype, body in hits[: args.limit]:
        when = ""
        if ts:
            try:
                when = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            except Exception:
                when = str(ts)
        kind = MSG_TYPE_NAMES.get(ltype, f"type{ltype}")
        clean = " ".join(body.split())
        print(f"[{when}] {kind:<6} {sender:<26} :: {clean[:200]}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list")
    p_list.add_argument("--all", action="store_true", help="include 1:1 chats too")
    p_list.set_defaults(func=cmd_list)

    p_msg = sub.add_parser("messages")
    p_msg.add_argument("username")
    p_msg.add_argument("--limit", type=int, default=50)
    p_msg.set_defaults(func=cmd_messages)

    p_exp = sub.add_parser("export")
    p_exp.add_argument("username")
    p_exp.add_argument("out")
    p_exp.add_argument("--limit", type=int, default=0)
    p_exp.set_defaults(func=cmd_export)

    p_search = sub.add_parser("search")
    p_search.add_argument("keyword")
    p_search.add_argument("--limit", type=int, default=50)
    p_search.set_defaults(func=cmd_search)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
