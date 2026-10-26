import os
import stat


def test_load_key_validates_hex(monkeypatch, tmp_path):
    import config
    import crypto

    key_file = tmp_path / "key.txt"
    monkeypatch.setattr(config, "KEY_FILE", str(key_file))
    key_file.write_text("not-a-key", encoding="ascii")
    try:
        crypto.load_key()
    except ValueError as exc:
        assert "密钥" in str(exc)
    else:
        raise AssertionError("invalid key should fail")

    key_file.write_text("AB" * 32, encoding="ascii")
    assert crypto.load_key() == "ab" * 32


def test_derived_key_cache_is_private(monkeypatch, tmp_path):
    import crypto

    db_file = tmp_path / "sample.db"
    db_file.write_bytes(b"0123456789abcdef" + b"x" * 32)
    cache_file = tmp_path / "all_keys.json"
    monkeypatch.setattr(crypto, "_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(crypto, "_cache_loaded", False)
    crypto._derived_cache.clear()

    derived = crypto.derive_key("ab" * 32, str(db_file))
    assert len(derived) == 64
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(cache_file).st_mode) == 0o600
