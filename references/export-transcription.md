# Export and voice transcription

Read this file only when the user requests chat export or voice transcription.

## Privacy and scope

Export only the requested contact and date range. If matching returns multiple plausible contacts, stop and ask the user to select one. Output files and voice caches contain private conversation data; keep them local and do not attach or upload them unless the user explicitly requests that destination.

## Export

```bash
"$PYTHON" "$SKILL_DIR/scripts/common/export_chat.py" CONTACT --year 2026 -o /path/to/chat.txt
"$PYTHON" "$SKILL_DIR/scripts/common/export_chat.py" CONTACT --start 2026-01-01 --end 2026-06-30 -o /path/to/chat.txt
```

Omitting dates exports all available history. The exporter merges message shards, orders messages chronologically, and writes the output with private permissions.

## Image manifest and transcript links

Add `--with-media` to associate image messages with the local resource index and
export only images belonging to the selected conversation and date window:

```bash
"$PYTHON" "$SKILL_DIR/scripts/common/export_chat.py" CONTACT --year 2026 --with-media --no-transcribe -o /path/to/chat.html
```

This creates `chat.html`, `chat.media.json` and a fresh `chat.assets-<export-id>/`
directory named in the manifest's `assets_directory` field. Repeated exports keep
their assets separate, so a new date window does not inherit older images. TXT shows relative
paths, Markdown and HTML preview supported plaintext images. Format is inferred
from `.md`/`.html` or selected with `--format txt|markdown|html`. Keep the three
outputs together when moving them. The manifest explicitly distinguishes resolved,
missing and encrypted/unsupported images. Encrypted originals are not decoded.
The initial adapter covers images; video/file/voice messages keep their existing
export behavior. See [the format and supported index profile](../docs/media-manifest-proposal.md).

## Voice model consent

Check both `voice-backend` and `voice-model` in `doctor.py --json` before any export that may include audio.

- Both are `ok`: transcription may run automatically unless the user asks to preserve `[Audio]`; use `--no-transcribe` to disable it.
- `voice-backend` is `warn`: install the optional local stack with the platform setup flag shown by doctor. This installs sizeable libraries but not the model.
- `voice-model` is `warn`: ordinary export leaves `[Audio]` and does not download a model. Ask whether the user wants the approximately 3 GB large-v3 download.
- User approves: rerun with `--transcribe`; the first run downloads the platform model.
- User declines: use `--no-transcribe`.

macOS uses `mlx-whisper`; Windows uses `faster-whisper` CPU int8. Both read SILK v3 blobs from `VoiceInfo.voice_data`, decode locally, and align transcripts through `svr_id == server_id`.

To transcribe independently or reuse a prior map:

```bash
"$PYTHON" "$SKILL_DIR/scripts/common/transcribe_db.py" CONTACT -o /path/to/voice-map.json
"$PYTHON" "$SKILL_DIR/scripts/common/export_chat.py" CONTACT --voice-map /path/to/voice-map.json -o /path/to/chat.txt
```

The persistent `voice_cache.json` avoids repeat transcription and is written with private permissions. Messages whose audio was never downloaded locally remain `[Audio]`.
