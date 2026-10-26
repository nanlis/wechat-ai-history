import builtins
import importlib
import sys
import json
import os
import stat
import types


def test_import_does_not_require_pilk(monkeypatch):
    original_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "pilk":
            raise ModuleNotFoundError("No module named 'pilk'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)
    sys.modules.pop("transcribe_db", None)
    sys.modules.pop("voice_decode", None)
    module = importlib.import_module("transcribe_db")
    assert callable(module.transcribe_server_ids)


def test_macos_uses_mlx(monkeypatch):
    import transcribe_db
    monkeypatch.setattr(transcribe_db, "IS_MACOS", True)
    called = {}
    fake = types.ModuleType("mlx_whisper")
    fake.transcribe = lambda wav, path_or_hf_repo, language: called.update({"mlx": True}) or {"text": "你好"}
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
    assert transcribe_db._transcribe_wav("/tmp/x.wav") == "你好"
    assert called.get("mlx") is True


def test_windows_uses_faster_whisper(monkeypatch):
    import transcribe_db
    monkeypatch.setattr(transcribe_db, "IS_MACOS", False)
    monkeypatch.setattr(transcribe_db, "_fw_model", None, raising=False)

    class _Seg:
        def __init__(self, t): self.text = t

    class _Model:
        def __init__(self, *a, **k): pass
        def transcribe(self, wav, language): return ([_Seg("你"), _Seg("好")], None)

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = _Model
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    assert transcribe_db._transcribe_wav("/tmp/x.wav") == "你好"


def test_fetch_blobs_handles_int_ids(win_backend):
    import transcribe_db
    out = transcribe_db._fetch_blobs([1002])   # native int, like real Windows query output
    assert "1002" in out                        # str-keyed, blob fetched without TypeError


def test_voice_cache_is_private(monkeypatch, tmp_path):
    import transcribe_db
    path = tmp_path / "voice_cache.json"
    monkeypatch.setattr(transcribe_db, "VOICE_CACHE", str(path))
    monkeypatch.setattr(transcribe_db, "SKILL_DIR", str(tmp_path))
    transcribe_db._save_voice_cache({"1": "你好"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"1": "你好"}
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
