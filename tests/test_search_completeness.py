import os
import sqlite3
import time


def _local_type(app_type):
    return (app_type << 32) | 49


def _message_db(win_backend):
    return os.path.join(
        win_backend,
        "wxid_test001_a2f4",
        "db_storage",
        "message",
        "message_0.db",
    )


def test_search_finds_share_card_beyond_the_old_200_row_window(win_backend):
    import db
    import query
    from conftest import SAMPLE_TABLE

    now = int(time.time())
    music = "<msg><appmsg><type>3</type><title>普通歌曲</title></appmsg></msg>"
    finder = (
        "<msg><appmsg><type>51</type><finderFeed>"
        "<desc>目标内容</desc></finderFeed></appmsg></msg>"
    )
    con = sqlite3.connect(_message_db(win_backend))
    con.executemany(
        f"INSERT INTO {SAMPLE_TABLE} "
        "(local_id, server_id, create_time, local_type, real_sender_id, message_content) "
        "VALUES (?, ?, ?, ?, 2, ?);",
        [
            (index + 10, index + 10, now - index, _local_type(3), music)
            for index in range(201)
        ],
    )
    con.execute(
        f"INSERT INTO {SAMPLE_TABLE} "
        "(local_id, server_id, create_time, local_type, real_sender_id, message_content) "
        "VALUES (999, 999, ?, ?, 2, ?);",
        (now - 201, _local_type(51), finder),
    )
    con.commit()
    con.close()
    db.reset_caches()

    result = query.search("视频号", days=1, limit=10)

    assert result["count"] == 1
    assert result["messages"][0]["type"] == "视频号"
    assert "目标内容" in result["messages"][0]["content"]


def test_search_decodes_compressed_blob_before_matching(monkeypatch, win_backend):
    import db
    import message
    import query
    from conftest import SAMPLE_TABLE

    now = int(time.time())
    blob = b"\x28\xb5\x2f\xfd" + b"\x00" * 24
    decoded = "wxid_friend001:\n压缩正文中的唯一关键词"
    monkeypatch.setattr(message, "_decompress_zstd", lambda _data: decoded.encode())

    con = sqlite3.connect(_message_db(win_backend))
    con.execute(
        f"INSERT INTO {SAMPLE_TABLE} "
        "(local_id, server_id, create_time, local_type, real_sender_id, message_content) "
        "VALUES (777, 777, ?, 1, 2, ?);",
        (now, blob),
    )
    con.commit()
    con.close()
    db.reset_caches()

    result = query.search("唯一关键词", days=1, limit=10)

    assert result["count"] == 1
    assert result["messages"][0]["content"] == "压缩正文中的唯一关键词"


def test_query_and_export_share_the_canonical_message_decoder(monkeypatch):
    import export_chat
    import message
    import query

    blob = b"\x28\xb5\x2f\xfd" + b"\x00" * 8
    decoded = "wxid_friend001:\n统一解码结果"
    monkeypatch.setattr(message, "_decompress_zstd", lambda _data: decoded.encode())

    assert query._decode_content(blob) == "统一解码结果"
    assert export_chat._decode_msg(blob.hex()) == "统一解码结果"
