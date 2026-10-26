import importlib


def test_macos_backend_is_sqlcipher(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    import config
    monkeypatch.setattr(config.shutil, "which", lambda name: "/usr/local/bin/sqlcipher")
    importlib.reload(config)
    assert config.DB_BACKEND == "sqlcipher"
    assert config.SQLCIPHER_PATH == "/usr/local/bin/sqlcipher"


def test_windows_backend_is_sqlite3(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    import config
    importlib.reload(config)
    assert config.DB_BACKEND == "sqlite3"
    assert config.DECRYPTED_DIR.endswith("decrypted")


def test_reload_restores_real_platform():
    import config
    importlib.reload(config)  # 还原真实平台，避免污染后续测试
