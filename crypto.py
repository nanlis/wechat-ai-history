"""Key loading and PBKDF2 derivation."""
import hashlib
import json
import os
import tempfile

import config

_CACHE_FILE = os.path.join(config.SKILL_DIR, "all_keys.json")
_derived_cache: dict[str, str] = {}
_cache_loaded = False


def load_key() -> str:
    """Load the raw key from key.txt (64 hex chars = 32 bytes)."""
    if not os.path.exists(config.KEY_FILE):
        raise FileNotFoundError(
            f"密钥文件不存在: {config.KEY_FILE}\n"
            "运行 scripts/macos/extract_key.sh 提取密钥"
        )
    with open(config.KEY_FILE, encoding="ascii") as f:
        key = f.read().strip()
    try:
        raw = bytes.fromhex(key)
    except ValueError as exc:
        raise ValueError(f"密钥格式无效: {config.KEY_FILE}") from exc
    if len(raw) != 32 or len(key) != 64:
        raise ValueError(f"密钥必须是 64 个十六进制字符: {config.KEY_FILE}")
    return key.lower()


def derive_key(raw_key_hex: str, db_path: str) -> str:
    """Derive per-DB encryption key from raw key + DB file salt.

    WeChat uses: PBKDF2-HMAC-SHA512(raw_key, db_salt[0:16], 256000) → 32 bytes
    SQLCipher then uses the derived key with kdf_iter=1 (no further PBKDF2).
    PBKDF2 costs ~200ms per call, so derived keys are cached in all_keys.json
    keyed by (raw key fingerprint, salt) — salt change invalidates automatically.
    """
    global _cache_loaded
    with open(db_path, "rb") as f:
        salt = f.read(16)
    raw_key = bytes.fromhex(raw_key_hex)
    cache_key = hashlib.sha256(raw_key).hexdigest()[:16] + ":" + salt.hex()
    if not _cache_loaded:
        _cache_loaded = True
        try:
            with open(_CACHE_FILE, encoding="utf-8") as f:
                _derived_cache.update(json.load(f))
        except (OSError, ValueError):
            pass
    if cache_key in _derived_cache:
        return _derived_cache[cache_key]
    derived = hashlib.pbkdf2_hmac("sha512", raw_key, salt, 256000, dklen=32).hex()
    _derived_cache[cache_key] = derived
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="all_keys.", dir=config.SKILL_DIR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_derived_cache, f, indent=1)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, _CACHE_FILE)
    except OSError:
        try:
            os.unlink(tmp_path)
        except (OSError, UnboundLocalError):
            pass
    return derived
