from types import SimpleNamespace


def test_sqlcipher_query_surfaces_cli_error(monkeypatch, tmp_path):
    import config
    import crypto
    import db

    db_path = tmp_path / "message_0.db"
    db_path.write_bytes(b"0123456789abcdef" + b"x" * 32)
    monkeypatch.setattr(config, "DB_BACKEND", "sqlcipher")
    monkeypatch.setattr(config, "SQLCIPHER_PATH", "/test/sqlcipher")
    monkeypatch.setattr(crypto, "load_key", lambda: "ab" * 32)
    monkeypatch.setattr(
        db.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"Error: file is not a database",
        ),
    )

    try:
        db.query(str(db_path), "SELECT 1;")
    except RuntimeError as exc:
        assert "file is not a database" in str(exc)
        assert "abab" not in str(exc)
    else:
        raise AssertionError("SQLCipher errors must not look like empty results")


def test_sqlcipher_is_always_opened_read_only(monkeypatch):
    import config
    import db

    monkeypatch.setattr(config, "SQLCIPHER_PATH", "/test/sqlcipher")
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return SimpleNamespace(returncode=0, stdout=b"1\n", stderr=b"")

    monkeypatch.setattr(db.subprocess, "run", fake_run)
    assert db._run_sqlcipher("/tmp/example.db", "SELECT 1;", 1) == "1"
    assert seen["args"] == ["/test/sqlcipher", "-readonly", "/tmp/example.db"]
