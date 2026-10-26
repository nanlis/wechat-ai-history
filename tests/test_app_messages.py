import os
import sqlite3
import time


def _local_type(app_type):
    return (app_type << 32) | 49


def _xml(app_type, body):
    return f"<msg><appmsg><type>{app_type}</type>{body}</appmsg></msg>"


def test_music_share_parses_cdata_metadata_and_prefers_xml_type():
    import appmsg

    content = _xml(
        3,
        "<title><![CDATA[夜曲 & Live]]></title>"
        "<des>周杰伦 &amp; 乐队</des>"
        "<sourcedisplayname>QQ音乐</sourcedisplayname>"
        "<dataurl>https://example.com/song?a=1&amp;b=2</dataurl>"
        "<songalbumurl>https://example.com/cover.jpg</songalbumurl>",
    )
    parsed = appmsg.parse_app_message(content, _local_type(4))

    assert parsed["app_type"] == 3
    assert parsed["kind"] == "music"
    assert parsed["label"] == "音乐分享"
    assert parsed["title"] == "夜曲 & Live"
    assert parsed["artist"] == "周杰伦 & 乐队"
    assert parsed["source"] == "QQ音乐"
    assert parsed["url"] == "https://example.com/song?a=1&b=2"
    assert "夜曲 & Live" in parsed["summary"]
    assert "周杰伦 & 乐队" in parsed["summary"]


def test_video_link_and_malformed_xml_keep_readable_fields():
    import appmsg

    valid = _xml(
        4,
        "<title><![CDATA[演示视频]]></title>"
        "<des><![CDATA[两分钟介绍]]></des>"
        "<url><![CDATA[https://example.com/video]]></url>",
    )
    parsed = appmsg.parse_app_message(valid, _local_type(4))
    assert parsed["kind"] == "video_link"
    assert parsed["label"] == "视频分享"
    assert parsed["title"] == "演示视频"
    assert parsed["description"] == "两分钟介绍"

    malformed = (
        "<msg><appmsg><type>4</type><title><![CDATA[仍可读取]]></title>"
        "<url>https://example.com/video?x=1&y=2</url></appmsg></msg>"
    )
    fallback = appmsg.parse_app_message(malformed, _local_type(4))
    assert fallback["title"] == "仍可读取"
    assert fallback["url"] == "https://example.com/video?x=1&y=2"


def test_finder_share_uses_real_description_instead_of_placeholder():
    import appmsg

    content = _xml(
        51,
        "<title>当前版本不支持展示该内容，请升级至最新版本。</title>"
        "<finderFeed>"
        "<nickname><![CDATA[城市漫游]]></nickname>"
        "<username>finder_user</username>"
        "<desc><![CDATA[夜游外滩]]></desc>"
        "<objectId>12345</objectId>"
        "<objectNonceId>nonce-1</objectNonceId>"
        "<mediaList><media><url>https://example.com/finder.mp4</url>"
        "<coverUrl>https://example.com/finder.jpg</coverUrl></media></mediaList>"
        "</finderFeed>",
    )
    parsed = appmsg.parse_app_message(content, _local_type(51))

    assert parsed["kind"] == "finder"
    assert parsed["label"] == "视频号"
    assert parsed["title"] == "夜游外滩"
    assert parsed["description"] == "夜游外滩"
    assert parsed["source"] == "城市漫游"
    assert parsed["finder_username"] == "finder_user"
    assert parsed["object_id"] == "12345"
    assert parsed["object_nonce_id"] == "nonce-1"
    assert parsed["url"] == "https://example.com/finder.mp4"
    assert parsed["thumb_url"] == "https://example.com/finder.jpg"


def test_finder_live_mini_program_file_and_unknown_cards_preserve_metadata():
    import appmsg

    live = appmsg.parse_app_message(
        _xml(
            63,
            "<finderLive><nickname>直播间</nickname><desc>新品发布</desc>"
            "<finderUsername>host</finderUsername><liveId>888</liveId>"
            "<coverUrl>https://example.com/live.jpg</coverUrl></finderLive>",
        ),
        _local_type(63),
    )
    assert (live["kind"], live["source"], live["live_id"]) == (
        "finder_live",
        "直播间",
        "888",
    )

    mini = appmsg.parse_app_message(
        _xml(
            36,
            "<title>点餐</title><sourcedisplayname>示例商店</sourcedisplayname>"
            "<weappinfo><username>gh_demo@app</username><appid>wx123</appid>"
            "<pagepath>pages/menu</pagepath>"
            "<weappiconurl>https://example.com/icon.png</weappiconurl></weappinfo>",
        ),
        _local_type(36),
    )
    assert mini["kind"] == "mini_program"
    assert mini["mini_program_username"] == "gh_demo@app"
    assert mini["page_path"] == "pages/menu"

    file_card = appmsg.parse_app_message(
        _xml(
            6,
            "<title>报价单.pdf</title><fileext>pdf</fileext>"
            "<totallen>1048576</totallen><filemd5>abc</filemd5>",
        ),
        _local_type(6),
    )
    assert file_card["kind"] == "file"
    assert file_card["file_size"] == 1048576
    assert "1.0 MB" in file_card["summary"]

    unknown = appmsg.parse_app_message(
        _xml(
            999,
            "<title>新型卡片</title><des>未来格式</des>"
            "<url>https://example.com/future</url>",
        ),
        _local_type(999),
    )
    assert unknown["kind"] == "app_message"
    assert unknown["label"] == "分享卡片"
    assert "新型卡片" in unknown["summary"]
    assert "https://example.com/future" in unknown["summary"]


def test_quote_card_keeps_reply_and_quoted_content():
    import appmsg

    parsed = appmsg.parse_app_message(
        _xml(
            57,
            "<title>我同意</title><refermsg><type>1</type>"
            "<displayname>Alice</displayname>"
            "<content><![CDATA[明天十点开会]]></content></refermsg>",
        ),
        _local_type(57),
    )
    assert parsed["kind"] == "quote"
    assert parsed["quoted_sender"] == "Alice"
    assert parsed["quoted_content"] == "明天十点开会"
    assert parsed["summary"] == "我同意 [↩ Alice: 明天十点开会]"


def test_nested_type_does_not_override_local_type_fallback():
    import appmsg

    content = (
        "<msg><appmsg><title>我同意</title><refermsg><type>1</type>"
        "<displayname>Alice</displayname><content>原消息</content>"
        "</refermsg></appmsg></msg>"
    )
    parsed = appmsg.parse_app_message(content, _local_type(57))
    assert parsed["app_type"] == 57
    assert parsed["kind"] == "quote"


def test_query_and_export_share_the_same_app_message_parser(monkeypatch):
    import export_chat
    import message
    import query

    monkeypatch.setattr(message, "is_my_message", lambda _sender: False)
    content = _xml(
        3,
        "<title><![CDATA[测试歌曲]]></title><des>测试歌手</des>"
        "<dataurl>https://example.com/song</dataurl>",
    )
    row = {
        "create_time": 1,
        "local_type": _local_type(3),
        "real_sender_id": 0,
        "message_content": content,
        "message_hex": content.encode().hex(),
        "msg_hex": content.encode().hex(),
    }

    formatted = query._fmt_msg(row)
    assert formatted["type"] == "音乐分享"
    assert formatted["content"].startswith("[音乐分享] 测试歌曲")
    assert formatted["app"]["artist"] == "测试歌手"
    assert formatted["is_text"] is True

    exported = export_chat.format_row(row)
    assert "[音乐分享] 测试歌曲" in exported
    assert "测试歌手" in exported
    assert "https://example.com/song" in exported


def test_read_search_summary_and_stats_include_app_messages(win_backend):
    import db
    import query
    from conftest import SAMPLE_TABLE

    now = int(time.time())
    path = os.path.join(
        win_backend,
        "wxid_test001_a2f4",
        "db_storage",
        "message",
        "message_0.db",
    )
    finder = _xml(
        51,
        "<title>当前版本不支持展示该内容，请升级至最新版本。</title>"
        "<finderFeed><nickname>城市漫游</nickname><desc>夜游外滩</desc>"
        "<objectId>12345</objectId></finderFeed>",
    )
    music = _xml(3, "<title>夜曲</title><des>周杰伦</des>")
    con = sqlite3.connect(path)
    con.execute(
        f"INSERT INTO {SAMPLE_TABLE} "
        "(local_id, server_id, create_time, local_type, real_sender_id, message_content) "
        "VALUES (3, 1003, ?, ?, 2, ?), (4, 1004, ?, ?, 2, ?);",
        (now, _local_type(51), finder, now + 1, _local_type(3), music),
    )
    con.commit()
    con.close()
    db.reset_caches()

    read = query.read_chat("wxid_friend001", limit=10, days=1)
    read_types = {item["type"] for item in read["chats"][0]["messages"]}
    assert {"视频号", "音乐分享"}.issubset(read_types)

    search = query.search("视频号", days=1, limit=10)
    assert search["count"] == 1
    assert search["messages"][0]["type"] == "视频号"
    assert "夜游外滩" in search["messages"][0]["content"]

    summary = query.summary(days=1)
    summary_types = {
        item["type"]
        for conversation in summary["conversations"]
        for item in conversation["messages"]
    }
    assert {"视频号", "音乐分享"}.issubset(summary_types)

    stats = dict(query.stats(days=1)["by_type"])
    assert stats["视频号"] == 1
    assert stats["音乐分享"] == 1


def test_stats_reports_unknown_app_message_subtypes(win_backend):
    import db
    import query
    from conftest import SAMPLE_TABLE

    now = int(time.time())
    path = os.path.join(
        win_backend,
        "wxid_test001_a2f4",
        "db_storage",
        "message",
        "message_0.db",
    )
    unknown = _xml(
        999,
        "<title>未来卡片</title><custom><opaque>未来字段</opaque></custom>",
    )
    con = sqlite3.connect(path)
    con.execute(
        f"INSERT INTO {SAMPLE_TABLE} "
        "(local_id, server_id, create_time, local_type, real_sender_id, message_content) "
        "VALUES (9, 1009, ?, ?, 2, ?);",
        (now, _local_type(999), unknown),
    )
    con.commit()
    con.close()
    db.reset_caches()

    result = query.stats(days=1)
    search = query.search("未来字段", days=1, limit=10)

    assert result["unknown_app_types"] == [{"app_type": 999, "count": 1}]
    assert search["count"] == 1
