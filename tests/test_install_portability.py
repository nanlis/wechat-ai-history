"""Install-time portability checks.

A fresh machine has different paths than the machine that authored the repo.
These tests pin the properties that keep `bash install.sh` working there:
the skill must resolve its runtime dynamically, and the manual it points at
must actually ship with it.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_install_script_links_the_whole_repo_as_skill():
    """Linking the repo keeps references/ and scripts/ reachable.

    Copying a single SKILL.md would ship a manual whose referenced pages
    (references/*.md, scripts/common/*.py) are missing from the install.
    """
    install = _read("install.sh")

    assert 'link_dir "$SKILL_SRC" "$REPO_DIR"' in install
    assert 'cp "$REPO_DIR/skill/SKILL.md" "$SKILL_SRC/SKILL.md"' not in install


def test_install_script_uses_junction_not_ln_s():
    """`ln -s` silently fails on Windows without symlink privileges.

    Git Bash runs with MSYS=winsymlinks:nativestrict, so `ln -s` needs
    SeCreateSymbolicLinkPrivilege.  Without it the command errors out, and
    because install.sh used to swallow that error, an empty directory was
    left behind and the skill silently resolved to nothing.
    """
    install = _read("install.sh")

    assert "New-Item -ItemType Junction" in install
    assert 'ln -sfn "$SKILL_SRC"' not in install
    assert 'ln -s "$REPO_DIR"' not in install


def test_install_script_avoids_cmd_exe_mklink():
    """`cmd.exe /c "mklink /J ..."` loses the /J flag to quote mangling.

    Git Bash hands the command to cmd.exe after its own quote processing,
    so cmd sees `/J` as a path argument instead of the junction flag.
    Comments and the manual fallback hint may still mention it; what must
    not exist is an actual invocation.
    """
    install = _read("install.sh")

    for line in install.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "cmd.exe /c" not in stripped, f"不应直接调 cmd.exe: {stripped}"
        assert "mklink" not in stripped, f"不应直接调 mklink: {stripped}"


def test_install_script_ships_the_reference_pages():
    install = _read("install.sh")

    assert "references/" in install


def test_windows_skill_has_no_machine_specific_paths():
    """Hardcoded paths from the authoring machine break on a fresh install."""
    skill = _read("skill/SKILL.md")

    stale = re.findall(r"[A-Za-z]:[\\/](?:Document|Projects|Users)[^\s`\"']*", skill)

    assert not stale, f"skill/SKILL.md 仍含本机硬编码路径: {stale[:3]}"


def test_windows_skill_resolves_paths_at_runtime():
    skill = _read("skill/SKILL.md")

    assert "mcp.json" in skill, "应从 MCP 配置解析真实路径"
    assert "$REPO" in skill and "$DATA_ROOT" in skill


def test_top_level_skill_references_exist():
    """Every references/ page the entry SKILL.md points at must ship."""
    top = _read("SKILL.md")

    for ref in sorted(set(re.findall(r"references/([a-z-]+\.md)", top))):
        assert (ROOT / "references" / ref).exists(), f"缺少 references/{ref}"


def test_install_script_covers_both_skill_variants():
    """The cross-platform skill and the Windows field notes are two entries."""
    install = _read("install.sh")

    assert "wechat-decrypt" in install
    assert "wechat-local-history" in install
