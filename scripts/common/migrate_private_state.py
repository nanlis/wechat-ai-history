#!/usr/bin/env python3
"""Migrate private wechat-decrypt state without overwriting existing data."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


PRIVATE_FILES = (
    "key.txt",
    "key_windows.txt",
    "contacts.json",
    "all_keys.json",
    "voice_cache.json",
)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _copy_private_file(source: Path, target: Path) -> bool:
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary_path = Path(temporary)
    try:
        with source.open("rb") as input_file, os.fdopen(fd, "wb") as output_file:
            shutil.copyfileobj(input_file, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.chmod(temporary_path, 0o600)
        try:
            os.link(temporary_path, target)
        except FileExistsError:
            return False
        return True
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    finally:
        temporary_path.unlink(missing_ok=True)


def _link_directory(source: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(source)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or result.stdout.strip())
        return
    target.symlink_to(source.resolve(), target_is_directory=True)


def migrate_private_state(
    target: Path,
    sources: list[Path],
    link_decrypted: bool = True,
) -> dict:
    target = target.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    result = {"copied": [], "linked": [], "skipped": [], "warnings": []}

    for source in sources:
        source = source.expanduser()
        if not source.is_dir() or _same_path(source, target):
            continue
        for name in PRIVATE_FILES:
            source_file = source / name
            target_file = target / name
            if not source_file.is_file():
                continue
            if target_file.exists() or target_file.is_symlink():
                result["skipped"].append(name)
                continue
            try:
                if _copy_private_file(source_file, target_file):
                    result["copied"].append(name)
                else:
                    result["skipped"].append(name)
            except OSError as exc:
                result["warnings"].append(f"{name}: {exc}")

        source_decrypted = source / "decrypted"
        target_decrypted = target / "decrypted"
        if (
            link_decrypted
            and source_decrypted.is_dir()
            and not target_decrypted.exists()
            and not target_decrypted.is_symlink()
        ):
            try:
                _link_directory(source_decrypted, target_decrypted)
                result["linked"].append("decrypted")
            except OSError as exc:
                result["warnings"].append(f"decrypted: {exc}")

    result["copied"] = sorted(set(result["copied"]))
    result["linked"] = sorted(set(result["linked"]))
    result["skipped"] = sorted(set(result["skipped"]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument("--no-link-decrypted", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = migrate_private_state(
        args.target,
        args.source,
        link_decrypted=not args.no_link_decrypted,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for name in result["copied"]:
            print(f"  migrated private file: {name}")
        for name in result["linked"]:
            print(f"  linked private directory: {name}")
        for warning in result["warnings"]:
            print(f"  WARN: {warning}")
        if not result["copied"] and not result["linked"] and not result["warnings"]:
            print("  no legacy private state to migrate")
    return 0 if not result["warnings"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
