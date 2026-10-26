# macOS setup and key extraction

Read this file only for macOS installation, a missing/invalid key, or re-extraction after WeChat changes.

## Install

From the skill checkout:

```bash
bash setup.sh
```

Setup creates `.venv`, installs dependencies, safely links the checkout at `$HOME/.agents/skills/wechat-decrypt`, migrates missing private files from the legacy `$HOME/.codex/skills/wechat-decrypt` copy, and registers the `wechat` stdio MCP server with Codex. It never overwrites a private file.

Ordinary setup omits the sizeable voice stack. Install it only for transcription with `bash setup.sh --with-voice`; this does not download the model.

If the user-skill path already points to another checkout, ordinary setup stops. Inspect that checkout, then explicitly switch with `bash setup.sh --upgrade`; setup moves the previous path to a timestamped backup before linking the current checkout. A failed private-state migration restores the prior link.

Run the diagnostic with the venv Python. A missing key is expected before first extraction.

```bash
.venv/bin/python scripts/common/doctor.py --json
```

## Extract the raw key

Only do this when the key is missing/invalid or WeChat changed. The user must understand that ad-hoc signing replaces the app bundle's official signature.

1. Check the signature:

   ```bash
   codesign -dv /Applications/WeChat.app 2>&1 | grep 'Signature='
   ```

2. If it is not already ad-hoc signed, ask the user to run:

   ```bash
   sudo codesign --force --deep --sign - /Applications/WeChat.app
   ```

3. Explain that extraction closes the current WeChat process, then run:

   ```bash
   bash scripts/macos/extract_key.sh
   ```

4. Tell the user to scan the QR code. The script writes `key.txt` with private permissions and never echoes the key.
5. Re-run `doctor.py --json`.

## Restore the official signature

After a successful extraction, tell the user to reinstall WeChat from the App Store or the official WeChat site so the app bundle regains Tencent's signature. Do not suggest a recursive delete command. The chat container is separate from the app bundle, but the user should follow their normal backup practice before reinstalling.

Repeated privacy prompts or broken screenshot/data permissions after extraction usually indicate that the app is still ad-hoc signed. Daily read/search/export work does not need ad-hoc signing.

## Troubleshooting

- No `RAW_KEY`: confirm the user completed QR login before the 120-second timeout.
- Frida missing: rerun `bash setup.sh`; `extract_key.sh` prefers `.venv/bin/frida`.
- SQLCipher missing: install with `brew install sqlcipher`; `config.py` discovers it from `PATH` or `WECHAT_SQLCIPHER_PATH`.
- Database missing: open WeChat, sign in, and confirm data exists below `~/Library/Containers/com.tencent.xinWeChat/`.

The extractor hooks `CCKeyDerivationPBKDF` and captures the 32-byte raw key used before per-database PBKDF2 derivation.
