# 微信 4.x 本地聊天记录读取（Windows）

从本机微信 4.1.15.56 的加密数据库里提取群聊/好友聊天记录，导出成可读文本。

## 一句话状态

已跑通。密钥已拿到并验证，21 个库全部解密为明文 SQLite（115.7 万条消息），
读取器 `wxread.py` 支持列群 / 读某个会话 / 导出 txt / 全文搜索。

---

## 环境事实

> 下表是**某台机器的实测记录**，账号名与路径已泛化。换机器时只有「数据根目录」和「账号目录名」会变，
> 其余结论（加密参数、目录结构、KDF 参数）不变。

| 项 | 值 |
|---|---|
| 微信版本 | 4.1.15.56，装在 `<微信安装目录>` |
| 进程 | `Weixin.exe`（多进程）+ `WeChatAppEx.exe` |
| 数据根目录 | `<WECHAT_DATA_ROOT>`（微信设置 → 文件管理里改过位置） |
| 账号目录 | `xwechat_files/<wxid>_<device>/db_storage`，一个账号一个目录 |
| 加密 | SQLCipher v4，AES-256-CBC + HMAC-SHA512 |
| KDF | PBKDF2-HMAC-SHA512，**256,000 次迭代**，32 字节密钥 |
| 页 | 4096 字节，reserve 80 字节（IV 16 + MAC 64） |

**账号不唯一怎么办**：raw key 是**按账号**派生的，一把 key 只开得了它所属的那个账号。
`decrypt_all.py` 会拿 key 去逐个探测数据根下的所有账号目录，只解开能打开的那一个；
想指定账号用 `--account <目录名或wxid>`，或设环境变量 `WECHAT_ACCOUNT`。

---

## 目录

```
<REPO>\
├── .venv\                       frida 17.24.3 + pycryptodome + zstandard
├── key_windows.txt              ← raw key（敏感，勿外传）
├── decrypted\                   ← 明文 SQLite 库（敏感，勿外传）
│   └── <account>\db_storage\
│       ├── message\message_0.db   主库，几十到几百 MB
│       ├── message\message_1.db   第二个分片
│       ├── contact\contact.db      群 / 成员名单
│       ├── session\session.db     会话列表
│       └── ... 每个账号几十个库
└── scripts\windows\
    ├── hook_raw_key_broad.py   ← 提取 raw key（主力）
    ├── decrypt_stream.py       ← 流式解密（超大库必用）
    ├── decrypt_all.py          ← 一次性解密（自动匹配账号）
    ├── find_ctx.py             ← 备用提 key 路线（按 kdf_iter 常量定位）
    ├── dump_exports.py         ← 扫 DLL 导出符号（排查用）
    └── wxread.py               ← 读取器（list/messages/export/search）
```

---

## 操作手册

### 0. 前置

```bash
cd <仓库目录>
export WECHAT_DATA_ROOT="<数据根目录>"
```

### 1. 提取 raw key（微信必须正在运行）

```bash
.venv/Scripts/python.exe scripts/windows/hook_raw_key_broad.py 900
```

**触发条件：用户登录微信的那一刻。** 登录会重新派生密钥，钩子那一刻命中。
登录后如果没抓到，可以让用户在微信里做任何事（开群、滑历史）再等。

抓到后写 `key_windows.txt`，并打印 `*** RAW KEY VERIFIED against <db> ***`。

### 2. 解密数据库

```bash
# 小库批量
.venv/Scripts/python.exe scripts/windows/decrypt_all.py

# 大库流式（message_0.db 必用这个）
.venv/Scripts/python.exe scripts/windows/decrypt_stream.py \
  "$(cat key_windows.txt | tr -d '\n\r')" \
  "$WECHAT_DATA_ROOT/<account>/db_storage/message/message_0.db" \
  "decrypted/<account>/db_storage/message/message_0.db"
```

### 3. 读取

```bash
# 列所有群
.venv/Scripts/python.exe scripts/windows/wxread.py list

# 读某个会话（默认最后 50 条）
.venv/Scripts/python.exe scripts/windows/wxread.py messages "<群wxid>" --limit 100

# 导出成 txt
.venv/Scripts/python.exe scripts/windows/wxread.py export "<群wxid>" out.txt

# 全文搜索
.venv/Scripts/python.exe scripts/windows/wxread.py search "关键词" --limit 100
```

---

## 微信 4.x 数据模型

### 消息按会话分表

表名 = `Msg_<md5(username)>`，一个库里几百张。
会话 username 形如 `<数字>@chatroom`（群）或 `wxid_xxxxx`（好友）。

### Msg 表字段

```sql
CREATE TABLE Msg_<md5>(
  local_id INTEGER PRIMARY KEY AUTOINCREMENT,
  server_id INTEGER,
  local_type INTEGER,        -- 消息类型码
  sort_seq INTEGER,
  real_sender_id INTEGER,
  create_time INTEGER,       -- Unix 时间戳
  status INTEGER, upload_status INTEGER, download_status INTEGER,
  server_seq INTEGER, origin_source INTEGER,
  source TEXT,               -- 二进制 blob，里面嵌 wxid
  message_content TEXT,      -- 正文（富媒体时是加密二进制）
  compress_content TEXT,     -- 压缩正文
  packed_info_data BLOB,
  WCDB_CT_message_content INTEGER,
  WCDB_CT_source INTEGER
);
```

### 关键坑

1. **发送者在正文里，不在字段里。** 群聊正文格式是 `wxid_xxx: 消息内容`。
2. `source` 是二进制，要用正则从 bytes 里抠 wxid：
   `re.findall(rb"wxid_[A-Za-z0-9_\-]{6,32}", raw)`
3. **群名不在 contact.db**（那里只有数字 ID）。群名从 `session.db` 的
   `SessionTable.summary` 取。
4. `SessionTable.type` 全是 0 —— 4.x 不再区分群/好友。靠 username 尾缀 `@chatroom` 判断。
5. `local_type` 类型码：
   `1`=文本 `3`=图片 `43`=视频 `47`=表情 `42`=名片 `48`=位置 `49`=文件
   `50`=语音 `10000`=系统提示 `10001`=撤回 `10002`=转账
   `8594229559345`=合并转发 `1094903714`=链接分享 `268435455`=小程序
6. **富媒体正文是独立加密的**，解不出文字内容。图片/视频要走 `media_0.db` /
   `message_resource.db` 拿文件路径。
7. `message_fts.db` 是全文索引库，可以直接 SQL 搜，比自己遍历快。

---

## 提 key 的技术要点

### 原理

SQLCipher 派生关系：

```
enc    = PBKDF2-HMAC-SHA512(raw_key, db_salt, 256000, 32)   # 每库独立的 K1
mac_key = PBKDF2-HMAC-SHA512(enc, db_salt ^ 0x3A, 2, 32)
```

**raw key 不在内存里以明文形式存在**（4.x 把它加密缓存 + AES-NI + 用后擦除三重保护）。
但 PBKDF2 做 HMAC 时会构造 ipad 块 = `key XOR 0x36`，
**hook SHA-512 压缩函数就能从 ipad 块反推出 key**。

### 三条缺一不可

1. **扫全部模块**的 SHA-512 K 表（`22 ae 28 d7 98 2f 8a 42`），
   不能只钩 `Weixin.dll` —— 只钩它抓到的是 TLS/登录的 HMAC，验证全部落空。
2. **候选先落盘**再验证，绝不丢候选。
3. **验证覆盖全部 21 个库**：每个库 salt 不同 → K1 不同。
   K1 直接 AES 验页头；raw 走 PBKDF2 验。

### 验证页头

解出的第 1 页 plaintext 前 8 字节必须是：
`10 00 02 02 50 40 20 20`（页大小 4096 / 保留 80 / 嵌入比 64/32/32）

### 微信没有可钩的导出符号

扫了 22 个二进制、8773 个导出符号，**PBKDF2 / HMAC / sqlite3_key 一个都没有**
（OpenSSL 全静态链接）。只有 cronet 的 sha256 无关。
所以只能钩 SHA-512 压缩函数，不能直接钩 `PKCS5_PBKDF2_HMAC`。

---

## 踩过的坑（别再试）

| 路线 | 为什么失败 |
|---|---|
| 内存扫 `x'<64hex>'` 模式 | 4.1.15.56 把 key 加密缓存，扫不出来；且那工具把迭代写成 2 次（错） |
| frida spawn gating | **Windows 不支持**（`not yet supported on this OS`），仅 Linux/macOS |
| hook `sqlite3_key` | 静态内联，非导出符号，`findExportByName` 找不到 |
| 只 hook `Weixin.dll` | 抓的是 TLS 的 HMAC，不是 SQLCipher 的 |
| 按 salt 邻域扫 K1 | ctx 里 salt 和 key 是**独立指针**，不相邻，必然扑空 |
| `key_info.db` 的 key_info_data | 只是加密信封，没有明文密钥 |
| `wx.exe init` | 即使建了 junction 仍报"未能自动检测到微信数据目录" |
| `tasklist` 枚举进程 | 本会话沙箱限制返回 0 行 → **一律用 frida 的 `enumerate_processes()`** |
| `decrypt_all.py` 解大库 | 整个库读进内存 → 676MB 直接 MemoryError → 用 `decrypt_stream.py` |
| `cmd.exe /c mklink` | 被安全策略拦截 → 用 PowerShell `New-Item -ItemType Junction` |

---

## 敏感数据边界

- `key_windows.txt`（raw key）—— 等同于你微信账号的钥匙，**不可外传**
- `decrypted/`（明文聊天记录）—— 涉及群友隐私，**不可外传、不入 git**
- 用户本人查看自己的记录没问题；给第三方或公开场合一律不行
