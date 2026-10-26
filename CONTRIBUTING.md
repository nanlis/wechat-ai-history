# 贡献指南

## 这是什么

把本机微信 4.x 的聊天记录，变成 AI 可以直接调用的能力。
形态是 **MCP server + Agent Skill + Windows 解密链路**。

## 开发环境

```bash
git clone https://github.com/nanlis/wechat-ai-history.git
cd wechat-ai-history
bash install.sh          # 探测环境、建 venv、装依赖
```

或手动：

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

## 跑测试

```bash
.venv/Scripts/python.exe -m pytest -q
```

测试**不需要**微信运行，也**不需要**真实密钥——全部用合成数据库。

改完先跑：

```bash
.venv/Scripts/python.exe scripts/common/doctor.py --json
```

## 目录结构

| 路径 | 作用 |
|---|---|
| `server.py` | MCP server，7 个 `wechat_*` 工具 |
| `db.py` / `crypto.py` / `config.py` | 查询与解密内核 |
| `accounts.py` | 账号作用域：让密钥自己认账号 |
| `xmlmsg.py` | 微信 XML 消息体解析（图片/语音/视频/表情/位置/名片） |
| `scripts/windows/` | 提密钥、解密、只读 CLI |
| `scripts/common/` | 跨平台内核（查询、快照、诊断） |
| `skill/SKILL.md` | 教 AI 何时调、怎么调 |
| `references/` | 安装与故障排查手册 |
| `docs/` | 架构、设计决策、踩坑记录 |

## 隐私红线（不可协商）

这个仓库是**公开**的。以下规则由测试强制执行，违反会直接让 CI 失败：

1. **不提交任何真实数据**——账号 id、群 id、联系人名、聊天内容、坐标。
2. **不提交机器专属路径**——数据目录、微信安装目录、仓库路径必须自动探测或用占位符。
3. **不提交密钥或明文库**——`key_windows.txt`、`decrypted/`、`*.db` 已在 `.gitignore`。
4. **测试数据必须是合成的**——用 `wxid_example…` 这类明显是假的 id。

对应的守卫测试在 `tests/test_account_portability.py`。提交前跑一遍：

```bash
.venv/Scripts/python.exe -m pytest tests/test_account_portability.py -q
```

## 提交规范

- 一个提交做一件事
- 提交信息首行用中文祈使句：`feat:` / `fix:` / `docs:` / `test:` / `refactor:`
- 涉及性能的改动，把**修复前后的实测数字**写进提交信息
- 不要 force push 到 `main`

## 版本敏感的代码

以下部分跟**微信版本**强绑定，换版本可能整体失效，改动前务必实测：

- `scripts/windows/hook_raw_key_broad.py`（密钥提取）
- `scripts/windows/find_ctx.py`（ctx 定位）
- `scripts/common/crypto.py`（SQLCipher 参数）

已知死路见 [`docs/design-decisions.md`](docs/design-decisions.md) 的 5.3 节——
**先看那张表，别重复踩坑**。

## 安全提醒

只操作用户本机、用户有权访问的数据。解密后的明文库涉及群友隐私，全程本机、零联网。
请遵守相关法律法规。
