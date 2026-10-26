# 微信聊天记录 → AI 可用化：沉淀方案决策

> 2026-10-25 实测记录。目标：让 AI（WorkBuddy / Claude Code / Codex）能直接读写本机微信 4.x 聊天记录。

## 一、结论先说

**做成三件套，不是三选一：**

| 层 | 形态 | 职责 | 谁维护 |
|---|---|---|---|
| **内核** | Python 包（`wxdec-skill` 仓库） | 提密钥、解密、查询 | 只我维护（版本敏感） |
| **接口** | 本地 MCP Server（stdio） | 把内核暴露成 6 个工具给任意 AI | 已装好，零维护 |
| **沉淀** | WorkBuddy Skill | 让 AI 知道"什么时候该用微信工具" | 声明式，随内核升级 |

**明确不做：造一个 WeFlow 式的独立 GUI 软件。**

## 二、为什么不造 GUI（WeFlow 已撞车）

WeFlow（github.com/hicccc77/WeFlow，11k+ stars）已经做到：
- Electron 桌面端、本地实时查看
- 消息/朋友圈/视频解密、群画像、年度报告
- 导出 JSON/HTML/Excel/CSV/Markdown
- 自带 HTTP API（:5031）

**但它有一块是空的**，README 原文：

> 需要配套的第三方可插拔组件（本项目依赖**自定义的原生解密组件，需自行设计**）

也就是说：**WeFlow 把"解密内核"外包给用户了。** 我们今天做的正是这块。

### 分工判断

- **给人看**（浏览、年度报告、可视化）→ WeFlow 更强，不要重造
- **给 AI 看**（问答、检索、喂上下文）→ WeFlow 的 GUI 帮不上，且它的 HTTP API 仍在早期阶段、接口会变

所以：**内核自研，GUI 不做，接口做成 MCP。**

## 三、为什么是 MCP 而不是 Skill 直接干活

MCP 是**能力层**，Skill 是**知识层**，两者不冲突，是上下游：

```
┌─────────────────────────────────────────┐
│  AI 客户端                              │
│  (WorkBuddy / Claude Code / Codex)      │
├─────────────────────────────────────────┤
│  Skill: wechat-local-history            │  ← 教 AI 何时/怎么用
│  "要读某个群 → 调 wechat_read_chat"     │
├─────────────────────────────────────────┤
│  MCP Server (stdio)  ✅ 已装            │  ← 6 个工具
│  list_chats / read_chat / search        │
│  recent / summary / system_events       │
├─────────────────────────────────────────┤
│  wxdec-skill 仓库（Python）             │  ← 内核
│  提密 → 解密 → 查询                     │
├─────────────────────────────────────────┤
│  微信本地数据 D:\WeChatData\xwechat_files │
└─────────────────────────────────────────┘
```

只做 Skill 不做 MCP：AI 只能通过 Bash 跑脚本，能干但笨、每次要现拼命令。
只做 MCP 不做 Skill：AI 不知道有这个工具，不会主动用。

## 四、本次实测的关键数据

> 以下是**某一台机器某个账号**的实测值，用来证明这套方案在百万级数据上跑得通。
> 换账号后这些数字会变，但技术路径不变。

| 项 | 数值 |
|---|---|
| 解密库 | 21 个 / 1.3 GB 明文 |
| 消息总数 | 1,157,006 条 |
| 会话 | 7,521 个（含 179 个群） |
| 群成员 | 26,659 人 |
| `integrity_check` | ok |
| 微信版本 | 4.1.15.56（安装于 `<微信安装目录>\4.1.15.56`） |
| 数据根目录 | `<WECHAT_DATA_ROOT>`（安装时自动探测） |
| 账号 | 一个微信账号一个目录；密钥按账号派生 |
| 加密参数 | SQLCipher 4 / PBKDF2-HMAC-SHA512 / 256000 次 / AES-256-CBC / page 4096 |

## 五、最有价值的技术突破（换版本要重做的东西）

### 5.1 密钥提取：hook SHA-512 压缩函数，从 HMAC ipad 反推 raw key

微信 4.x 把 OpenSSL **全部静态链接**进 DLL —— 扫了 8773 个导出符号，
**没有任何** PBKDF2 / HMAC / sqlite3_key 可钩。

唯一可行路径：钩 SHA-512 压缩函数，在 HMAC 内层取到 `key ^ 0x36` 的 ipad 块，
XOR 回 0x36 得到明文 key。

**三个必须同时满足的条件（缺一不可）：**

1. **钩全部模块**，不能只钩 `Weixin.dll`
   → 只钩 `Weixin.dll` 会抓到 TLS 的 HMAC，验证永远不过
2. **钩到的候选必须落盘再离线验证**
   → 在线验证会漏；本例中 4 个候选全是 TLS 假货
3. **微信登录的那一刻**
   → SQLCipher 只在**首次打开某个库**时派生密钥；登录会重新派生，这是最可靠的触发点

**SQLCipher 4 派生关系（务必记牢）：**
```
enc  = PBKDF2-HMAC-SHA512(raw_key, db_salt, 256000, 32)   # 每库独立
mack = PBKDF2-HMAC-SHA512(enc, db_salt ^ 0x3A, 2, 32)
```
`db_salt` 与 `K1(enc)` 在内存 ctx 结构体里**相邻**，但 `key` 指针是独立的 —— 
所以"按 salt 邻域扫内存找 key"这个直觉**注定扑空**，必须靠 `kdf_iter=256000` 常量定位 ctx。

### 5.2 大库必须流式解密

`decrypt_all.py` 把整个文件读进内存 → 676MB 的 `message_0.db` 直接 **MemoryError**。
改用 `decrypt_stream.py` 逐页解密，165,284 页 **8 秒**跑完。

> 踩坑：SQLCipher 里 salt 占页首 16 字节，密文从第 16 字节开始，
> IV 在 `page[4096-80 : 4096-80+16]`。从 offset 0 解整页 → 页头校验必挂。

### 5.3 待避的 8 条死路

| # | 路线 | 为什么失败 |
|---|---|---|
| 1 | 内存扫 `x'<64hex><32hex>'` | 4.1.15.56 把 key 加密缓存了；且该工具把迭代写成 2 次（错） |
| 2 | `wx-cli` 自动检测 | 只认 `~/Documents/xwechat_files`；认不出 D 盘自定义路径 |
| 3 | frida **spawn gating** | **Windows 根本不支持**（`not yet supported on this OS`），仅 Linux/macOS |
| 4 | hook `sqlite3_key` | 4.x 里是静态内联，非导出符号 → `not a function` |
| 5 | attach 单个 Weixin.exe | 4.x 多进程，开库做 PBKDF2 的不一定是内存最大的那个 |
| 6 | `key_info.db` 里的信封 | protobuf blob 内**没有**明文密钥，滑窗验证全落空 |
| 7 | 只钩 `Weixin.dll` | 抓到 TLS 的 HMAC 假货，验证永远不过 |
| 8 | `tasklist` 查进程 | 沙箱环境下返回 0 行；改用 `frida.enumerate_processes()` |

## 六、已完成的安装配置

### MCP 注册（`C:\Users\<user>\.workbuddy\mcp.json`）
```json
{
  "mcpServers": {
    "wechat-local": {
      "command": "<REPO>\\.venv\\Scripts\\python.exe",
      "args": ["<REPO>\\server.py"],
      "env": {
        "WECHAT_DATA_ROOT": "<安装时探测到的数据根目录>",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```
> 新增 MCP 不会自动激活。需在连接器管理页右上角的自定义连接器入口点「信任」。

### 目录联接（让工具能自动探测数据目录）
```
C:\Users\<你>\Documents\xwechat_files  →  <你的真实数据目录>
```

### 手动命令（不需要 MCP 也能用）
```bash
cd <REPO>
V=.venv/Scripts/python.exe

$V scripts/windows/wxread.py list                      # 列群（带群名）
$V scripts/windows/wxread.py messages "<群wxid>" --limit 100
$V scripts/windows/wxread.py export "<群wxid>" out.txt
$V scripts/windows/wxread.py search "关键词"             # 全文搜索

WECHAT_DATA_ROOT="<数据根目录>" $V scripts/windows/decrypt_all.py   # 全量解密（自动匹配账号）
```

## 七、后续路线（三选一，按优先级）

### P0：把内核固化成 Skill（30 分钟）
写 `~/.skills-manager/skills/wechat-local-history/SKILL.md`，
声明式教 AI：何时调哪个工具、群 ID 怎么查、失败怎么办。
**这是投入产出比最高的一步。**

### P1：密钥刷新守护（1 小时）
密钥随微信登录/更新失效。需要一个 `wxkey --refresh` 一键命令，
自动完成：检测微信进程 → 挂钩 → 等登录 → 落盘新 key → 重解密。
目前这条链是手工的，微信重启一次就得重来一遍。

### P2：富媒体解码（半天起）
图片/表情/视频的正文是 WCDB **单独加密**的，当前只能标类型。
仓库有 `scripts/common/export_media.py`、`voice_decode.py`、
`transcribe_db.py` 可接，但需要额外密钥/模型。

### P3：Agent 化（可选）
让 AI 自动做「年度聊天报告」「群活跃度分析」「从聊天里挖选题」
—— 你的内容创作线（@your-handle）直接能用。

## 八、安全边界（不可妥协）

- 密钥（`key_windows.txt`）= 微信账号的钥匙，**绝不外传、不入 git、不打印到对话**
- 解密后的明文库涉及群友隐私，**只在本地**
- 只操作用户本机、用户有权访问的数据
- 已在 `SKILL.md` 里写死「Never print a raw key」
