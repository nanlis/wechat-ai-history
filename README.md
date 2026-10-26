# wechat-ai-history

> 把本机微信 4.x 的聊天记录，变成 AI 可以直接调用的能力。
>
> 密钥提取 → 数据库解密 → MCP server → Agent Skill，全链路本地只读。

[![Windows](https://img.shields.io/badge/Windows-4.0%2B-0078D4?logo=windows)](https://github.com/nanlis/wechat-ai-history)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## 这是什么

一句话：**在你的电脑上，给 AI 装上一双能读微信的眼睛。**

以前想把群里的一段记录给 AI，你得截图、复制、粘贴，格式全乱、上下文丢一半。
现在一句话就行，而且 AI 拿到的是**结构化的、带时间戳和发送者的原文**。

| 能力 | 说明 |
|---|---|
| 列会话 / 找群 | 7,521 个会话，179 个群，直接给群名和 wxid |
| 读某个群 | 支持全部历史，不只是最近 7 天 |
| 全文搜索 | 跨两个消息分片库检索，115 万条消息随便搜 |
| 摘要 / 系统事件 | 撤回、转账、拍一拍、进群退群等独立入口 |

## 为什么现成工具都不行

| 方案 | 卡在哪 |
|---|---|
| 微信官方 | 没有导出接口，备份是加密的、换机就废 |
| 常规「内存扫密钥」 | 微信 4.x 把 OpenSSL **全静态链接**进 DLL，扫 8773 个导出符号**没有** PBKDF2/HMAC/sqlite3_key 可钩 |
| [WeFlow](https://github.com/hicccc77/WeFlow) | 做得很全，但 README 明说「依赖**自定义的原生解密组件，需自行设计**」——**这块留空了** |
| wx-cli | 只认 `~/Documents/xwechat_files`，认不出自定义数据路径 |

**本仓库填的就是那块空。**

## 快速开始（全新 Windows 电脑）

```bash
git clone https://github.com/nanlis/wechat-ai-history.git
cd wechat-ai-history
bash install.sh
```

安装脚本会自动完成：探测 Python → 探测微信 → 探测数据目录 → 建 venv 装依赖 →
创建目录联接 → 注册 MCP server → 安装 Skill 到各运行时。

然后三步：

```bash
# 1. 从桌面双击启动微信并登录
#    （登录会重新派生数据库密钥 —— 这是提取密钥的唯一可靠时机）

# 2. 提取密钥 + 解密全部数据库
bash scripts/windows/refresh_key.sh

# 3. 在 WorkBuddy / Claude Code 里点一次「信任」wechat-local
#    （连接器管理页 → 右上角自定义连接器入口）
```

之后直接说：

```
「把 XX 群的记录发给我」
「搜一下我微信里关于 XX 的聊天」
```

## 架构

```
AI 客户端          WorkBuddy · Claude Code · Codex
    ↓
Skill              skill/SKILL.md —— 教 AI 何时调、怎么调、有哪些坑
    ↓
MCP Server         server.py —— 6 个工具，stdio，本地
    ↓
内核               scripts/windows/ —— 提密 → 解密 → 查询
    ↓
数据               微信本地数据目录（SQLCipher 4 加密）
```

三层缺一不可：只有 Skill，AI 每次要现拼命令；只有 MCP，AI 不知道工具存在；只有内核，那只是脚本，不是能力。

## 核心突破：怎么拿到密钥

微信 4.x 把 OpenSSL 全部静态链接进 DLL，没有任何可钩的导出符号。
最终方案是**钩 SHA-512 压缩函数**，从 HMAC 内层的 ipad 块还原明文 key：

```
HMAC 内层：ipad = key ⊕ 0x36
→ 抓到 ipad，XOR 回 0x36 即得明文 key
```

三个必须同时满足的条件（换版本会踩的坑）：

1. **钩全部模块** —— 只钩 `Weixin.dll` 会抓到 TLS 的 HMAC 假货
2. **候选落盘再离线验证** —— 在线验证会漏，4 个 TLS 假货就是这么混进来的
3. **微信登录那一刻** —— SQLCipher 只在首次打开某个库时派生密钥

### SQLCipher 4 参数

```
enc  = PBKDF2-HMAC-SHA512(raw_key, db_salt, 256000, 32)   # 每库独立
mack = PBKDF2-HMAC-SHA512(enc, db_salt ^ 0x3A, 2, 32)
AES-256-CBC · page 4096 · reserve 80
```

## 工具清单

| 脚本 | 作用 |
|---|---|
| `scripts/windows/hook_raw_key_broad.py` | 钩全部模块，捕获并落盘密钥候选 |
| `scripts/windows/decrypt_stream.py` | 流式解密（676MB 主库 8 秒，避免 MemoryError） |
| `scripts/windows/decrypt_all.py` | 全量解密 |
| `scripts/windows/wxread.py` | 列会话 / 读群 / 搜索 / 导出 txt |
| `scripts/windows/refresh_key.sh` | 一键提密钥 + 重解密 |
| `scripts/windows/find_k1.py` | 内存中定位单库的派生密钥 |
| `scripts/windows/find_ctx.py` | 按 `kdf_iter=256000` 常量定位 SQLCipher ctx |
| `scripts/windows/dump_exports.py` | 扫描 DLL 导出符号 |
| `server.py` | MCP server（6 工具） |
| `install.sh` | 全新电脑一键安装 |

## 已验证的规模

| 项 | 数值 |
|---|---|
| 解密数据库 | 21 个 / 1.3 GB 明文 |
| 消息总数 | 1,157,006 条 |
| 会话 | 7,521 个（含 179 个群） |
| 群成员 | 26,659 人 |
| `integrity_check` | ok |
| 测试环境 | Windows · 微信 4.1.15.56 |

## 已知限制

- **富媒体正文解不开**：图片/表情/视频/语音的正文由 WCDB 单独加密，工具只能标注类型。
  文本消息 100% 可读。仓库内有 `export_media.py` / `voice_decode.py` 可进一步接驳。
- **密钥随微信登录/升级失效**：跑 `refresh_key.sh` 重新提取。
- **微信 4.x 不再填充 `SessionTable.type`**：群靠 `@chatroom` 后缀识别。
- 需要**管理员权限**读取微信进程内存。

## 安全边界

- 密钥（`key_windows.txt`）等同微信账号的钥匙，**绝不入库、不外传、不打印到对话**
- 解密后的明文库涉及群友隐私，**全程本机，零联网**
- `.gitignore` 已强制排除密钥、`decrypted/`、`*.db` 及各类中间产物
- **只操作用户本机、用户有权访问的数据**

请负责任地使用本工具，遵守相关法律法规。

## 致谢与出处

本仓库基于 [tzwkb/wechat-decrypt](https://github.com/tzwkb/wechat-decrypt)（MIT License,
Copyright (c) 2026 cocofeng）演进而来。原仓库提供了跨平台解密框架骨架，
本仓库在 **Windows 微信 4.x** 上的关键突破为：

- SHA-512 压缩函数挂钩方案与全模块覆盖策略
- 大库流式解密（`decrypt_stream.py`）
- 跨分片搜索、MCP server 接入、Agent Skill 封装
- 全新电脑一键安装流程

上游原始说明见 `README.upstream.md` / `README_ZH.upstream.md`，
技术细节见 `docs/windows-4.x-key-extraction.md` 与 `docs/我们做的是什么.md`。

## License

MIT
