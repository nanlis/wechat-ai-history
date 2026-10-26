import os
import sys


def test_csv_field_size_limit_falls_back(monkeypatch):
    import db

    calls = []

    def _field_size_limit(limit):
        calls.append(limit)
        if limit > 2_147_483_647:
            raise OverflowError
        return limit

    monkeypatch.setattr(db.csv, "field_size_limit", _field_size_limit)
    selected = db._set_csv_field_size_limit()
    assert calls[0] == sys.maxsize
    assert selected <= 2_147_483_647
    assert calls[-1] == selected


def test_query_reads_plaintext(win_backend):
    import db
    data_dir = db.find_data_dir()
    msg_db = os.path.join(data_dir, "message", "message_0.db")
    rows = db.query(msg_db, "SELECT user_name FROM Name2Id;")
    assert rows == [{"user_name": "wxid_friend001"}]


def test_query_raw_single_column(win_backend):
    import db
    data_dir = db.find_data_dir()
    msg_db = os.path.join(data_dir, "message", "message_0.db")
    names = db.query_raw(msg_db, "SELECT name FROM sqlite_master WHERE type='table';")
    assert "Name2Id" in names


def test_get_name2id_maps_table_to_wxid(win_backend):
    import db
    from conftest import SAMPLE_TABLE
    mapping = db.get_name2id()
    assert mapping.get(SAMPLE_TABLE) == "wxid_friend001"


def test_query_raw_multi_column_and_null(win_backend):
    import db
    from conftest import SAMPLE_TABLE
    data_dir = db.find_data_dir()
    msg_db = os.path.join(data_dir, "message", "message_0.db")
    rows = db.query_raw(
        msg_db,
        f"SELECT local_id, NULL, message_content FROM {SAMPLE_TABLE} WHERE local_id=1;",
    )
    assert rows == ["1||hello"]


def test_get_message_dbs_no_key_on_windows(win_backend, monkeypatch):
    import crypto, db
    def _boom():
        raise FileNotFoundError("key.txt missing")
    monkeypatch.setattr(crypto, "load_key", _boom)
    dbs = db.get_message_dbs()
    assert len(dbs) == 1 and dbs[0].endswith("message_0.db")


def test_get_my_wxid_strips_device_suffix(win_backend):
    import db
    assert db.get_my_wxid() == "wxid_test001"


def test_decrypted_data_discovery_does_not_require_message_zero(win_backend):
    import db

    data_dir = db.find_data_dir()
    message_dir = os.path.join(data_dir, "message")
    os.rename(
        os.path.join(message_dir, "message_0.db"),
        os.path.join(message_dir, "message_3.db"),
    )
    db.reset_caches()

    assert db.find_data_dir() == data_dir
    assert db.get_message_dbs() == [os.path.join(message_dir, "message_3.db")]


def test_find_data_dir_missing_raises(monkeypatch, tmp_path):
    import pytest
    import config
    import db
    monkeypatch.setattr(config, "DB_BACKEND", "sqlite3", raising=False)
    monkeypatch.setattr(config, "DECRYPTED_DIR", str(tmp_path / "empty"), raising=False)
    with pytest.raises(FileNotFoundError):
        db.find_data_dir()


def test_encrypted_data_discovery_validates_nonzero_message_shard(monkeypatch, tmp_path):
    import config
    import crypto
    import db

    data_dir = tmp_path / "wxid_device" / "db_storage"
    message_dir = data_dir / "message"
    message_dir.mkdir(parents=True)
    shard = message_dir / "message_4.db"
    shard.write_bytes(b"encrypted")
    monkeypatch.setattr(config, "DB_BACKEND", "sqlcipher", raising=False)
    monkeypatch.setattr(
        config,
        "WECHAT_DATA_GLOB",
        str(tmp_path / "*" / "db_storage"),
        raising=False,
    )
    monkeypatch.setattr(crypto, "load_key", lambda: "11" * 32)
    monkeypatch.setattr(db, "test_key", lambda _key, path: path == str(shard))
    db.reset_caches()

    assert db.find_data_dir() == str(data_dir)


def test_plaintext_query_never_creates_missing_db(win_backend, tmp_path):
    import db
    missing = tmp_path / "missing.db"
    assert db.test_key("", str(missing)) is False
    assert not missing.exists()


def test_plaintext_query_is_read_only(win_backend):
    import pytest
    import db
    data_dir = db.find_data_dir()
    msg_db = os.path.join(data_dir, "message", "message_0.db")
    with pytest.raises(Exception, match="readonly|read-only|query_only"):
        db.query(msg_db, "DELETE FROM Name2Id;")
    assert db.query(msg_db, "SELECT count(*) AS n FROM Name2Id;") == [{"n": 1}]
