#!/usr/bin/env bash
# 一键刷新微信数据库密钥并重新解密。
# 用途：微信重启 / 升级后，MCP 工具读不到数据时跑这个。
#
# 流程：
#   1. 检查微信是否在运行（用 frida 枚举，tasklist 在沙箱下不可靠）
#   2. 挂上 SHA-512 钩子，等待密钥派生
#   3. 如果超时，提示用户去桌面双击微信并登录（登录会重新派生密钥）
#   4. 拿到密钥后解密全部库
#
# 账号无关：密钥自己决定属于哪个账号。decrypt_all.py 会逐个探测数据根下的
# 账号目录，只解开这把密钥能打开的那一个。数据目录也自动探测，无需改脚本。
#
# 用法：
#   bash refresh_key.sh            # 默认等待 180 秒
#   bash refresh_key.sh 600        # 等 10 分钟
#   WECHAT_DATA_ROOT=/path/to/xwechat_files bash refresh_key.sh

set -u
cd "$(dirname "$0")/../.." || exit 1

VENV=".venv/Scripts/python.exe"
WAIT="${1:-180}"

if [ ! -x "$VENV" ]; then
  echo "ERR: 找不到虚拟环境 $VENV"
  exit 1
fi

# ---- 自动探测数据根目录（与 install.sh 保持一致的候选顺序）----
detect_data_root() {
  local c
  for c in \
    "$WECHAT_DATA_ROOT" \
    "/d/Document/xwechat_files" "/c/Document/xwechat_files" \
    "/e/Document/xwechat_files" "/d/xwechat_files" "/c/xwechat_files" \
    "/d/Documents/xwechat_files" "/c/Documents/xwechat_files" \
    "$HOME/Documents/xwechat_files" "/c/Users/$USERNAME/Documents/xwechat_files"; do
    [ -n "$c" ] || continue
    if [ -d "$c" ] && ls -d "$c"/*/db_storage >/dev/null 2>&1; then
      echo "$c"; return 0
    fi
  done
  return 1
}

DATA_ROOT="$(detect_data_root || true)"
if [ -z "$DATA_ROOT" ]; then
  echo "ERR: 没找到微信数据目录（xwechat_files）。"
  echo "     请设置环境变量后重试，例如："
  echo "       WECHAT_DATA_ROOT='C:\\Users\\<你>\\Documents\\xwechat_files' bash scripts/windows/refresh_key.sh"
  echo "     也可以在微信「设置 → 文件管理 → 打开文件夹」里看实际路径。"
  exit 1
fi

export WECHAT_DATA_ROOT="$DATA_ROOT"

echo "=============================================="
echo " 微信密钥刷新 / 重新解密"
echo " 数据目录: $DATA_ROOT"
echo " 等待窗口: ${WAIT}s"
echo "=============================================="

echo ""
echo "[1/4] 检查微信进程..."
PIDS=$("$VENV" -c "
import frida
try:
    d = frida.get_local_device()
    ps = [str(p.pid) for p in d.enumerate_processes() if p.name.lower() == 'weixin.exe']
    print(' '.join(ps))
except Exception as e:
    print('')
" 2>/dev/null)

if [ -z "$PIDS" ]; then
  echo "  [!] 微信未运行。请先从桌面双击启动微信并登录，"
  echo "      然后重新运行本脚本。"
  echo ""
  echo "  >>> 现在就去启动微信 <<<"
  echo ""
  # 仍然尝试挂钩（万一用户正在启动）
  echo "  继续尝试挂钩（若微信此刻启动，可直接命中）..."
else
  echo "  [OK] Weixin.exe 进程: $PIDS"
fi

echo ""
echo "[2/4] 挂上 SHA-512 钩子，等待密钥派生..."
echo "      >>> 关键：请在微信里【登录一次】<<<"
echo "      （登录会重新派生数据库密钥，这是唯一可靠的触发点）"
echo ""

"$VENV" scripts/windows/hook_raw_key_broad.py "$WAIT"
HOOK_RC=$?

if [ ! -s key_windows.txt ]; then
  echo ""
  echo "ERR: 未捕获到密钥。"
  echo "     可能原因："
  echo "       - 微信没重新登录（登录 100% 会触发派生）"
  echo "       - 微信版本变了（需要重新定位 SHA-512 入口）"
  echo "     参考: docs/windows-4.x-key-extraction.md"
  exit 1
fi

echo ""
echo "[3/4] 密钥已捕获。开始重新解密..."
# decrypt_all.py 会用这把密钥自动匹配账号，并对超大库改走流式解密。
"$VENV" scripts/windows/decrypt_all.py 2>&1 | tail -20
DECRYPT_RC=${PIPESTATUS[0]}

if [ "$DECRYPT_RC" != "0" ]; then
  echo ""
  echo "ERR: 解密失败。常见原因：这把密钥属于另一个微信账号"
  echo "     —— 请在微信里切换/登录你要读的那个账号，再重跑本脚本。"
  exit 1
fi

echo ""
echo "[4/4] 完成。验证一下："
"$VENV" -c "
import os, sys
sys.path.insert(0, 'scripts/common')
import query
r = query.list_chats()
print('  会话数:', len(r))
" 2>&1 | tail -3

echo ""
echo "=============================================="
echo " 完成。密钥与明文库均留在本机，请勿外传。"
echo "=============================================="
