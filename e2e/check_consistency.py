#!/usr/bin/env python3
"""Compare the development checkout with one installed runtime copy."""

import argparse
import hashlib
import os
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
SOURCE_EXTENSIONS = {".py", ".md", ".sh", ".ps1", ".js", ".yaml"}
RUNTIME_TOP_LEVEL = {
    "LICENSE",
    "README.md",
    "README_ZH.md",
    "SKILL.md",
    "appmsg.py",
    "config.py",
    "contacts.py",
    "crypto.py",
    "db.py",
    "message.py",
    "server.py",
    "setup.ps1",
    "setup.sh",
}
RUNTIME_DIRECTORIES = (
    "agents",
    "references",
    "scripts/common",
    "scripts/macos",
    "scripts/windows",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_files() -> list[Path]:
    files = [REPO_DIR / name for name in sorted(RUNTIME_TOP_LEVEL)]
    for directory in RUNTIME_DIRECTORIES:
        root = REPO_DIR / directory
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix in SOURCE_EXTENSIONS
            and "__pycache__" not in path.parts
        )
    return sorted(files)


def _default_skill_dir() -> Path | None:
    """Find an installed runtime copy, skipping paths that resolve into the checkout.

    Install locations are often symlinks back to the checkout, and comparing the
    checkout with itself always passes. Those candidates are skipped.
    """
    env = os.environ.get("WECHAT_SKILL_DIR")
    candidates = ([Path(env).expanduser()] if env else []) + [
        Path("~/.grok/skills/wechat-decrypt").expanduser(),
        Path("~/.kimi-code/skills/wechat-decrypt").expanduser(),
        Path("~/.agents/skills/wechat-decrypt").expanduser(),
        Path("~/.codex/skills/wechat-decrypt").expanduser(),
        Path("~/.claude/skills/wechat-decrypt").expanduser(),
    ]
    for path in candidates:
        if not path.is_dir():
            continue
        resolved = path.resolve()
        if resolved == REPO_DIR or REPO_DIR in resolved.parents:
            continue
        return path
    return None


def check_runtime(skill_dir: Path) -> list[str]:
    drift = []
    for source in _runtime_files():
        relative = source.relative_to(REPO_DIR)
        target = skill_dir / relative
        if not target.is_file():
            drift.append(f"missing: {relative}")
        elif _digest(source) != _digest(target):
            drift.append(f"different: {relative}")
    return drift


def check_vendored(wecom_repo: Path) -> list[str]:
    pairs = (
        (REPO_DIR / "scripts/common/read_doc.py", wecom_repo / "decrypt/read_doc.py"),
        (REPO_DIR / "scripts/common/crypto_backend.py", wecom_repo / "decrypt/crypto_backend.py"),
    )
    drift = []
    for left, right in pairs:
        if not right.is_file():
            drift.append(f"vendored target missing: {right}")
        elif _digest(left) != _digest(right):
            drift.append(f"vendored different: {left.name}")
    return drift


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", type=Path, help="Installed wechat-decrypt directory")
    parser.add_argument(
        "--with-vendored",
        action="store_true",
        help="Also compare shared files with the sibling wecom-agent repository",
    )
    parser.add_argument("--wecom-repo", type=Path, help="Override the wecom-agent checkout")
    args = parser.parse_args()

    skill_dir = args.skill_dir.expanduser().resolve() if args.skill_dir else _default_skill_dir()
    if skill_dir is None or not skill_dir.is_dir():
        print("FAIL: no installed skill found; pass --skill-dir or set WECHAT_SKILL_DIR")
        return 1

    drift = check_runtime(skill_dir)
    if args.with_vendored:
        wecom_repo = (
            args.wecom_repo.expanduser().resolve()
            if args.wecom_repo
            else Path(os.environ.get("WECOM_REPO", REPO_DIR.parent / "wecom-agent")).expanduser()
        )
        drift.extend(check_vendored(wecom_repo))

    if drift:
        print(f"FAIL: {len(drift)} consistency gap(s) against {skill_dir}")
        for item in drift:
            print(f"  {item}")
        return 1
    print(f"OK: {len(_runtime_files())} runtime files match {skill_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
