#!/usr/bin/env python3
"""Read-only environment diagnostics for wechat-decrypt."""

import argparse
import glob
import importlib.util
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass


SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def _has_module(*names: str) -> bool:
    return any(importlib.util.find_spec(name) is not None for name in names)


def _voice_backend_check(system: str) -> Check:
    if system == "Darwin":
        module = "mlx_whisper"
        label = "mlx-whisper"
        fix = "Run: bash setup.sh --with-voice"
    else:
        module, label, fix = (
            "faster_whisper",
            "faster-whisper",
            "Run: powershell -File setup.ps1 -WithVoice",
        )
    available = _has_module(module)
    return Check(
        "voice-backend",
        "ok" if available else "warn",
        (
            f"{label} available"
            if available
            else f"{label} not installed; ordinary export still works"
        ),
        "" if available else fix,
    )


def _mcp_api_check() -> Check:
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
    except (ImportError, ModuleNotFoundError) as exc:
        return Check(
            "dependency:mcp-api",
            "warn",
            f"FastMCP v1 API unavailable: {exc}",
            "Run setup again to install mcp>=1,<2",
        )
    return Check("dependency:mcp-api", "ok", "FastMCP v1 API available")


def _mac_key_database_check(skill_dir: str, data_dirs: list[str]) -> Check:
    databases = sorted(
        database
        for data_dir in data_dirs
        for database in glob.glob(
            os.path.join(data_dir, "message", "message_[0-9].db")
        )
        if os.path.isfile(database)
    )
    if not databases:
        return Check(
            "key-database",
            "fail",
            "No active message database available for key validation",
            "Open and sign in to WeChat",
        )
    probe = (
        "import crypto,db,sys; "
        "raise SystemExit(0 if db.test_key(crypto.load_key(), sys.argv[1]) else 1)"
    )
    for database in databases:
        try:
            result = subprocess.run(
                [sys.executable, "-c", probe, database],
                cwd=skill_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=12,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return Check(
                "key-database",
                "ok",
                "Current key opens a message database",
            )
    return Check(
        "key-database",
        "fail",
        "Current key cannot open any message database",
        "Read references/macos.md and re-extract the key",
    )


def _read_key(path: str) -> tuple[bool, str]:
    try:
        with open(path, encoding="ascii") as f:
            value = f.read().strip()
    except OSError:
        return False, "missing"
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return False, "invalid format"
    return True, "present (64 hex chars)"


def _key_check(path: str, reference: str, check_permissions: bool) -> Check:
    key_ok, detail = _read_key(path)
    if not key_ok:
        return Check("key", "fail", detail, f"Read {reference} and extract the key")
    if check_permissions:
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except OSError:
            mode = 0
        if mode & 0o077:
            return Check(
                "key",
                "warn",
                f"{detail}; permissions are {mode:04o}, expected 0600",
                "Restrict the key file to owner read/write only",
            )
    return Check("key", "ok", detail)


def _same_path(left: str, right: str) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _codex_mcp_check(skill_dir: str) -> Check:
    codex = shutil.which("codex")
    if not codex:
        return Check("mcp", "warn", "Codex CLI not found", "Install Codex or use query.py directly")
    try:
        result = subprocess.run(
            [codex, "mcp", "get", "wechat"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check("mcp", "warn", f"Could not inspect MCP registration: {exc}", "Run setup again")
    if result.returncode != 0 or "enabled: true" not in result.stdout:
        return Check("mcp", "warn", "wechat MCP is not enabled", "Run setup again")
    expected = os.path.join(skill_dir, "server.py")
    args_line = next(
        (line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.strip().startswith("args:")),
        "",
    )
    registered = args_line.strip('"')
    if registered and not _same_path(registered, expected):
        return Check("mcp", "warn", f"MCP points to another checkout: {registered}", "Run setup from this checkout")
    return Check("mcp", "ok", "wechat MCP is enabled")


def _skill_discovery_check(skill_dir: str) -> Check:
    official = os.path.expanduser("~/.agents/skills/wechat-decrypt")
    if _same_path(official, skill_dir):
        return Check("skill", "ok", f"Discoverable at {official}")
    return Check(
        "skill",
        "warn",
        f"Current checkout is not linked at {official}",
        "Run setup again to create the user-skill link",
    )


def collect_checks(system: str | None = None, skill_dir: str = SKILL_DIR) -> list[Check]:
    system = system or platform.system()
    checks = [
        Check(
            "platform",
            "ok" if system in {"Darwin", "Windows"} else "fail",
            system,
            "Use macOS or Windows" if system not in {"Darwin", "Windows"} else "",
        ),
        Check(
            "python",
            "ok" if sys.version_info >= (3, 10) else "fail",
            platform.python_version(),
            "Install Python 3.10+" if sys.version_info < (3, 10) else "",
        ),
        _skill_discovery_check(skill_dir),
        _codex_mcp_check(skill_dir),
    ]

    if system not in {"Darwin", "Windows"}:
        return checks

    if not _has_module("mcp"):
        checks.append(
            Check("dependency:mcp", "warn", "Python package missing", "Run setup again")
        )
    else:
        checks.append(Check("dependency:mcp", "ok", "Python package available"))
        checks.append(_mcp_api_check())

    if _has_module("zstd", "zstandard", "pyzstd"):
        checks.append(Check("dependency:zstd", "ok", "Message decompressor available"))
    else:
        checks.append(
            Check(
                "dependency:zstd",
                "warn",
                "Long compressed messages cannot be decoded",
                "Run setup again",
            )
        )

    if system == "Darwin":
        sqlcipher = os.environ.get("WECHAT_SQLCIPHER_PATH") or shutil.which("sqlcipher")
        checks.append(
            Check(
                "sqlcipher",
                "ok" if sqlcipher else "fail",
                sqlcipher or "not found",
                "Install with Homebrew: brew install sqlcipher" if not sqlcipher else "",
            )
        )
        key_check = _key_check(
            os.path.join(skill_dir, "key.txt"),
            "references/macos.md",
            check_permissions=os.name != "nt",
        )
        checks.append(key_check)
        data_glob = os.path.expanduser(
            "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/*/db_storage"
        )
        data_dirs = glob.glob(data_glob)
        checks.append(
            Check(
                "database",
                "ok" if data_dirs else "fail",
                f"{len(data_dirs)} db_storage director{'y' if len(data_dirs) == 1 else 'ies'} found",
                "Open and sign in to WeChat" if not data_dirs else "",
            )
        )
        if key_check.status != "fail" and data_dirs:
            checks.append(_mac_key_database_check(skill_dir, data_dirs))
        if _has_module("frida"):
            checks.append(Check("dependency:frida", "ok", "Key extraction dependency available"))
        else:
            checks.append(Check("dependency:frida", "warn", "Key extraction dependency missing", "Run setup again"))
        model_dir = os.path.expanduser(
            "~/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-mlx"
        )
        checks.append(
            Check(
                "voice-model",
                "ok" if os.path.isdir(model_dir) else "warn",
                "cached" if os.path.isdir(model_dir) else "not cached; ordinary export still works",
                "Download only after user approval (~3 GB)" if not os.path.isdir(model_dir) else "",
            )
        )
        checks.append(_voice_backend_check(system))
    else:
        checks.append(
            _key_check(
                os.path.join(skill_dir, "key_windows.txt"),
                "references/windows.md",
                check_permissions=False,
            )
        )
        decrypted = glob.glob(
            os.path.join(skill_dir, "decrypted", "**", "message", "message_[0-9].db"),
            recursive=True,
        )
        checks.append(
            Check(
                "database",
                "ok" if decrypted else "fail",
                f"{len(decrypted)} decrypted message database(s) found",
                "Run scripts/windows/decrypt_all.py" if not decrypted else "",
            )
        )
        for module, label in (("frida", "frida"), ("Crypto", "pycryptodome")):
            checks.append(
                Check(
                    f"dependency:{label}",
                    "ok" if _has_module(module) else "warn",
                    "available" if _has_module(module) else "missing",
                    "Run setup again" if not _has_module(module) else "",
                )
            )
        hf_cache = (
            os.path.expanduser(os.environ["HF_HUB_CACHE"])
            if os.environ.get("HF_HUB_CACHE")
            else os.path.join(
                os.path.expanduser(os.environ.get("HF_HOME", "~/.cache/huggingface")),
                "hub",
            )
        )
        model_dir = os.path.join(hf_cache, "models--Systran--faster-whisper-large-v3")
        checks.append(
            Check(
                "voice-model",
                "ok" if os.path.isdir(model_dir) else "warn",
                "cached" if os.path.isdir(model_dir) else "not cached; ordinary export still works",
                "Download only after user approval (~3 GB)" if not os.path.isdir(model_dir) else "",
            )
        )
        checks.append(_voice_backend_check(system))

    return checks


def _human(checks: list[Check]) -> str:
    labels = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
    lines = [f"[{labels[item.status]:4}] {item.name}: {item.detail}" for item in checks]
    lines.extend(f"       fix: {item.fix}" for item in checks if item.fix)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose a wechat-decrypt installation")
    parser.add_argument("--json", action="store_true", help="Output structured JSON")
    args = parser.parse_args()
    checks = collect_checks()
    ok = not any(item.status == "fail" for item in checks)
    if args.json:
        print(json.dumps({"ok": ok, "checks": [asdict(item) for item in checks]}, ensure_ascii=False, indent=2))
    else:
        print(_human(checks))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
