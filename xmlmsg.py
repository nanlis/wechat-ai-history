"""Parse WeChat XML message bodies (image, voice, video, emoji, location).

WeChat stores non-text messages as a small XML document instead of plain
text.  ``local_type`` 3/34/43/47/48 (image/voice/video/emoji/location) and
friends all carry such a body, so a raw read hands an agent a wall of tags
instead of anything readable.

This module turns those bodies into stable metadata plus a short human
readable summary.  It is deliberately read-only and dependency free.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET


# WeChat packs a subtype into the high bits of ``local_type``.  The low 16
# bits carry the real message class.
_TYPE_BASE_MASK = 0xFFFF

# Message classes that carry an XML body we know how to summarize.
XML_MESSAGE_TYPES = {
    3: ("image", "图片"),
    34: ("voice", "语音"),
    42: ("card", "名片"),
    43: ("video", "视频"),
    47: ("emoji", "表情"),
    48: ("location", "位置"),
    62: ("video", "视频"),
    74: ("video", "视频"),
}

_UNKNOWN = ("message", "其他消息")


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    return html.unescape(str(value)).strip("\x00\r\n \t")


def _first(*values) -> str:
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _text_of(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return _clean(node.text)


def _attr(node: ET.Element | None, *names: str) -> str:
    """Read the first attribute that exists, matching on local name."""
    if node is None:
        return ""
    attributes = {_local_name(key): value for key, value in node.attrib.items()}
    for name in names:
        value = attributes.get(name.lower())
        if value:
            return _clean(value)
    return ""


def _find(root: ET.Element, *names: str) -> ET.Element | None:
    """Depth-first search for the first child element with a matching tag."""
    wanted = {name.lower() for name in names}
    for node in root.iter():
        if node is root:
            continue
        if _local_name(node.tag) in wanted:
            return node
    return None


def _xml_fragment(content: str | bytes | None) -> str:
    """Trim a raw body down to the XML document it embeds."""
    if content is None:
        return ""
    if isinstance(content, (bytes, bytearray)):
        text = bytes(content).decode("utf-8", errors="replace")
    else:
        text = str(content)
    text = text.strip("\x00\r\n \t")
    if not text:
        return ""
    text = "".join(
        char for char in text
        if char in "\r\n\t" or ord(char) >= 32
    )
    lowered = text.lower()
    positions = [position for marker in ("<?xml", "<msg", "<appmsg", "<voicemsg")
                 if (position := lowered.find(marker)) >= 0]
    if positions:
        text = text[min(positions):]
    lowered = text.lower()
    for closing in ("</msg>", "</voicemsg>", "</appmsg>"):
        end = lowered.rfind(closing)
        if end >= 0:
            text = text[:end + len(closing)]
            break
    return text.strip()


def _parse_root(content: str | bytes | None) -> ET.Element | None:
    fragment = _xml_fragment(content)
    if not fragment:
        return None
    try:
        return ET.fromstring(fragment)
    except ET.ParseError:
        return None


def _base_type(raw_type) -> int:
    """Return the low 16 bits of ``local_type`` (the real message class)."""
    try:
        value = int(raw_type)
    except (TypeError, ValueError):
        return -1
    return value & _TYPE_BASE_MASK


def _voice_seconds(length) -> str:
    try:
        total = int(length)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    if total < 1000:
        return f"{total}秒"
    return f"{total / 1000:.1f}秒"


def _summarize_image(node: ET.Element) -> str:
    """Images carry no text; only size and a media handle are available."""
    length = _attr(node, "length")
    if not length:
        return ""
    try:
        return f"{int(length) // 1024}KB"
    except ValueError:
        return ""


def _summarize_voice(node: ET.Element) -> str:
    seconds = _voice_seconds(_attr(node, "voicelength", "length"))
    translate = _attr(node, "transtext", "transcontent")
    if translate:
        return f"{seconds} {translate}".strip()
    return seconds


def _summarize_video(node: ET.Element) -> str:
    length = _attr(node, "playlength")
    if length:
        try:
            return f"{int(length)}秒"
        except ValueError:
            pass
    return ""


def _summarize_emoji(node: ET.Element) -> str:
    md5 = _attr(node, "md5", "androidmd5", "applemd5")
    return f"表情 {md5[:12]}" if md5 else "表情"


def _summarize_location(node: ET.Element) -> str:
    name = _first(_attr(node, "poiname"), _attr(node, "label"))
    x = _attr(node, "x")
    y = _attr(node, "y")
    parts = [name] if name else []
    if x and y:
        parts.append(f"({x}, {y})")
    return " ".join(parts)


def _summarize_card(node: ET.Element) -> str:
    """Contact cards live under ``<card>`` with a displayname attribute."""
    nickname = _first(
        _attr(node, "displayname"),
        _attr(node, "nickname"),
        _attr(node, "bigheadimgurl"),
    )
    return nickname


def _summarize(node: ET.Element, kind: str) -> str:
    if kind == "image":
        return _summarize_image(node)
    if kind == "voice":
        return _summarize_voice(node)
    if kind == "video":
        return _summarize_video(node)
    if kind == "emoji":
        return _summarize_emoji(node)
    if kind == "location":
        return _summarize_location(node)
    if kind == "card":
        return _summarize_card(node)
    return ""


def _find_payload(root: ET.Element, kind: str) -> ET.Element:
    """Locate the element that actually carries the payload for this kind."""
    tags = {
        "image": ("img",),
        "voice": ("voicemsg",),
        "video": ("videomsg",),
        "emoji": ("emoji",),
        "location": ("location",),
        "card": ("card",),
    }.get(kind, ())
    for tag in tags:
        node = _find(root, tag)
        if node is not None:
            return node
    return root


def is_xml_type(raw_type) -> bool:
    """True when ``local_type`` marks a message class we know how to parse.

    Accepts the string form used across the query layer as well as ints.
    """
    return _base_type(raw_type) in XML_MESSAGE_TYPES


def parse_xml_message(content: str | bytes | None, raw_type=0) -> dict:
    """Return stable metadata for an XML-bodied WeChat message.

    The result always carries ``kind``, ``label`` and ``summary`` so callers
    can render a consistent line regardless of which XML shape came in.
    """
    base = _base_type(raw_type)
    kind, label = XML_MESSAGE_TYPES.get(base, _UNKNOWN)

    root = _parse_root(content)
    if root is None:
        # Not parseable as XML: keep the raw text so nothing is silently lost.
        raw = _clean(content)
        return {
            "kind": kind,
            "label": label,
            "summary": raw[:200],
            "raw": raw,
            "parsed": False,
            "base_type": base,
        }

    payload = _find_payload(root, kind)
    summary = _summarize(payload, kind)

    extra = {}
    if kind == "voice":
        extra["duration"] = _voice_seconds(_attr(payload, "voicelength"))
        extra["transcript"] = _first(
            _attr(payload, "transtext"), _attr(payload, "transcontent")
        )
    elif kind == "image":
        extra["cdn_url"] = _first(
            _attr(payload, "cdnthumburl"),
            _attr(payload, "cdnmidimgurl"),
            _attr(payload, "cdnbigimgurl"),
        )
        extra["aes_key"] = _first(
            _attr(payload, "aeskey"), _attr(payload, "cdnthumbaeskey")
        )
    elif kind == "emoji":
        extra["md5"] = _first(
            _attr(payload, "md5"), _attr(payload, "androidmd5"), _attr(payload, "applemd5")
        )
    elif kind == "location":
        extra["latitude"] = _attr(payload, "y")
        extra["longitude"] = _attr(payload, "x")
        extra["name"] = _first(_attr(payload, "poiname"), _attr(payload, "label"))
    elif kind == "card":
        extra["display_name"] = _first(
            _attr(payload, "displayname"), _attr(payload, "nickname")
        )

    # Group chats carry the real sender inside the payload, which is the only
    # reliable source when the envelope prefix was stripped upstream.
    sender = _first(
        _attr(payload, "fromusername"),
        _attr(root, "fromusername"),
    )

    return {
        "kind": kind,
        "label": label,
        "summary": summary,
        "raw": "",
        "parsed": True,
        "base_type": base,
        "sender": sender,
        **extra,
    }


def format_xml_message(data: dict) -> str:
    """Render a parsed XML message as one readable line."""
    label = data.get("label") or "消息"
    summary = _clean(data.get("summary"))
    return f"[{label}]" + (f" {summary}" if summary else "")


def is_xml_body(content: str | bytes | None) -> bool:
    """True when the body looks like an XML document rather than plain text."""
    if content is None:
        return False
    if isinstance(content, (bytes, bytearray)):
        try:
            content = bytes(content).decode("utf-8", errors="replace")
        except Exception:
            return False
    text = str(content).lstrip()
    if not text.startswith("<"):
        return False
    return bool(re.match(r"<\?xml|<msg\b|<img\b|<voicemsg\b|<videomsg\b|<emoji\b|<location\b|<card\b", text))
