import os
import stat


def test_windows_model_is_not_assumed_cached(monkeypatch, tmp_path):
    import export_chat

    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert export_chat.model_cached("Windows") is False
    model = tmp_path / "hub" / "models--Systran--faster-whisper-large-v3"
    model.mkdir(parents=True)
    assert export_chat.model_cached("Windows") is True


def test_private_export_permissions(tmp_path):
    import export_chat

    path = tmp_path / "chat.txt"
    with export_chat._open_private_text(str(path)) as handle:
        handle.write("private")
    assert path.read_text(encoding="utf-8") == "private"
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
