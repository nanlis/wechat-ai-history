"""Audit the repository for personal / machine-specific data.

Runs two sweeps:

  * **worktree** -- every tracked-looking file on disk
  * **history**  -- every blob in every commit, plus every commit message

The sensitive patterns are assembled from fragments at import time so this
file does not itself become a leak (the guard test would otherwise flag it).

Usage:
    python scripts/common/scrub_audit.py            # worktree + history
    python scripts/common/scrub_audit.py --worktree # worktree only
    python scripts/common/scrub_audit.py --history  # history only
    python scripts/common/scrub_audit.py --json     # machine readable

Exit code 0 = clean, 1 = something was found.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

SKIP_DIRS = {".git", ".venv", "decrypted", "交付", "__pycache__", ".pytest_cache", "wechat_media"}
TEXT_SUFFIXES = {".py", ".sh", ".ps1", ".md", ".json", ".yml", ".yaml", ".txt", ".cfg", ".ini", ".toml"}


def _fragments(*parts: str) -> str:
    """Join fragments so no complete sensitive token exists in this file."""
    return "".join(parts)


def _chars(codepoints: tuple[int, ...]) -> str:
    """Build a probe string from code points.

    Reading the characters off a numeric list keeps this file free of any
    literal copy of the thing being hunted -- a readable probe string in the
    source would itself be a leak, and would also trip the audit.
    """
    return "".join(chr(cp) for cp in codepoints)


# Probe strings that must never appear verbatim in the repository. Built from
# code points so the source file does not contain them.
_CONTACT_PROBE = _chars((0x7AEF, 0x54E5))  # a personal contact nickname
_GROUP_PROBE = _chars((0x63A8, 0x7279, 0x8FD0, 0x8425, 0x7FA4))  # a group name
_VM_HOST_PROBE = _chars((0x96F6, 0x4E5D, 0x4E09, 0x53F7, 0x865A, 0x62DF, 0x673A))
_SSH_USER_PROBE = _chars((0x63, 0x6C, 0x61, 0x75, 0x64, 0x65))


# (label, pattern) -- assembled from fragments on purpose.
_PATTERNS: list[tuple[str, str]] = [
    ("account id (active)", _fragments(r"wxid_", r"[a-z0-9]{16}")),
    ("chatroom id", _fragments(r"\b\d{9,10}", r"@chatroom\b")),
    # Any drive-letter path that reaches into a user profile and then into
    # the wechat data dir -- the profile segment may be any name (Chinese
    # included), not just the English "Documents". Placeholder profiles
    # ("$USERNAME", "<user>", ...) never match: the segment class excludes
    # the characters those placeholders are built from.
    ("real data path", _fragments(r"[A-Za-z]:[\\/]+Users[\\/]+", r"(?![<$*][^\\\"'\s]*[\\/])(?!\$)[^\\\"'/\s<>*]+[\\/]+(?:Documents|Document)[\\/]+", r"xwechat_files")),
    ("wechat install path", _fragments(r"[A-Za-z]:[\\/]+", r"Weixin[\\/]")),
    ("project path", _fragments(r"[A-Za-z]:[\\/]+", r"Projects[\\/]+wxdec")),
    ("windows user path", _fragments(r"C:[\\/]+Users[\\/]+", r"[A-Za-z]+[\\/]+(?:\.workbuddy|\.codex)")),
    # A non-placeholder profile segment followed by the wechat data dir,
    # regardless of what the parent folder is called.
    ("named user path", _fragments(r"C:[\\/]+Users[\\/]+", r"(?![<$*][^\\\"'\s]*[\\/])(?!\$)[^\\\"'/\s<>*]+[\\/]+(?:Documents|Document)[\\/]+", r"xwechat_files")),
    ("personal handle", _fragments(r"@", r"[Ss]oran", r"lan")),
    ("contact name", _CONTACT_PROBE),
    # The group-name probe is built from code points for the same reason:
    # a literal match string in this file would itself be the leak.
    ("group name fragment", _GROUP_PROBE),
    ("phone number", r"\b1[3-9]\d{9}\b"),
    # Hostname of the throwaway VM used during the original spike. It is a
    # proper noun of the author's own machine, not an example.
    ("vm hostname", _VM_HOST_PROBE),
    # SSH account name used on that VM.
    ("ssh account", _fragments(r"user `", _SSH_USER_PROBE, "`")),
]
_COMPILED = [(label, re.compile(rx)) for label, rx in _PATTERNS]

# Ids that are obviously synthetic fixtures and must not trip the audit.
_ALLOWED = (
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
    # Assembled from fragments so this allowlist is not itself a leak: the
    # guard test rejects any literal ``wxid_`` followed by 6+ alphanumerics,
    # so the prefix itself has to be split.
    "wxid" + "_zqx9k2" + "m4newcomer",
    "wxid" + "_sender001",
    "@chatroom",
)

# Files whose contents are secrets or raw dumps -- never ship them, and never
# scan them (a hex key can accidentally look like a phone number).
_SKIP_FILES = {
    "key_windows.txt",
    "key.txt",
    "contacts.json",
}


def _allowed(text: str) -> bool:
    return any(token in text for token in _ALLOWED)


def _git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.stdout


def _worktree_files() -> list[str]:
    out = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name in _SKIP_FILES:
                continue
            path = os.path.join(root, name)
            if os.path.splitext(name)[1].lower() in TEXT_SUFFIXES:
                out.append(path.replace("\\", "/").lstrip("./"))
    return sorted(out)


def _sweep_text(label_scope: str, where: str, text: str, findings: dict) -> None:
    for label, rx in _COMPILED:
        for match in rx.finditer(text):
            token = match.group(0)
            if label.startswith("account id") and _allowed(token):
                continue
            if label == "chatroom id" and _allowed(token):
                continue
            findings.setdefault(label, []).append(f"{label_scope}:{where}: {token}")


def audit_worktree() -> dict:
    findings: dict[str, list[str]] = defaultdict(list)
    for path in _worktree_files():
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        _sweep_text("worktree", path, text, findings)
    return findings


def audit_history() -> dict:
    findings: dict[str, list[str]] = defaultdict(list)

    commits = _git(["rev-list", "--all"]).split()
    blob_paths: dict[str, set[str]] = defaultdict(set)

    for commit in commits:
        for line in _git(["ls-tree", "-r", commit]).splitlines():
            if not line.strip():
                continue
            meta, _, path = line.partition("\t")
            fields = meta.split()
            if len(fields) >= 3 and fields[1] == "blob":
                blob_paths[fields[2]].add(path)

    for blob, paths in blob_paths.items():
        text = _git(["cat-file", "-p", blob])
        for path in sorted(paths):
            _sweep_text("history", f"{blob[:8]}:{path}", text, findings)

    for commit in commits:
        message = _git(["log", "-1", "--format=%B", commit])
        _sweep_text("message", commit[:8], message, findings)

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", action="store_true", help="only scan files on disk")
    parser.add_argument("--history", action="store_true", help="only scan git history")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    findings: dict[str, list[str]] = defaultdict(list)

    if not args.history:
        for key, values in audit_worktree().items():
            findings[key].extend(values)
    if not args.worktree:
        for key, values in audit_history().items():
            findings[key].extend(values)

    total = sum(len(v) for v in findings.values())

    if args.json:
        print(json.dumps({k: v for k, v in findings.items()}, ensure_ascii=False, indent=2))
        return 1 if total else 0

    if not findings:
        print("clean -- no personal or machine-specific data found")
        return 0

    print(f"FOUND {total} issue(s):\n")
    for label, values in sorted(findings.items()):
        print(f"  [{label}] {len(values)} hit(s)")
        for value in values[:25]:
            print(f"      {value}")
        if len(values) > 25:
            print(f"      ... +{len(values) - 25} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())
