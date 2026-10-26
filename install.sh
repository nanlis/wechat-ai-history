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

# 微信可执行文件：可能在安装根目录，也可能在版本子目录里（/d/Weixin/4.1.15.56/Weixin.exe）
EXE=""
for c in \
  "/c/Program Files/Tencent/WeChat" "/c/Program Files (x86)/Tencent/WeChat" \
  "/c/Program Files/Tencent/Weixin" "/d/Weixin" "/e/Weixin" \
  "/d/Program Files/Tencent/WeChat" "/e/Program Files/Tencent/WeChat"; do
  [ -d "$c" ] || continue
  for e in "$c"/Weixin.exe "$c"/WeChat.exe; do
    [ -f "$e" ] && { EXE="$e"; break; }
  done
  [ -n "$EXE" ] && break
  # 版本子目录
  for sub in "$c"/*/; do
    for e in "$sub"Weixin.exe "$sub"WeChat.exe; do
      [ -f "$e" ] && { EXE="$e"; break; }
    done
    [ -n "$EXE" ] && break
  done
  [ -n "$EXE" ] && break
done
if [ -n "$EXE" ]; then
  ok "微信程序: $EXE"
  # 顺手确认版本号（微信 4.0+ 才支持本方案）
  VER=$(ls -d "$(dirname "$EXE")"/*/ 2>/dev/null | head -1)
  [ -n "$VER" ] && echo -e "    ${DIM}版本目录: ${VER%/}${NC}"
else
  warn "没找到微信程序（请确认已安装微信 4.0+）"
  echo -e "    ${DIM}不影响后续流程，提密时再确认即可${NC}"
fi

# ---------------------------------------------------------------------------
step "3/7  探测微信数据目录"

# 路径归一化：把 Windows 风格 (D:\...) 与 Git Bash 风格 (/d/...) 互相转换。
# 用 Python 做，避免 shell 转义地狱（反斜杠在 bash 里是转义字符）。
PY_PATH_HELPER="$REPO_DIR/.path_helper.py"
cat > "$PY_PATH_HELPER" <<'PYHELPER'
import re, sys

def to_bash(p):
    p = str(p).replace('\\', '/')
    m = re.match(r'^([A-Za-z]):/(.*)$', p)
    if m:
        return '/' + m.group(1).lower() + '/' + m.group(2)
    return p

def to_win(p):
    p = str(p).replace('/', '\\')
    m = re.match(r'^([A-Za-z]):\\?(.*)$', p)
    if m:
        return m.group(1).upper() + ':\\' + m.group(2).lstrip('\\')
    m = re.match(r'^/([A-Za-z])/(.*)$', str(p).replace('\\', '/'))
    if m:
        return m.group(1).upper() + ':\\' + m.group(2).replace('/', '\\')
    return p

if __name__ == '__main__':
    mode, value = sys.argv[1], sys.argv[2]
    print(to_bash(value) if mode == 'bash' else to_win(value))
PYHELPER
path_bash() { "$PY" "$PY_PATH_HELPER" bash "$1"; }
path_win()  { "$PY" "$PY_PATH_HELPER" win  "$1"; }

DATA_ROOT_RAW="${1:-}"
if [ -n "$DATA_ROOT_RAW" ]; then
  DATA_ROOT="$(path_bash "$DATA_ROOT_RAW")"
else
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
  echo -e "    ${DIM}格式示例：C:\\Users\\$USERNAME\\Documents\\xwechat_files${NC}"
  read -rp "    请手动输入数据目录（回车用默认 C:\\Users\\$USERNAME\\Documents\\xwechat_files）: " DATA_ROOT_RAW
  DATA_ROOT="$(path_bash "${DATA_ROOT_RAW:-C:/Users/$USERNAME/Documents/xwechat_files}")"
fi
DATA_ROOT_WIN="$(path_win "$DATA_ROOT")"

# 验证：目录里应该有 db_storage
if [ -d "$DATA_ROOT" ] && ls -d "$DATA_ROOT"/*/db_storage >/dev/null 2>&1; then
  ACCOUNTS=$(ls -d "$DATA_ROOT"/*/db_storage 2>/dev/null | wc -l)
  ok "数据目录: $DATA_ROOT_WIN （$ACCOUNTS 个账号）"
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
TARGET_WIN="$DATA_ROOT_WIN"
LINK_WIN="C:\\Users\\$USERNAME\\Documents\\xwechat_files"
if [ ! -e "$LINK_DIR" ]; then
  # 用 PowerShell 原生 Junction：
  # Git Bash 里调 cmd.exe /c mklink 会被引号转义吃掉（/J 被当成路径）。
  powershell.exe -NoProfile -Command \
    "New-Item -ItemType Junction -Path '$LINK_WIN' -Target '$TARGET_WIN' -ErrorAction Stop | Out-Null" \
    >/dev/null 2>&1 && \
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
"$VPY" - "$MCP_FILE" "$REPO_DIR" "$DATA_ROOT_WIN" <<'PYEOF'
import json, os, sys

mcp_file, repo_dir, data_root = sys.argv[1], sys.argv[2], sys.argv[3]


def to_win(path):
    """Normalise any path form into a Windows path (D:\\...)."""
    p = str(path).replace('/', '\\')
    while '\\\\' in p[3:]:
        p = p[:3] + p[3:].replace('\\\\', '\\')
    return p


repo_win = to_win(repo_dir)
root_win = to_win(data_root)

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
print(f'  已写入 {mcp_file}')
print(f'  数据根目录 {root_win}')
PYEOF
ok "MCP server: wechat-local"

# ---------------------------------------------------------------------------
step "7/7  安装 Skill 到各运行时"

# Windows 上必须用目录联接（mklink /J），不能用 ln -s：
# Git Bash 的 MSYS=winsymlinks:nativestrict 下 ln -s 需要管理员权限，
# 无权限时会静默失败并留下空目录（ln 的错误被 2>/dev/null 吞掉）。
# mklink /J 不需要符号链接权限，且与本脚本第 5 步、.workbuddy/skills 下
# 其他 skill 的做法一致。
link_dir() {
  # link_dir <目标路径> <源目录>
  local target="$1" source="$2"
  local target_win source_win
  target_win="$(to_win_path "$target")"
  source_win="$(to_win_path "$source")"
  mkdir -p "$(dirname "$target")"
  # 已是联接 -> 视为就绪
  if [ -n "$(fsutil_reparse "$target_win")" ]; then
    return 0
  fi
  # 是真实目录（不是联接）-> 可能是旧版留下的空壳，移走后重建
  if [ -e "$target" ]; then
    if [ -z "$(ls -A "$target" 2>/dev/null)" ]; then
      rmdir "$target" 2>/dev/null || rm -rf "$target" 2>/dev/null
    else
      return 1
    fi
  fi
  # 用 PowerShell 原生 Junction：
  # Git Bash 里调 cmd.exe /c mklink 会被引号转义吃掉（/J 被当成路径），
  # 而 ln -s 在 Windows 上需要符号链接权限，无权限时静默失败。
  powershell.exe -NoProfile -Command \
    "New-Item -ItemType Junction -Path '$target_win' -Target '$source_win' -ErrorAction Stop | Out-Null" \
    >/dev/null 2>&1
}

# Windows 路径（反斜杠 + 大写盘符），供 PowerShell 使用
to_win_path() {
  "$PY" -c "
import sys
p = sys.argv[1].replace('/', chr(92))
if len(p) > 1 and p[1] == ':':
    print(p[0].upper() + p[1:])
else:
    print(p)
" "$1"
}

# 判断路径是否为 reparse point（联接/符号链接），输出非空即表示是
fsutil_reparse() {
  local target_win="$1"
  powershell.exe -NoProfile -Command \
    "\$i = Get-Item -LiteralPath '$target_win' -Force -ErrorAction SilentlyContinue; if (\$i -and (\$i.Attributes -band [IO.FileAttributes]::ReparsePoint)) { 'yes' }" \
    2>/dev/null | tr -d '\r\n '
}

# 用「目录联接」而不是复制单文件：顶层 SKILL.md 引用了 references/*.md 与
# scripts/common/*，只复制一份 SKILL.md 会让 AI 拿到说明书却找不到手册正文。
SKILL_SRC="$HOME/.skills-manager/skills/wechat-decrypt"

# 旧版本可能留下真实目录（ln -s 在 Windows 上无权限时会建空目录），
# 先备份再重建，避免直接卡住。
if [ -e "$SKILL_SRC" ] && [ -z "$(fsutil_reparse "$(to_win_path "$SKILL_SRC")")" ]; then
  BACKUP="$HOME/.skills-manager/backups/wechat-decrypt.$(date +%Y%m%d%H%M%S).$$"
  mkdir -p "$(dirname "$BACKUP")"
  mv "$SKILL_SRC" "$BACKUP" 2>/dev/null && \
    echo -e "    ${DIM}旧 Skill 已备份: $BACKUP${NC}"
fi

if link_dir "$SKILL_SRC" "$REPO_DIR"; then
  ok "Skill 已链接到仓库（references/ 与 scripts/ 全部可达）"
else
  warn "Skill 链接失败，AI 可能读不到 references/ 手册"
  warn "可手动执行：powershell -Command \"New-Item -ItemType Junction -Path '$(to_win_path "$SKILL_SRC")' -Target '$(to_win_path "$REPO_DIR")'\""
fi

# 顶层 SKILL.md 是跨平台版（用 SKILL_DIR 相对定位），是各运行时的主入口。
# skill/SKILL.md 是 Windows 微信实战补充，作为副入口一并提供。
if [ -f "$REPO_DIR/skill/SKILL.md" ]; then
  ok "实战补充说明已就绪（skill/SKILL.md + references/）"
fi

for rt in .workbuddy .claude .codex .agents .grok .hermes .codebuddy; do
  d="$HOME/$rt/skills"
  [ -d "$d" ] || continue
  link_dir "$d/wechat-decrypt" "$SKILL_SRC" && \
    echo -e "    ${DIM}↳ $rt${NC}"
done
ok "Skill 已链接到各运行时"

# 实战补充版（Windows 微信踩坑经验）单独成 skill，
# 目录名用 wechat-local-history，与仓库内 skill/SKILL.md 的 name 对齐。
# 这一版是「复制品」而非联接（内容独立、便于各运行时共享），
# 因此目标路径存在时直接覆盖更新，不做联接。
SUPP_SRC="$HOME/.skills-manager/skills/wechat-local-history"
if [ -f "$REPO_DIR/skill/SKILL.md" ]; then
  mkdir -p "$SUPP_SRC/references"
  cp "$REPO_DIR/skill/SKILL.md" "$SUPP_SRC/SKILL.md"
  for ref in macos windows db-repair export-transcription; do
    [ -f "$REPO_DIR/references/$ref.md" ] && \
      cp "$REPO_DIR/references/$ref.md" "$SUPP_SRC/references/$ref.md"
  done
  for rt in .workbuddy .claude .codex .agents .grok .hermes .codebuddy; do
    d="$HOME/$rt/skills"
    [ -d "$d" ] || continue
    target="$d/wechat-local-history"
    if [ -e "$target" ] && [ -z "$(fsutil_reparse "$(to_win_path "$target")")" ]; then
      # 旧版留下的真实目录 -> 换成联接，保持单一真源
      rm -rf "$target" 2>/dev/null
    fi
    link_dir "$target" "$SUPP_SRC" 2>/dev/null
  done
  ok "实战补充 Skill 已就绪（wechat-local-history）"
fi

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
