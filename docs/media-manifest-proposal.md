# Per-message local image manifest

Implemented for [issue #6](https://github.com/tzwkb/wechat-decrypt/issues/6).
The filename retains the original proposal link; this document describes the
shipping interface.

## Export

```bash
python scripts/common/export_chat.py CONTACT --year 2026 --with-media --no-transcribe -o chat.html
```

`--with-media` produces the transcript, a sibling `chat.media.json` sidecar and
`chat.assets-<export-id>/`. The manifest's `assets_directory` field names the exact
directory to move with this export. Each run uses a fresh directory, so changing
the date window at the same transcript path does not retain unrelated old images
inside the new asset directory. Previous exported directories are preserved.
Only image messages in the selected conversation and date window
are considered. Ordinary exports remain unchanged when this flag is omitted.

The extension chooses TXT, Markdown (`.md`) or HTML (`.html`/`.htm`). Override it
with `--format txt|markdown|html`. TXT appends relative image paths; Markdown/HTML
embed supported local JPEG, PNG, GIF or WebP images. Keep the transcript, JSON and
assets directory together when moving an export. HTML is static and escapes
message markup. Markdown escapes message markup before adding generated links.

## Manifest version 1

```json
{
  "version": 1,
  "transcript": "chat.html",
  "snapshot": "opaque local snapshot fingerprint",
  "assets_directory": "chat.assets-export_id",
  "entries": [{
    "message": {
      "conversation": "wxid_example",
      "table": "Msg_conversation_hash",
      "shard": "message/message_0.db",
      "local_id": 42,
      "server_id": "100042"
    },
    "kind": "image",
    "status": "resolved",
    "files": [{"path": "chat.assets-export_id/message_identity/image.dat.png"}]
  }]
}
```

The snapshot fingerprint scopes the manifest to local storage and database file
metadata. It does not fingerprint every database byte. Message identity includes
conversation/table, shard and local ID; the server ID is optional. Equal local IDs
in different shards or conversations stay distinct. Each message gets a distinct
asset directory, so equal basenames do not overwrite other messages. A stored
`.dat` containing a supported plaintext image receives a detected image suffix.

Statuses are `resolved`, `not_found` and `encrypted_unsupported`. Unresolved entries
have empty `files` and a `reason` such as `resource_missing`, `hardlink_missing`,
`ambiguous_resource`, `ambiguous_location`, `index_missing`, `unsupported_schema`,
`unsupported_resource_profile`, `original_account_missing` or
`file_missing_or_unreadable`. Placeholders remain visible in all three transcript
formats. An ambiguous relationship never selects the first candidate.

## Verified image-index profile

Read-only inspection and a native message/index join verified this profile on
macOS WeChat **4.1.15 (270102)**. Synthetic SQLite fixtures reproduce its table/field
relationships; the native check exercises the read-only SQLCipher path. Windows uses the
same adapter on the decrypted mirror and locates files in the original account;
its native account format still depends on this index profile.

1. Resolve the selected conversation through `message/message_resource.db`:
   `ChatName2Id.rowid -> MessageResourceInfo.chat_id`.
2. Match local ID, creation time, image type and server ID when present. Reject
   multiple matching resource rows. Shard identity stays in the exported entry.
3. Read the image resource identifier from protobuf field **2.1** of
   `MessageResourceInfo.packed_info`, accepting only the demonstrated 32-hex form.
4. Look that identifier up in the registered `image_hardlink_info_v4.file_name`
   profile in `hardlink/hardlink.db`. Resolve `dir1` and `dir2` through `dir2id`,
   scoped to the independently registered conversation directory.
5. Read the registered file below
   `msg/attach/<dir1>/<dir2>/Img/<file_name>`. Different directory candidates stay
   ambiguous. The resolver does not use the message XML MD5 or scan unrelated
   cache files for filename guesses.

Unknown schemas/protobuf profiles remain unresolved with an explicit reason.
The exporter only copies supported plaintext images. Encrypted `.dat` originals
remain `encrypted_unsupported`; image AES/XOR decoding is a separate feature, as
specified in the issue. Video, file and voice export retain their existing behavior.

## Local data and verification

The sidecar omits raw resource payloads, database/image keys, authentication fields,
CDN URLs and absolute source paths. It contains conversation/message identifiers,
so keep it private like the transcript. Sidecars and copied files use private
permissions; source and destination paths must remain inside their allowed roots.
The exporter opens source files read-only and rejects a copy that changes while
being read. It does not download media or repair a live database.

Tests cover correct index-to-image mapping despite a different XML MD5, missing
server IDs, equal local IDs across shards/conversations, missing/encrypted assets,
ambiguous resources/locations, unsupported profiles, selection/date filtering,
collision-safe output, isolation of repeated exports, secret omission, traversal/symlink refusal and full CLI
exports in TXT, Markdown and HTML. A browser smoke test verifies that the generated
HTML loads its relative image and shows the unresolved states.
