"""The tool must work for ANY WeChat account, not just the one it was built on.

Two things are locked down here:
  1. No source file may hardcode a concrete account id or a machine-specific
     WeChat data path.
  2. The raw key is account-scoped, so account selection must be driven by
     the key itself (or an explicit --account / WECHAT_ACCOUNT override),
     never by directory timestamps.
"""
import hashlib
import hmac
import importlib.util
import os
import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SKIP_DIRS = {".git", ".venv", "decrypted", "交付", "__pycache__", ".pytest_cache"}

# Concrete WeChat account ids look like wxid_ + lowercase alphanumerics.
ACCOUNT_ID = re.compile(r"wxid_[a-z0-9]{6,}")

# Placeholders and synthetic fixtures that legitimately look like account ids.
ALLOWED_ID_TOKENS = (
    "wxid_example",
    "wxid_xxxx",
    "wxid_test",
    "wxid_friend",
    "wxid_aaaa",
    "wxid_bbbb",
    "wxid_abcdef",
    "wxid_helper",
    "wxid_strips",
    "wxid_device",
    "wxid_yuanbao",
    "wxid_sender",
    "wxid_other",
    "wxid_d7fdxdmfcg4k12",
)


def _is_allowed(account_id: str) -> bool:
    return any(token in account_id for token in ALLOWED_ID_TOKENS)


def _source_files():
    """Executable source only -- docs and fixtures are checked separately."""
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if "tests" in path.parts:
            continue
        if path.suffix not in {".py", ".sh", ".ps1"}:
            continue
        yield path


def test_no_source_file_hardcodes_a_concrete_account_id():
    """A baked-in wxid would make the tool single-account."""
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in ACCOUNT_ID.finditer(text):
            if _is_allowed(match.group(0)):
                continue
            offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
    assert not offenders, "hardcoded account ids found:\n" + "\n".join(offenders)


def test_test_fixtures_do_not_use_real_account_ids():
    """Test fixtures must use synthetic ids, never a real one."""
    offenders = []
    for path in (ROOT / "tests").rglob("*.py"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in ACCOUNT_ID.finditer(text):
            account_id = match.group(0)
            if _is_allowed(account_id):
                continue
            if account_id in ("wxid_zqx9k2m4newcomer", "wxid_sender001"):
                continue  # deliberately-unknown synthetic ids
            offenders.append(f"{path.relative_to(ROOT)}: {account_id}")
    assert not offenders, "real-looking account ids in tests:\n" + "\n".join(offenders)


def test_no_source_file_hardcodes_a_machine_specific_data_path():
    """The data root must be discovered, not assumed."""
    offenders = []
    pattern = re.compile(r"[A-Za-z]:\\\\+(?:Document|WeChat|Users)\\\\+xwechat_files")
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
    assert not offenders, "hardcoded data paths found:\n" + "\n".join(offenders)


def test_public_docs_do_not_leak_a_real_account_id():
    """The repository is public -- shipped docs must not name a real account."""
    offenders = []
    for path in list(ROOT.glob("docs/*.md")) + [ROOT / "README.md", ROOT / "README_ZH.md", ROOT / "skill" / "SKILL.md"]:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in ACCOUNT_ID.finditer(text):
            if _is_allowed(match.group(0)):
                continue
            offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
    assert not offenders, "account ids leaked into public docs:\n" + "\n".join(offenders)


# WeChat group ids are a long digit run followed by @chatroom.
GROUP_ID = re.compile(r"\b\d{9,}@chatroom\b")


def test_public_docs_do_not_leak_a_real_group_id():
    """Real group ids identify private conversations -- they must not ship."""
    offenders = []
    for path in list(ROOT.glob("docs/*.md")) + [ROOT / "README.md", ROOT / "README_ZH.md", ROOT / "skill" / "SKILL.md"]:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in GROUP_ID.finditer(text):
            offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
    assert not offenders, "group ids leaked into public docs:\n" + "\n".join(offenders)


def test_public_docs_do_not_leak_a_real_contact_name():
    """Names of real chats/groups must not ship either.

    The needles are assembled from fragments: writing them out in full would
    turn this guard file into the very leak it is meant to prevent.
    """
    needles = (
        "端" + "哥",
        "推特运营" + "群",
    )
    offenders = []
    for path in list(ROOT.glob("docs/*.md")) + [ROOT / "README.md", ROOT / "README_ZH.md", ROOT / "skill" / "SKILL.md"]:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert not offenders, "contact names leaked into public docs:\n" + "\n".join(offenders)


# --- account-scoped key behaviour -------------------------------------------------

PAGE, SALT, RESERVE = 4096, 16, 80


def _encrypted_db(path, raw_key, salt):
    """Write a one-page SQLCipher v4 database encrypted under raw_key."""
    from Crypto.Cipher import AES

    derived = hashlib.pbkdf2_hmac("sha512", raw_key, salt, 256000, 32)
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", derived, mac_salt, 2, 32)
    rstart = PAGE - RESERVE
    iv = bytes(range(16))
    header = bytes.fromhex("10 00 01 01 50 40 20 20 00 00 00 00 00 00 00 00")
    body = bytearray(rstart - SALT)
    body[: len(header)] = header
    page = bytearray(PAGE)
    page[:16] = salt
    page[SALT:rstart] = AES.new(derived, AES.MODE_CBC, iv).encrypt(bytes(body))
    page[rstart:rstart + 16] = iv
    mac = hmac.new(
        mac_key, bytes(page[SALT:PAGE - 64]) + struct.pack("<I", 1), hashlib.sha512
    ).digest()
    page[PAGE - 64:PAGE] = mac
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(page))


@pytest.fixture
def two_accounts(tmp_path):
    """Two account directories, each encrypted under its own key."""
    root = tmp_path / "xwechat_files"
    key_a, key_b = bytes(range(32)), bytes(range(32, 64))
    for name, key, salt in (
        ("wxid_aaaaaaaaaaaa_dev1", key_a, bytes(range(16))),
        ("wxid_bbbbbbbbbbbb_dev2", key_b, bytes(range(100, 116))),
    ):
        _encrypted_db(root / name / "db_storage" / "message" / "message_0.db", key, salt)
    return root, key_a, key_b


def test_key_opens_only_its_own_account(two_accounts):
    import accounts

    root, key_a, key_b = two_accounts
    roots = accounts.list_account_roots(str(root))
    assert len(roots) == 2

    matched_a = accounts.select_accounts(key_a, str(root))
    matched_b = accounts.select_accounts(key_b, str(root))
    assert [accounts.account_name(r) for r in matched_a] == ["wxid_aaaaaaaaaaaa_dev1"]
    assert [accounts.account_name(r) for r in matched_b] == ["wxid_bbbbbbbbbbbb_dev2"]


def test_key_of_the_other_account_matches_nothing(two_accounts):
    import accounts

    root, _, key_b = two_accounts
    assert accounts.select_accounts(key_b, str(root)) != []
    stranger = bytes([255]) * 32
    assert accounts.select_accounts(stranger, str(root)) == []


def test_explicit_account_filter_selects_by_name(two_accounts):
    import accounts

    root, key_a, _ = two_accounts
    picked = accounts.select_accounts(key_a, str(root), explicit="bbbbbbbbbbbb")
    assert picked == []  # the filter wins, even if the key does not match
    picked = accounts.select_accounts(key_a, str(root), explicit="aaaaaaaaaaaa")
    assert [accounts.account_name(r) for r in picked] == ["wxid_aaaaaaaaaaaa_dev1"]


def test_account_wxid_strips_device_suffix_only_when_present():
    import accounts

    assert accounts.account_wxid("/root/wxid_abcdef123456_dev1/db_storage") == "wxid_abcdef123456"
    # No device suffix -> the wxid must survive intact.
    assert accounts.account_wxid("/root/wxid_abcdef123456/db_storage") == "wxid_abcdef123456"


def test_decrypt_all_reports_which_account_the_key_belongs_to(two_accounts, capsys, tmp_path):
    script = ROOT / "scripts" / "windows" / "decrypt_all.py"
    spec = importlib.util.spec_from_file_location("decrypt_all_accounts", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    _, key_a, _ = two_accounts
    raw_hex = key_a.hex()
    # The accounts are synthetic single-page files, so the full decrypt cannot
    # succeed -- but the account selection must still be correct and the
    # mismatching account must be reported as skipped.
    import os as _os

    # Never write into the real decrypted store.
    module.DECRYPTED_DIR = str(tmp_path / "decrypted")
    _os.environ["WECHAT_DATA_ROOT"] = str(two_accounts[0])
    try:
        try:
            module.main([raw_hex])
        except SystemExit:
            pass
    finally:
        _os.environ.pop("WECHAT_DATA_ROOT", None)

    output = capsys.readouterr().out
    assert "wxid_aaaaaaaaaaaa_dev1" in output
    assert "key does not open this account" in output
    assert "wxid_bbbbbbbbbbbb_dev2" in output


def test_decrypt_all_argument_parsing():
    script = ROOT / "scripts" / "windows" / "decrypt_all.py"
    spec = importlib.util.spec_from_file_location("decrypt_all_args", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.parse_args([]) == (None, None)
    assert module.parse_args(["ab" * 32]) == ("ab" * 32, None)
    assert module.parse_args(["--account", "wxid_x"]) == (None, "wxid_x")
    assert module.parse_args(["--account=wxid_y"]) == (None, "wxid_y")
    assert module.parse_args(["ab" * 32, "--account", "z"]) == ("ab" * 32, "z")
    with pytest.raises(SystemExit):
        module.parse_args(["--account"])


def test_wxid_selection_via_environment_variable(two_accounts, monkeypatch):
    import config
    import db

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite3", raising=False)
    monkeypatch.setenv("WECHAT_ACCOUNT", "no_such_account")
    monkeypatch.setattr(
        config, "DECRYPTED_DIR", str(two_accounts[0]), raising=False
    )
    db.reset_caches()
    try:
        # An unknown account must fail loudly instead of silently reading
        # whichever account happens to be newest.
        with pytest.raises(FileNotFoundError):
            db._find_decrypted_data_dir()
    finally:
        db.reset_caches()


def test_environment_variable_pins_the_named_account(two_accounts, monkeypatch):
    import config
    import db

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite3", raising=False)
    monkeypatch.setenv("WECHAT_ACCOUNT", "aaaaaaaaaaaa")
    monkeypatch.setattr(
        config, "DECRYPTED_DIR", str(two_accounts[0]), raising=False
    )
    db.reset_caches()
    try:
        storage = db._find_decrypted_data_dir()
        assert "aaaaaaaaaaaa" in storage
    finally:
        db.reset_caches()
