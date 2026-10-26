# Repairing a corrupted message database

## Symptom

- A `message_N.db` shard is missing from `db_storage/message/`, or a whole date range of chats disappears while other ranges are intact.
- WeChat quarantines a damaged shard by moving it to `<name>.db.factory/<timestamp>/` and starts writing to a new shard. The timestamp is when the shard was quarantined, not when it was damaged.

## What is actually wrong

WCDB/SQLCipher encrypts each 4096-byte page independently (AES-256-CBC plus a page HMAC). A quarantined shard is usually damaged in only a handful of pages, most often page 1 — the schema root — which is why the file no longer opens at all. The remaining pages are typically intact and recoverable.

Two facts make the repair possible:

- The file header of a quarantined shard often has a corrupted cipher salt, so `PRAGMA key` cannot derive a key from it. The true salt is the first 16 bytes of the paired `.db-first.material` file.
- The `<name>.db-wal` next to the archive holds the newest committed pages as complete on-disk page images, including page 1. Those frames usually cover exactly the pages that are damaged in the main file.

## Triage

```bash
SKILL_DIR=~/Desktop/Langlobal/wechat-decrypt   # or the installed skill directory
D="$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<account>/db_storage"

# Which shards are quarantined, and when
ls -d "$D/message/"*.db.factory/*
```

Then confirm each live shard is healthy and measure the gap: `PRAGMA integrity_check` on every live database, and compare the recovered window against the live set before claiming anything was lost.

## Rebuild the shard

`rebuild_from_factory.py` reads the salt from the `.material` file, validates every page by HMAC, finds the damaged pages, and overwrites them with validated WAL frames.

```bash
"$PYTHON" "$SKILL_DIR/scripts/common/rebuild_from_factory.py" \
  "$D/message/message_5.db.factory/2026-09-03_13-27-55.558964000" \
  -o /tmp/rebuilt.db --verify
```

`--verify` runs `PRAGMA integrity_check` on the output. `integrity_check` returning `ok` means the whole database is back, and the existing raw key opens it directly — no re-extraction is needed.

If pages remain damaged after the WAL is applied, the WAL did not cover them. In that case the database is still partially corrupt and the row-level fallback below is the only option.

## Fallback: salvage rows when the root page is gone

`salvage_btree.py` decrypts the pages that pass HMAC and walks table B-trees, or scans orphaned leaf pages when the root page itself is unrecoverable. `salvage_export.py` wraps that into a per-contact text export. Use it only when `rebuild_from_factory.py` cannot produce an `ok` database, since it recovers rows but not a usable database.

## Export what was recovered

```bash
"$PYTHON" "$SKILL_DIR/scripts/common/salvage_rebuilt.py" /tmp/rebuilt.db -o ~/Desktop/recovered
```

Writes one text file per conversation, an index, and a plaintext SQLite copy you can query with any tool. Voice messages are transcribed when the whisper model is already cached; pass `--no-transcribe` to skip, or `--no-plaintext` to skip the SQLite copy. The default output directory is derived from the recovered date range.

## Restoring the shard into WeChat

Restoring is optional and only affects whether WeChat itself displays the recovered range; the export above is independent of it.

1. Quit WeChat and confirm no process holds the shard: `lsof +D "$D/message"`.
2. Back up first: `cp -a "$D/message" ~/wx-db-backups/$(date +%Y%m%d_%H%M%S)/message` and verify the copy matches.
3. Copy the rebuilt database to `"$D/message/message_5.db"`. Do not copy a `-wal`, `-shm`, or `.material` alongside it; let WeChat create them.
4. Start WeChat. Acceptance looks like: the shard stays in place, WeChat creates `<name>.kvdb` and a `-wal`, and no new timestamp directory appears under the `.factory` directory. WeChat updates that shard's own `Name2Id` page on adoption; the message pages themselves are left alone.
5. Roll back by deleting the restored `message_5.db` and `message_5.kvdb*` while WeChat is closed.

`real_sender_id` is database-local: the same rowid maps to different contacts in different shards, so rowids must never be compared or copied across shards. Sender attribution is resolved per shard through that shard's own `Name2Id`.

## Limits

- Pages damaged without a WAL copy are lost, along with any rows that lived only in them.
- Messages written after the last successful flush before quarantine were never persisted and cannot be recovered.
- The rebuild is only as good as the archive: run `--verify` and state the remaining gap instead of assuming full recovery.
