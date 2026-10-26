import pytest


def test_image_body_summarizes_size_without_raw_tags():
    import xmlmsg

    content = (
        '<?xml version="1.0"?>\n<msg>\n\t<img aeskey="f60f44fb5b1451af" '
        'encryver="1" cdnthumburl="3057020100044b30490201000204f555e548" '
        'length="81920" />\n</msg>'
    )
    parsed = xmlmsg.parse_xml_message(content, 3)

    assert parsed["kind"] == "image"
    assert parsed["label"] == "图片"
    assert parsed["parsed"] is True
    assert parsed["summary"] == "80KB"
    assert "<" not in xmlmsg.format_xml_message(parsed)


def test_voice_body_reports_duration_and_transcript():
    import xmlmsg

    content = (
        '<msg><voicemsg voiceformat="4" length="33177" endflag="1" '
        'voicelength="17500" fromusername="wangjianjun189" isPlayed="1" />'
        '</msg>'
    )
    parsed = xmlmsg.parse_xml_message(content, 34)

    assert parsed["kind"] == "voice"
    assert parsed["label"] == "语音"
    assert parsed["duration"] == "17.5秒"
    assert parsed["sender"] == "wangjianjun189"
    assert parsed["summary"] == "17.5秒"


def test_voice_body_prefers_transcript_when_present():
    import xmlmsg

    content = (
        '<msg><voicemsg voicelength="3000" transtext="今晚八点开会" />'
        '</msg>'
    )
    parsed = xmlmsg.parse_xml_message(content, 34)

    assert parsed["transcript"] == "今晚八点开会"
    assert "今晚八点开会" in parsed["summary"]


def test_emoji_body_keeps_md5_handle():
    import xmlmsg

    content = (
        '<msg><emoji fromusername="wxid_sender001" '
        'tousername="49418517394@chatroom" type="1" '
        'md5="b5e237a3a15d205ca1e2dde26624c4ed" len="1024" /></msg>'
    )
    parsed = xmlmsg.parse_xml_message(content, 47)

    assert parsed["kind"] == "emoji"
    assert parsed["label"] == "表情"
    assert parsed["md5"] == "b5e237a3a15d205ca1e2dde26624c4ed"
    assert "b5e237a3a15d" in parsed["summary"]


def test_location_body_reports_name_and_coordinates():
    import xmlmsg

    content = (
        '<msg>\n\t<location x="30.779844" y="111.161328" scale="15" '
        'label="" maptype="roadmap" '
        'poiname="三峡人家景区胡金滩码头西47米" />\n</msg>'
    )
    parsed = xmlmsg.parse_xml_message(content, 48)

    assert parsed["kind"] == "location"
    assert parsed["name"] == "三峡人家景区胡金滩码头西47米"
    assert parsed["latitude"] == "111.161328"
    assert parsed["longitude"] == "30.779844"
    assert "三峡人家" in parsed["summary"]


def test_video_body_reports_play_length():
    import xmlmsg

    content = (
        '<msg><videomsg aeskey="ac8aa11fa22aa7a0" playlength="8" '
        'fromusername="wxid_d7fdxdmfcg4k12" /></msg>'
    )
    parsed = xmlmsg.parse_xml_message(content, 43)

    assert parsed["kind"] == "video"
    assert parsed["summary"] == "8秒"


def test_contact_card_body_reports_display_name():
    import xmlmsg

    content = (
        '<msg><card username="wxid_yuanbao" displayname="元宝" '
        'bigheadimgurl="https://example.com/head.jpg" /></msg>'
    )
    parsed = xmlmsg.parse_xml_message(content, 42)

    assert parsed["kind"] == "card"
    assert parsed["display_name"] == "元宝"


def test_high_subtype_bits_are_ignored():
    """WeChat packs a subtype into the high bits of ``local_type``."""
    import xmlmsg

    high = (4980736 << 16) | 49
    content = "<msg><appmsg><type>33</type></appmsg></msg>"
    parsed = xmlmsg.parse_xml_message(content, high)

    assert parsed["base_type"] == 49
    # type 49 is handled by appmsg, not by this module
    assert parsed["kind"] == "message"


def test_is_xml_type_accepts_string_and_int_forms():
    import xmlmsg

    assert xmlmsg.is_xml_type("3") is True
    assert xmlmsg.is_xml_type(3) is True
    assert xmlmsg.is_xml_type("1") is False
    assert xmlmsg.is_xml_type("") is False
    assert xmlmsg.is_xml_type("not-a-number") is False


def test_malformed_xml_keeps_raw_content_instead_of_dropping_it():
    import xmlmsg

    parsed = xmlmsg.parse_xml_message("<msg><img length=", 3)

    assert parsed["parsed"] is False
    assert parsed["raw"] == "<msg><img length="


def test_empty_body_is_handled():
    import xmlmsg

    for content in (None, "", b""):
        parsed = xmlmsg.parse_xml_message(content, 3)
        assert parsed["parsed"] is False
        assert parsed["raw"] == ""


def test_is_xml_body_detects_known_shapes():
    import xmlmsg

    assert xmlmsg.is_xml_body("<msg><img /></msg>") is True
    assert xmlmsg.is_xml_body('<?xml version="1.0"?><msg></msg>') is True
    assert xmlmsg.is_xml_body("普通文本消息") is False
    assert xmlmsg.is_xml_body("") is False
    assert xmlmsg.is_xml_body(None) is False


def test_query_formatter_renders_xml_body_instead_of_raw_tags():
    import query

    content = (
        '<msg><voicemsg voicelength="17500" '
        'fromusername="wangjianjun189" /></msg>'
    )
    row = {
        "create_time": "1700000000",
        "local_type": "34",
        "message_hex": content.encode("utf-8").hex(),
        "sender_wxid": "wxid_other",
    }
    formatted = query._fmt_msg(row)

    assert formatted["type"] == "语音"
    assert formatted["is_text"] is True
    assert "<voicemsg" not in formatted["content"]
    assert "17.5秒" in formatted["content"]


def test_query_formatter_still_keeps_plain_text_untouched():
    import query

    row = {
        "create_time": "1700000000",
        "local_type": "1",
        "message_hex": "今天下午三点开会".encode("utf-8").hex(),
        "sender_wxid": "wxid_other",
    }
    formatted = query._fmt_msg(row)

    assert formatted["type"] == "文本"
    assert formatted["content"] == "今天下午三点开会"
    assert formatted["is_text"] is True


def test_body_contains_matches_plain_and_compressed_bodies():
    import query

    plain = {"message_body": "这里提到三峡大坝".encode("utf-8")}
    assert query._body_contains(plain, "三峡".encode("utf-8")) is True
    assert query._body_contains(plain, "黄河".encode("utf-8")) is False


def test_body_contains_reads_both_hex_and_raw_shapes():
    import query

    body = "三峡".encode("utf-8")
    hexed = {"message_hex": body.hex()}
    raw = {"message_body": body}
    assert query._body_contains(hexed, "三峡".encode("utf-8")) is True
    assert query._body_contains(raw, "三峡".encode("utf-8")) is True


def test_body_contains_handles_empty_body():
    import query

    assert query._body_contains({}, "x".encode("utf-8")) is False
    assert query._body_contains({"message_body": b""}, "x".encode("utf-8")) is False


def test_label_could_match_finds_card_kinds():
    import query

    row = {"local_type": "49"}
    assert query._label_could_match(row, "视频号") is True
    assert query._label_could_match(row, "音乐分享") is True
    assert query._label_could_match(row, "完全不相关的词") is False


def test_label_could_match_finds_media_kinds():
    import query

    assert query._label_could_match({"local_type": "3"}, "图片") is True
    assert query._label_could_match({"local_type": "34"}, "语音") is True
    assert query._label_could_match({"local_type": "3"}, "视频号") is False


def test_message_exposes_zstd_body_check():
    import message

    assert message.is_zstd_body(b"\x28\xb5\x2f\xfdrest") is True
    assert message.is_zstd_body(b"plain text") is False
    assert message.is_zstd_body(None) is False
    assert message.is_zstd_body("plain") is False
