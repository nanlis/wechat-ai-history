#!/usr/bin/env bash
# ============================================================================
#  微信聊天记录 → AI 能力  一键安装（全新 Windows 电脑）
#
#  用法：
#    bash install.sh                 # 自动探测数据目录
#    bash install.sh "D:\MyWeChat"   # 指定微信数据目录
#
#  做完之后：
#    1. 从桌面双击启动微信并登录
#    2. 运行 scripts/windows/refresh_key.sh 提取密钥 + 解密
#    3. 在 AI 客户端里说「把 XX 群的记录发给我」
# ============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[1;33m'; CYN='\033[0;36m'; DIM='\033[2m'; NC='\033[0m'
step() { echo -e "\n${CYN}==>${NC} ${YEL}$1${NC}"; }
ok()   { echo -e "    ${GRN}✓${NC} $1"; }
warn() { echo -e "    ${YEL}!${NC} $1"; }
die()  { echo -e "    ${RED}✗ $1${NC}"; exit 1; }

echo -e "${CYN}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   微信聊天记录 → AI 能力  安装程序           ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ---------------------------------------------------------------------------
step "1/7  检查运行环境"

# Python：优先 3.11+（frida 17 要求），退回系统 Python
PY=""
for cand in \
  "C:/Users/$USERNAME/.workbuddy/binaries/python/versions/3.13.12/python.exe" \
  "$(command -v python3 2>/dev/null || true)" \
  "$(command -v python 2>/dev/null || true)"; do
  [ -n "$cand" ] && [ -x "$cand" ] && PY="$cand" && break
done
[ -z "$PY" ] && die "找不到 Python。请先安装 Python 3.11+ 并加入 PATH。"

PY_VER=$("$PY" -c "import sys;print('%d.%d'%sys.version_info[:2])")
case "$PY_VER" in
  3.[1-9]|3.1[0-9]) ok "Python $PY_VER  ($PY)";;
  *) warn "Python $PY_VER 偏旧，frida 17 可能装不上（建议 3.11+）";;
esac

# Git Bash 自带管理员令牌时才能读进程内存
if powershell.exe -NoProfile -Command \
  "if(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){'Y'}else{'N'}" \
  2>/dev/null | grep -q Y; then
  ok "当前终端有管理员权限（读进程内存需要）"
else
  warn "当前终端【没有】管理员权限"
  echo -e "    ${DIM}提密钥会失败。请右键 Git Bash → 以管理员身份运行 → 重跑本脚本${NC}"
fi

# ---------------------------------------------------------------------------
step "2/7  探测微信安装位置"

WECHAT_DIR=""
for c in "/c/Program Files/Tencent/WeChat" "/c/Program Files (x86)/Tencent/WeChat" \
         "/d/Weixin" "/c/Program Files/Tencent/Weixin"; do
  [ -d "$c" ] && { WECHAT_DIR="$c"; break; }
done
# 找 Weixin.exe / WeChat.exe
EXE=""
for c in /c/Program\ Files/Tencent/WeChat /d/Weixin/* /c/Program\ Files/Tencent/Weixin; do
  for e in "$c"/Weixin.exe "$c"/WeChat.exe; do
    [ -f "$e" ] && { EXE="$e"; break; }
  done
  [ -n "$EXE" ] && break
done
[ -n "$EXE" ] && ok "微信程序: $EXE" || warn "没找到微信程序（请确认已安装微信 4.0+）"

# ---------------------------------------------------------------------------
step "3/7  探测微信数据目录"

DATA_ROOT="${1:-}"
if [ -z "$DATA_ROOT" ]; then
  for c in \
    "/d/Document/xwechat_files" "/c/Document/xwechat_files" "/c/Users/$USERNAME/Documents/xwechat_files" \
    "/e/Document/xwechat_files" "/d/xwechat_files" "/c/xwechat_files" \
    "/d/Documents/xwechat_files" "/c/Documents/xwechat_files"; do
    [ -d "$c" ] && DATA_ROOT="$c" && break
  done
fi
if [ -z "$DATA_ROOT" ]; then
  echo -e "    ${YEL}没自动找到数据目录。${NC}"
  echo -e "    ${DIM}常见位置：微信设置 → 文件管理 → 打开文件夹${NC}"
  echo -e "    ${DIM}格式示例：<数据根目录>${NC}"
  read -rp "    请手动输入数据目录（回车用默认 C:\Users\$USERNAME\Documents\xwechat_files）: " DATA_ROOT
  DATA_ROOT="${DATA_ROOT:-C:/Users/$USERNAME/Documents/xwechat_files}"
fi

# 验证：目录里应该有 db_storage
if [ -d "$DATA_ROOT" ] && ls -d "$DATA_ROOT"/*/db_storage >/dev/null 2>&1; then
  ACCOUNTS=$(ls -d "$DATA_ROOT"/*/db_storage 2>/dev/null | wc -l)
  ok "数据目录: $DATA_ROOT （$ACCOUNTS 个账号）"
else
  warn "目录结构异常：$DATA_ROOT 下没找到 */db_storage"
  echo -e "    ${DIM}如果微信数据在别处，请重新运行并指定路径${NC}"
fi

# ---------------------------------------------------------------------------
step "4/7  创建 Python 虚拟环境并安装依赖"

VENV_DIR="$REPO_DIR/.venv"
if [ -x "$VENV_DIR/Scripts/python.exe" ]; then
  ok "虚拟环境已存在，跳过"
else
  "$PY" -m venv "$VENV_DIR" || die "创建 venv 失败"
  ok "已创建 .venv"
fi

VPY="$VENV_DIR/Scripts/python.exe"
echo -e "    ${DIM}安装 frida / pycryptodome / zstandard / mcp（首次约 2-5 分钟）...${NC}"
"$VPY" -m pip install --upgrade pip -q
"$VPY" -m pip install -q -r requirements-windows.txt || {
  warn "依赖安装有报错，尝试逐个安装"
  "$VPY" -m pip install -q "mcp[cli]>=1.0,<2" pycryptodome zstandard
  "$VPY" -m pip install -q "frida>=17,<18" || die "frida 安装失败（检查 Python 版本 ≥3.11）"
}
"$VPY" -c "import frida, Crypto, mcp; print('  依赖自检通过')" && ok "依赖齐全"

# ---------------------------------------------------------------------------
step "5/7  创建目录联接（让工具能自动发现数据目录）"

LINK_DIR="C:/Users/$USERNAME/Documents/xwechat_files"
TARGET_WIN=$(echo "$DATA_ROOT" | sed 's|^/c/|C:/|; s|^/d/|D:/|; s|^/e/|E:/|; s|/|\\|g')
LINK_WIN="C:\\Users\\$USERNAME\\Documents\\xwechat_files"
if [ ! -e "$LINK_DIR" ]; then
  cmd.exe /c "mklink /J \"$LINK_WIN\" \"$TARGET_WIN\"" >/dev/null 2>&1 && \
    ok "已创建联接 Documents\\xwechat_files → $TARGET_WIN" || \
    warn "联接创建失败（不影响主流程，可忽略）"
else
  ok "联接已存在"
fi

# ---------------------------------------------------------------------------
step "6/7  注册 MCP server 到 WorkBuddy"

MCP_DIR="$HOME/.workbuddy"
MCP_FILE="$MCP_DIR/mcp.json"
mkdir -p "$MCP_DIR"
"$VPY" - "$MCP_FILE" "$REPO_DIR" "$DATA_ROOT" <<'PYEOF'
import json, os, sys
mcp_file, repo_dir, data_root = sys.argv[1], sys.argv[2], sys.argv[3]
repo_win = repo_dir.replace('/', '\\')
root_win = data_root.replace('/', '\\')

config = {}
if os.path.exists(mcp_file):
    try:
        with open(mcp_file, encoding='utf-8') as fh:
            config = json.load(fh)
    except Exception:
        config = {}
servers = config.setdefault('mcpServers', {})
servers['wechat-local'] = {
    'command': os.path.join(repo_win, '.venv', 'Scripts', 'python.exe'),
    'args': [os.path.join(repo_win, 'server.py')],
    'env': {'WECHAT_DATA_ROOT': root_win, 'PYTHONIOENCODING': 'utf-8'},
}
with open(mcp_file, 'w', encoding='utf-8') as fh:
    json.dump(config, fh, indent=2, ensure_ascii=False)
print('  已写入', mcp_file)
PYEOF
ok "MCP server: wechat-local"

# ---------------------------------------------------------------------------
step "7/7  安装 Skill 到各运行时"

SKILL_SRC="$HOME/.skills-manager/skills/wechat-local-history"
mkdir -p "$SKILL_SRC"
if [ -f "$REPO_DIR/skill/SKILL.md" ]; then
  cp "$REPO_DIR/skill/SKILL.md" "$SKILL_SRC/SKILL.md"
  ok "Skill 内容已安装"
else
  warn "未找到 skill/SKILL.md（若仓库内已有则跳过）"
fi

for rt in .workbuddy .claude .codex .agents .grok .hermes .codebuddy; do
  d="$HOME/$rt/skills"
  [ -d "$d" ] || continue
  ln -sfn "$SKILL_SRC" "$d/wechat-local-history" 2>/dev/null && \
    echo -e "    ${DIM}↳ $rt${NC}"
done
ok "Skill 已链接到各运行时"

# ---------------------------------------------------------------------------
echo -e "\n${GRN}"
echo "  ══════════════════════════════════════════════"
echo "   安装完成"
echo "  ══════════════════════════════════════════════"
echo -e "${NC}"
cat <<EOF

  ${YEL}接下来三步：${NC}

  ${CYN}1.${NC} 从桌面双击启动 ${YEL}微信并登录${NC}
     ${DIM}（登录会重新派生数据库密钥，这是提取密钥的唯一可靠时机）${NC}

  ${CYN}2.${NC} 运行提密钥 + 解密：
     ${CYN}bash "$REPO_DIR/scripts/windows/refresh_key.sh"${NC}
     ${DIM}（脚本会提示你登录一次微信，密钥落盘后自动解密 21 个库）${NC}

  ${CYN}3.${NC} ${RED}在 WorkBuddy 里点一次「信任」${NC}
     ${DIM}连接器管理页 → 右上角自定义连接器入口 → 信任 wechat-local${NC}
     ${DIM}不点信任的话 MCP 工具不会激活${NC}

  ${YEL}然后直接说：${NC}
     ${CYN}「把 XX 群的记录发给我」${NC}
     ${CYN}「搜一下我微信里关于 XX 的聊天」${NC}

EOF
