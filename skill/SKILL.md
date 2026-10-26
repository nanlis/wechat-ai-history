---
name: wechat-local-history
category: research
description: "读取本机微信 4.x 聊天记录（群聊/好友消息、全文搜索、会话摘要）。适用于「把某个群的记录发给我」「搜一下我微信里关于X的聊天」「整理一下XX群的记录」「微信里有没有人说过…」这类请求。提供已装好的本地 MCP server（6 个工具），无需安装任何东西即可调用。"
---

# 微信本地聊天记录 → AI 可读

读取用户**本机**微信 4.x 的聊天记录。数据已在本机解密，AI 通过本地 MCP server 直接读取。

## 触发条件

- 「把 XX 群的记录发给我」「导出 XX 群」
- 「搜一下我微信里关于 XX 的聊天」
- 「我微信里有没有人提过 XX」
- 「整理一下 XX 群的聊天记录 / 做个总结」
- 「微信最近有什么重要消息」
- 提到了具体群名、好友昵称、wxid、@chatroom

## 已就绪的环境（不要重复安装）

| 项 | 值 |
|---|---|
| MCP server | `wechat-local`（已注册到 WorkBuddy 的 `mcp.json`） |
| 工具数量 | 6 个，见下表 |
| 数据根目录 | `D:\WeChatData\xwechat_files` |
| 内核仓库 | `<REPO>` |
| 活跃账号 | `wxid_example01_dev_a` |
| 已解密 | 21 个库 / 1.3GB / 1,157,006 条消息 / 7,521 会话 |

**如果 MCP 工具不可用**，退回到命令行（见「没有 MCP 时」一节），不要重新装包或重新解密。

## 工具路由表

| 用户想要 | 调用 | 必填参数 |
|---|---|---|
| 有哪些群 / 找某个群的 id | `wechat_list_chats` | `limit`（默认 100）、`offset` 翻页 |
| 读某个群/好友的聊天 | `wechat_read_chat` | `contact`（wxid 或群名）、`limit`、`days` |
| 全文搜索关键词 | `wechat_search_messages` | `keyword`、`days`、`limit` |
| 最近的消息 | `wechat_recent_messages` | `days`、`limit` |
| 近期摘要 / 待办 | `wechat_chat_summary` | `days` |
| 系统事件（撤回/转账/群变更） | `wechat_system_events` | `event`、`days`、`limit` |

## 使用要点（都是实测踩出来的）

### 1. 先 list 拿 wxid，再 read

`wechat_read_chat` 的 `contact` 参数要 wxid（如 `00000000000@chatroom`）。
**群名→wxid 的映射在 `wechat_list_chats` 里**，直接传群名可能匹配不上。
群成员多、返回列表很长时，先用 `limit` 拿前几十条让用户确认。

### 2. `days` 是时间窗，不是条数

`days` 限定往前多少天。要读「全历史」就传一个大值（如 `3650`）。
不传默认 7 天 —— 用户说「这个群的记录」通常指全部历史，别只给 7 天。

### 3. 搜不到就加大 days

微信搜索默认 30 天窗口。搜不到时先扩到 120 / 365 天再回复「没有」。
同一件事在两个分片库（`message_0.db` / `message_1.db`）里，工具已合并搜索。

### 4. 富媒体只能标类型

图片、表情、视频、合并转发、语音这些**正文是 WCDB 单独加密的**，
工具会输出 `[图片]` 这类标注，**拿不到里面的文字**。
遇到这类消息要如实说明「这是图片/视频，内容无法解析」，不要编造。

文本消息 100% 可读。

### 5. 系统事件有独立入口

撤回、红包/转账、拍一拍、进群/退群、群公告这类不在普通消息流里，
要用 `wechat_system_events`。用户问「谁撤回了」「有没有人发红包」时用它。

### 6. 拿群成员名单

群成员在 `contact.db` 的 `chat_room` 表（26,659 人）。
MCP 工具没直接暴露时，用命令行读明文库（路径见下）。

## 没有 MCP 时（降级路径）

```bash
cd /d/Projects/wxdec-skill
V=.venv/Scripts/python.exe

# 列群（带群名）
$V scripts/windows/wxread.py list

# 读某群
$V scripts/windows/wxread.py messages "00000000000@chatroom" --limit 100

# 导出 txt（5.5 万条约 40MB）
$V scripts/windows/wxread.py export "00000000000@chatroom" /tmp/out.txt

# 全文搜索（跨两个分片库）
$V scripts/windows/wxread.py search "某联系人"
```

明文库位置（只读，不要改）：
`<REPO>\decrypted\wxid_example01_dev_a\db_storage\`

## 密钥失效时（微信重启/升级后读不到数据）

症状：MCP 工具报找不到库 / 返回 0 条。

处理顺序：
1. 确认微信在运行：`frida` 枚举 `Weixin.exe`（**不要用 `tasklist`**，沙箱下返回 0 行）
2. 重新提密钥：
   ```bash
   cd /d/Projects/wxdec-skill
   WECHAT_DATA_ROOT="D:\WeChatData\xwechat_files" .venv/Scripts/python.exe \
     scripts/windows/hook_raw_key_broad.py 900
   ```
   脚本会挂上 SHA-512 钩子，**需要用户在微信里登录一次**（登录会重新派生密钥），
   密钥落在 `key_windows.txt`。
3. 重新解密：
   ```bash
   WECHAT_DATA_ROOT="D:\WeChatData\xwechat_files" .venv/Scripts/python.exe scripts/windows/decrypt_all.py
   ```
   676MB 的主库要用 `scripts/windows/decrypt_stream.py`（流式），否则 MemoryError。

**详细原理、8 条已排除的死路、SQLCipher 参数**：
`<REPO>\docs\windows-4.x-key-extraction.md`
`<REPO>\docs\沉淀方案-微信记录接入AI.md`

## 安全边界（硬性）

- **绝不输出、打印、转存原始密钥**（`key_windows.txt` 的内容）。它等同微信账号的钥匙。
- **绝不让明文库离开本机**，不上传、不提交 git、不贴进对话。
- 只操作用户本机、用户有权访问的数据。
- 涉及他人（群友）的聊天内容，转述时避免泄露隐私细节，必要时先摘要再给结论。
- 群聊内容若将被公开引用，先征得用户同意。

## 已知限制

- 富媒体正文解不开（见上）
- 密钥随微信登录/升级失效，需重新提（见上）
- 微信 4.x 的 `SessionTable.type` 字段不再填充，**群靠 `@chatroom` 后缀识别**
- 联系人库里有部分 `@openim` 账号（企业微信/视频号类），显示名可用
