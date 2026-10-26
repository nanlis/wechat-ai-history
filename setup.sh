#!/bin/bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd -P)"
VENV_DIR="$SKILL_DIR/.venv"
USER_SKILL_DIR="$HOME/.agents/skills/wechat-decrypt"
LEGACY_SKILL_DIR="$HOME/.codex/skills/wechat-decrypt"
BACKUP_ROOT="$HOME/.agents/backups"
UPGRADE=0
WITH_VOICE=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --upgrade) UPGRADE=1 ;;
        --with-voice) WITH_VOICE=1 ;;
        *)
            echo "Usage: bash setup.sh [--upgrade] [--with-voice]"
            exit 2
            ;;
    esac
    shift
done

if [[ "$(uname)" != "Darwin" ]]; then
    echo "此脚本仅适用于 macOS。Windows 请用: powershell -File setup.ps1"
    exit 1
fi

echo "=== WeChat Decrypt 安装（macOS / Codex）==="
echo "目录: $SKILL_DIR"
echo ""

BOOTSTRAP_PYTHON="$(command -v python3 || true)"
if [ -z "$BOOTSTRAP_PYTHON" ]; then
    echo "ERROR: 需要 Python 3.10+"
    exit 1
fi
if ! "$BOOTSTRAP_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "ERROR: 需要 Python 3.10+"
    exit 1
fi

if [ -e "$USER_SKILL_DIR" ] || [ -L "$USER_SKILL_DIR" ]; then
    if ! "$BOOTSTRAP_PYTHON" -c 'import os,sys; raise SystemExit(0 if os.path.samefile(sys.argv[1], sys.argv[2]) else 1)' "$USER_SKILL_DIR" "$SKILL_DIR" 2>/dev/null; then
        if [ "$UPGRADE" -ne 1 ]; then
            echo "ERROR: $USER_SKILL_DIR 指向另一个安装。"
            echo "确认要切换到当前仓库后，运行: bash setup.sh --upgrade"
            exit 1
        fi
    fi
fi

echo "[1/6] 创建隔离 Python 环境..."
"$BOOTSTRAP_PYTHON" -m venv "$VENV_DIR"
PYTHON="$VENV_DIR/bin/python"
echo "  $PYTHON ($("$PYTHON" --version))"

echo "[2/6] 检查 SQLCipher..."
if ! command -v sqlcipher &>/dev/null; then
    if ! command -v brew &>/dev/null; then
        echo "ERROR: 未找到 sqlcipher 或 Homebrew。先安装 Homebrew，再重跑 setup.sh。"
        exit 1
    fi
    brew install sqlcipher
fi
echo "  sqlcipher: $(command -v sqlcipher)"

echo "[3/6] 安装 Python 依赖..."
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install "mcp[cli]>=1.0,<2" frida-tools zstandard cryptography requests
if [ "$WITH_VOICE" -eq 1 ] && [ "$(uname -m)" = "arm64" ]; then
    "$PYTHON" -m pip install pilk mlx-whisper
elif [ "$WITH_VOICE" -eq 1 ]; then
    echo "  Intel Mac 暂不安装语音转写后端；查询和普通导出仍可用。"
else
    echo "  已跳过可选语音依赖；需要时运行: bash setup.sh --with-voice"
fi

echo "[4/6] 迁移私有状态并注册用户 Skill..."
mkdir -p "$(dirname "$USER_SKILL_DIR")"
BACKUP_SKILL_DIR=""
NEW_LINK_CREATED=0
if [ -e "$USER_SKILL_DIR" ] || [ -L "$USER_SKILL_DIR" ]; then
    if "$BOOTSTRAP_PYTHON" -c 'import os,sys; sys.exit(0 if os.path.samefile(sys.argv[1], sys.argv[2]) else 1)' "$USER_SKILL_DIR" "$SKILL_DIR" 2>/dev/null; then
        echo "  已链接: $USER_SKILL_DIR"
    else
        mkdir -p "$BACKUP_ROOT"
        BACKUP_SKILL_DIR="$BACKUP_ROOT/wechat-decrypt.$(date +%Y%m%d%H%M%S).$$"
        mv "$USER_SKILL_DIR" "$BACKUP_SKILL_DIR"
        ln -s "$SKILL_DIR" "$USER_SKILL_DIR"
        NEW_LINK_CREATED=1
        echo "  旧安装已备份: $BACKUP_SKILL_DIR"
        echo "  已链接: $USER_SKILL_DIR -> $SKILL_DIR"
    fi
else
    ln -s "$SKILL_DIR" "$USER_SKILL_DIR"
    NEW_LINK_CREATED=1
    echo "  已链接: $USER_SKILL_DIR -> $SKILL_DIR"
fi

MIGRATION_ARGS=(
    --target "$SKILL_DIR"
)
if [ -n "$BACKUP_SKILL_DIR" ]; then
    MIGRATION_ARGS+=(--source "$BACKUP_SKILL_DIR")
fi
MIGRATION_ARGS+=(--source "$LEGACY_SKILL_DIR")
if ! "$PYTHON" "$SKILL_DIR/scripts/common/migrate_private_state.py" "${MIGRATION_ARGS[@]}"; then
    echo "ERROR: 私有状态迁移失败，正在恢复用户 Skill 链接。"
    if [ "$NEW_LINK_CREATED" -eq 1 ] && [ -L "$USER_SKILL_DIR" ]; then
        rm "$USER_SKILL_DIR"
    fi
    if [ -n "$BACKUP_SKILL_DIR" ] && [ ! -e "$USER_SKILL_DIR" ]; then
        mv "$BACKUP_SKILL_DIR" "$USER_SKILL_DIR"
    fi
    exit 1
fi

echo "[5/6] 验证基础依赖..."
"$PYTHON" -c "import mcp, frida, zstandard, cryptography; print('  core dependencies OK')"
if [ "$WITH_VOICE" -eq 1 ] && [ "$(uname -m)" = "arm64" ]; then
    "$PYTHON" -c "import pilk, mlx_whisper; print('  voice dependencies OK')"
fi

echo "[6/6] 注册 Codex MCP Server..."
if command -v codex &>/dev/null; then
    codex mcp remove wechat >/dev/null 2>&1 || true
    codex mcp add wechat -- "$PYTHON" "$SKILL_DIR/server.py"
    echo "  Codex MCP 已注册"
else
    echo "  WARN: 未找到 Codex CLI；可先用 query.py，安装 Codex 后重跑 setup.sh。"
fi

echo ""
echo "=== 安装完成 ==="
if [ -f "$SKILL_DIR/key.txt" ]; then
    echo "已保留现有密钥。运行自检后重启 Codex:"
    echo "  $PYTHON scripts/common/doctor.py"
else
    echo "首次提取步骤:"
    echo "  1. sudo codesign --force --deep --sign - /Applications/WeChat.app"
    echo "  2. bash scripts/macos/extract_key.sh"
    echo "  3. $PYTHON scripts/common/doctor.py"
    echo "  4. 重启 Codex"
    echo ""
    echo "只在提取密钥时重签名；完成后按 references/macos.md 恢复官方签名。"
fi
