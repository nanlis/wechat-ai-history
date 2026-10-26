import time


def test_plain_system_event_classification():
    import message

    cases = {
        '"Alice" 拍了拍 "Bob" 的肩膀': ("pat", "拍一拍"),
        '"Alice" 撤回了一条消息': ("recall", "撤回"),
        '"Alice"邀请"Bob"加入了群聊': ("group_join", "加入群聊"),
        '"Alice"将"Bob"移出群聊': ("group_remove", "移出群聊"),
        '"Alice"退出群聊': ("group_leave", "退出群聊"),
        '"Alice"修改群名为“Project”': ("group_rename", "修改群名"),
        '"Alice"发布了群公告': ("group_notice", "群公告"),
        '"Alice"被设为管理员': ("group_admin", "群管理员"),
        '群主已转让给"Alice"': ("group_owner", "群主变更"),
        '"Alice"解散了群聊': ("group_disband", "解散群聊"),
        '你已通过了"Alice"的朋友验证请求': ("friend_added", "添加好友"),
        '你领取了"Alice"的红包': ("red_packet", "红包"),
        '你已收款 10.00 元': ("payment", "转账/收款"),
        '语音通话已结束': ("call", "通话"),
        '"Alice"置顶了一条消息': ("chat_pinned", "置顶"),
        "A future WeChat event": ("system", "系统消息"),
    }
    for content, expected in cases.items():
        parsed = message.parse_system_message(content)
        assert (parsed["event"], parsed["label"]) == expected
        assert parsed["text"] == content


def test_xml_pat_and_recall_are_supported():
    import message

    pat = (
        '<sysmsg type="pat"><pat><fromusername>Alice</fromusername>'
        '<pattedusername>Bob</pattedusername>'
        '<template><![CDATA[${fromusername} 拍了拍 ${pattedusername}]]></template>'
        "</pat></sysmsg>"
    )
    parsed = message.parse_system_message(pat)
    assert parsed == {"event": "pat", "label": "拍一拍", "text": "Alice 拍了拍 Bob"}

    recall = (
        '<sysmsg type="revokemsg"><revokemsg>'
        '<replacemsg><![CDATA[Alice 撤回了一条消息]]></replacemsg>'
        "</revokemsg></sysmsg>"
    )
    assert message.parse_system_message(recall)["event"] == "recall"

    payment = '<msg><content><![CDATA[你已收款 10.00 元]]></content></msg>'
    assert message.parse_system_message(payment) == {
        "event": "payment",
        "label": "转账/收款",
        "text": "你已收款 10.00 元",
    }


def test_plain_text_starting_with_angle_bracket_is_preserved():
    import message

    content = "<3 this is plain text"
    assert message.parse_system_message(content)["text"] == content


def test_local_type_recall_falls_back_when_content_is_empty():
    import query

    result = query._fmt_msg(
        {
            "create_time": 1,
            "local_type": 10002,
            "real_sender_id": 0,
            "message_content": "",
        }
    )
    assert result["event"] == "recall"
    assert result["type"] == "撤回"


def test_query_formats_system_event_as_readable_text():
    import query

    result = query._fmt_msg(
        {
            "create_time": 1,
            "local_type": 268445456,
            "real_sender_id": 0,
            "message_content": '"Alice" 拍了拍 "Bob"',
        }
    )
    assert result["event"] == "pat"
    assert result["type"] == "拍一拍"
    assert result["direction"] == "[系统·拍一拍]"
    assert result["content"] == '"Alice" 拍了拍 "Bob"'
    assert result["is_system"] is True


def test_query_decodes_system_event_from_wechat_blob_container():
    import query

    content = '"Alice" 拍了拍 "Bob"'
    blob = b"\x28\xb5\x2f\xfd" + b"\x00" * 6 + content.encode() + b"\x01\x00tail"
    result = query._fmt_msg(
        {
            "create_time": 1,
            "local_type": 10000,
            "real_sender_id": 0,
            "message_hex": blob.hex(),
        }
    )
    assert result["event"] == "pat"
    assert result["content"] == content


def test_export_includes_system_event_text():
    import export_chat

    content = '"Alice" 撤回了一条消息'
    line = export_chat.format_row(
        {
            "create_time": 1,
            "local_type": 10000,
            "msg_hex": content.encode().hex(),
        }
    )
    assert "[系统·撤回]" in line
    assert content in line


def test_export_decodes_system_event_from_wechat_blob_container():
    import export_chat

    content = '"Alice" 拍了拍 "Bob"'
    blob = b"\x28\xb5\x2f\xfd" + b"\x00" * 6 + content.encode() + b"\x01\x00tail"
    line = export_chat.format_row(
        {"create_time": 1, "local_type": 10000, "msg_hex": blob.hex()}
    )
    assert "[系统·拍一拍]" in line
    assert content in line


def test_search_and_summary_include_system_events(win_backend):
    import db
    import query
    from conftest import SAMPLE_TABLE

    data_dir = db.find_data_dir()
    db_path = f"{data_dir}/message/message_0.db"
    now = int(time.time())
    pat_content = '"Alice" 拍了拍 "Bob"'
    pat_blob = b"\x28\xb5\x2f\xfd" + b"\x00" * 6 + pat_content.encode() + b"\x01\x00"
    con = __import__("sqlite3").connect(db_path)
    con.execute(
        f"INSERT INTO {SAMPLE_TABLE} "
        "(local_id, server_id, create_time, local_type, real_sender_id, message_content) "
        "VALUES (3, 1003, ?, 10000, 0, ?);",
        (now, pat_blob),
    )
    con.execute(
        f"INSERT INTO {SAMPLE_TABLE} "
        "(local_id, server_id, create_time, local_type, real_sender_id, message_content) "
        "VALUES (4, 1004, ?, 10000, 0, ?);",
        (now + 1, '"Alice" 撤回了一条消息'),
    )
    con.execute(
        f"INSERT INTO {SAMPLE_TABLE} "
        "(local_id, server_id, create_time, local_type, real_sender_id, message_content) "
        "VALUES (5, 1005, ?, 10000, 0, ?);",
        (now + 2, "compatibility notice"),
    )
    con.commit()
    con.close()
    db.reset_caches()

    search = query.search("拍了拍", days=1, limit=10)
    assert search["count"] == len(search["messages"]) == 1
    assert search["messages"][0]["event"] == "pat"
    summary = query.summary(days=1)
    events = [
        item["event"]
        for conversation in summary["conversations"]
        for item in conversation["messages"]
    ]
    assert "pat" in events

    filtered = query.system_events("拍一拍", days=1, limit=10)
    assert filtered["filter"] == "pat"
    assert filtered["count"] == 1
    assert filtered["by_event"] == {"pat": 1}
    assert filtered["events"][0]["contact"] == "备注名"
