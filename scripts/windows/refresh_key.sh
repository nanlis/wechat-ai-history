#!/usr/bin/env bash
# 一键刷新微信数据库密钥并重新解密。
# 用途：微信重启 / 升级后，MCP 工具读不到数据时跑这个。
#
# 流程：
#   1. 检查微信是否在运行（用 frida 枚举，tasklist 在沙箱下不可靠）
#   2. 挂上 SHA-512 钩子，等待密钥派生
#   3. 如果超时，提示用户去桌面双击微信并登录（登录会重新派生密钥）
#   4. 拿到密钥后流式解密全部库
#
# 用法：
#   bash refresh_key.sh            # 默认等待 180 秒
#   bash refresh_key.sh 600        # 等 10 分钟

set -u
cd "$(dirname "$0")/../.." || exit 1

VENV=".venv/Scripts/python.exe"
DATA_ROOT="${WECHAT_DATA_ROOT:-<数据根目录>}"
WAIT="${1:-180}"

export WECHAT_DATA_ROOT="$DATA_ROOT"

echo "=============================================="
echo " 微信密钥刷新 / 重新解密"
echo " 数据目录: $DATA_ROOT"
echo " 等待窗口: ${WAIT}s"
echo "=============================================="

if [ ! -x "$VENV" ]; then
  echo "ERR: 找不到虚拟环境 $VENV"
  exit 1
fi

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
# 全量解密；主库走流式避免 MemoryError
"$VENV" scripts/windows/decrypt_all.py 2>&1 | tail -5

MAIN_SRC="$DATA_ROOT\\wxid_example01_dev_a\\db_storage\\message\\message_0.db"
MAIN_DST="decrypted\\wxid_example01_dev_a\\db_storage\\message\\message_0.db"
if [ -f "$MAIN_SRC" ]; then
  echo "  流式解密主库（676MB）..."
  "$VENV" scripts/windows/decrypt_stream.py \
    "$(tr -d '\r\n' < key_windows.txt)" "$MAIN_SRC" "$MAIN_DST" 2>&1 | tail -3
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
