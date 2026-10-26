import os
import sqlite3
import time


def _add_second_shard(decrypted_dir, table, timestamps):
    path = os.path.join(
        decrypted_dir,
        "wxid_test001_a2f4",
        "db_storage",
        "message",
        "message_1.db",
    )
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE Name2Id (user_name TEXT);")
    con.execute("INSERT INTO Name2Id (user_name) VALUES ('wxid_friend001');")
    con.execute(
        f"CREATE TABLE {table} "
        "(local_id INTEGER, server_id INTEGER, create_time INTEGER, local_type INTEGER, "
        "real_sender_id INTEGER, message_content TEXT);"
    )
    for idx, ts in enumerate(timestamps, 1):
        con.execute(
            f"INSERT INTO {table} VALUES (?, ?, ?, 1, 2, ?);",
            (idx, 2000 + idx, ts, f"shard-1-{idx}"),
        )
    con.commit()
    con.close()


def _set_first_shard_times(decrypted_dir, table, timestamps):
    path = os.path.join(
        decrypted_dir,
        "wxid_test001_a2f4",
        "db_storage",
        "message",
        "message_0.db",
    )
    con = sqlite3.connect(path)
    for local_id, ts in enumerate(timestamps, 1):
        con.execute(
            f"UPDATE {table} SET create_time=?, local_type=1, message_content=? "
            "WHERE local_id=?;",
            (ts, f"shard-0-{local_id}", local_id),
        )
    con.commit()
    con.close()


def test_read_merges_shards_before_limit(win_backend):
    import db
    import query
    from conftest import SAMPLE_TABLE

    now = int(time.time())
    _set_first_shard_times(win_backend, SAMPLE_TABLE, [now - 400, now - 200])
    _add_second_shard(win_backend, SAMPLE_TABLE, [now - 300, now - 100])
    db.reset_caches()

    result = query.read_chat("wxid_friend001", limit=3, days=1)
    messages = result["chats"][0]["messages"]
    assert [m["_ts"] for m in messages] == [now - 300, now - 200, now - 100]


def test_recent_limit_is_global_across_shards(win_backend):
    import db
    import query
    from conftest import SAMPLE_TABLE

    now = int(time.time())
    _set_first_shard_times(win_backend, SAMPLE_TABLE, [now - 400, now - 200])
    _add_second_shard(win_backend, SAMPLE_TABLE, [now - 300, now - 100])
    db.reset_caches()

    result = query.recent(days=1, limit=2)
    assert result["total"] == 2
    assert len(result["conversations"]) == 1
    assert [m["_ts"] for m in result["conversations"][0]["messages"]] == [
        now - 100,
        now - 200,
    ]


def test_summary_merges_same_chat_across_shards(win_backend):
    import db
    import query
    from conftest import SAMPLE_TABLE

    now = int(time.time())
    _set_first_shard_times(win_backend, SAMPLE_TABLE, [now - 400, now - 200])
    _add_second_shard(win_backend, SAMPLE_TABLE, [now - 300, now - 100])
    db.reset_caches()

    result = query.summary(days=1)
    assert len(result["conversations"]) == 1
    assert len(result["conversations"][0]["messages"]) == 4


def test_format_message_tolerates_invalid_timestamp():
    import query

    result = query._fmt_msg(
        {"create_time": "bad", "local_type": 1, "real_sender_id": 0, "message_content": "x"}
    )
    assert result["_ts"] == 0
    assert result["time"] == "未知时间"
