import importlib.util
import os
from pathlib import Path
import stat


SCRIPT = Path(__file__).parents[1] / "scripts" / "common" / "migrate_private_state.py"
SPEC = importlib.util.spec_from_file_location("wechat_state_migration", SCRIPT)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_migration_copies_missing_private_files_without_overwriting(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    target = tmp_path / "target"
    first.mkdir()
    second.mkdir()
    target.mkdir()
    (first / "key.txt").write_text("11" * 32, encoding="ascii")
    (first / "contacts.json").write_text('{"source": "first"}', encoding="utf-8")
    (second / "contacts.json").write_text('{"source": "second"}', encoding="utf-8")
    (target / "key.txt").write_text("22" * 32, encoding="ascii")

    result = migration.migrate_private_state(
        target,
        [first, second],
        link_decrypted=False,
    )

    assert (target / "key.txt").read_text(encoding="ascii") == "22" * 32
    assert (target / "contacts.json").read_text(encoding="utf-8") == '{"source": "first"}'
    assert result["copied"] == ["contacts.json"]
    assert "key.txt" in result["skipped"]
    if os.name != "nt":
        assert stat.S_IMODE((target / "contacts.json").stat().st_mode) == 0o600


def test_migration_links_existing_decrypted_directory_on_posix(tmp_path):
    if os.name == "nt":
        return
    source = tmp_path / "source"
    target = tmp_path / "target"
    decrypted = source / "decrypted"
    decrypted.mkdir(parents=True)
    (decrypted / "marker").write_text("local", encoding="utf-8")

    result = migration.migrate_private_state(target, [source])

    assert result["linked"] == ["decrypted"]
    assert (target / "decrypted").is_symlink()
    assert (target / "decrypted" / "marker").read_text(encoding="utf-8") == "local"
