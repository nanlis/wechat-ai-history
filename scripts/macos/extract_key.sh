#!/bin/bash
# One-click WeChat raw key extraction (macOS, Frida 17.x+)
set -euo pipefail
umask 077
SKILL_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
KEY_FILE="$SKILL_DIR/key.txt"
FRIDA_OUT="$(mktemp -t wechat_frida.XXXXXX)"
FRIDA_PID=""
FRIDA="$SKILL_DIR/.venv/bin/frida"
[ -x "$FRIDA" ] || FRIDA="$(command -v frida || true)"
if [ -z "$FRIDA" ]; then
    echo "ERROR: 未找到 frida。先运行 bash setup.sh。"
    exit 1
fi

cleanup() {
    if [ -n "$FRIDA_PID" ]; then
        kill "$FRIDA_PID" 2>/dev/null || true
    fi
    rm -f "$FRIDA_OUT"
}
trap cleanup EXIT

echo "=== WeChat Key Extraction ==="
echo ""
echo "微信窗口即将打开，请扫码登录。登录后 key 自动写入 key.txt。"

# 1. Kill existing WeChat
echo "[1/3] Exiting WeChat..."
killall WeChat 2>/dev/null || true
sleep 1

# 2. Spawn via Frida, capture all output to temp file (avoids SIGPIPE from head/grep)
echo "[2/3] Spawning WeChat via Frida..."
cd "$SKILL_DIR"

"$FRIDA" -f /Applications/WeChat.app/Contents/MacOS/WeChat -l scripts/macos/hook_pbkdf.js > "$FRIDA_OUT" 2>&1 &
FRIDA_PID=$!

# Wait for RAW_KEY= to appear in output (timeout 120s)
for i in $(seq 1 240); do
    if grep -q "RAW_KEY=" "$FRIDA_OUT" 2>/dev/null; then
        break
    fi
    sleep 0.5
done

# 3. Extract and save key
echo "[3/3] Extracting key..."
RAW_KEY=$(grep "RAW_KEY=" "$FRIDA_OUT" | head -1 | sed 's/.*RAW_KEY=//' | grep -oE '[a-f0-9]{64}' | head -1)

if [ -z "$RAW_KEY" ]; then
    echo "ERROR: Failed to capture key."
    echo "Frida output:"
    tail -20 "$FRIDA_OUT"
    echo ""
    echo "Make sure:"
    echo "  1. sudo codesign --force --deep --sign - /Applications/WeChat.app"
    echo "  2. You scanned QR code to log in"
    kill "$FRIDA_PID" 2>/dev/null || true
    FRIDA_PID=""
    exit 1
fi

kill "$FRIDA_PID" 2>/dev/null || true
FRIDA_PID=""
printf '%s\n' "$RAW_KEY" > "$KEY_FILE"
chmod 600 "$KEY_FILE"
echo "Done! Key saved to $KEY_FILE ($(wc -c < "$KEY_FILE" | tr -d ' ') bytes)."
echo ""
echo "⚠️  重要：提取用的 adhoc 重签名已破坏微信原始签名，"
echo "    会导致截图/数据访问反复弹出权限确认框。"
echo "    密钥已保存，日常查消息只读数据库、无需重签名。"
echo "    建议现在从 App Store 或微信官网重装微信，恢复腾讯原始签名。"
echo ""
echo "Restart Codex for MCP to take effect."
