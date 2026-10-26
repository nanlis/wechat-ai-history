# Pull Request 存档

> 本文件是仓库历史中两个已合并 PR 的内容存档。
> 原始 PR 已删除——GitHub 会为已合并 PR 保留 `refs/pull/N/head` 引用，
> 而该引用指向重写前的历史（含真实账号 id、群 id 与坐标）。
> 删除 PR 是为了消除这个残留入口，内容本身在此完整保留。

---

## PR #1 · feat(xml): 解析图片/语音/视频/表情/位置消息体 + 修复搜索内存与性能

## 背景

`nanlis/wechat-ai-history` 已经把微信 4.x 聊天记录接进 AI 链路。本次实测发现三处真实缺陷并修复。

## 改动一：XML 消息体解析（新增 xmlmsg.py）

微信把图片、语音、视频、表情、位置、名片这几类消息存成 XML 文档而非纯文本。仓库原有的 `decode_message_content()` 只剥掉群聊发信人前缀，调用方拿到的仍是**未解析的裸 XML 标签**，直接喂给模型就是一堆噪声。

实测证据（`message_1.db` 真实数据）：

```
local_type=3  (图片) → '<msg><img aeskey="f60f44fb..." cdnthumburl="3057..." length="81920" />'
local_type=34 (语音) → '<msg><voicemsg voiceformat="4" length="33177" voicelength="17500" ... />'
local_type=43 (视频) → '<msg><videomsg aeskey="ac8aa11f..." playlength="8" ... />'
local_type=47 (表情) → '<msg><emoji fromusername="wxid_example01" md5="b5e237a3..." />'
local_type=48 (位置) → '<msg><location x="30.000000" y="111.000000" poiname="示例地点..." />'
```

新增模块按类型输出可读摘要与结构化字段：

| 类型 | 修复前 | 修复后 |
|---|---|---|
| 图片 | 裸 XML 标签 | `[图片] 80KB` |
| 语音 | 裸 XML 标签 | `[语音] 17.5秒` |
| 视频 | 裸 XML 标签 | `[视频] 8秒` |
| 表情 | 裸 XML 标签 | `[表情] 表情 b5e237a3a15d` |
| 位置 | 裸 XML 标签 | `[位置] 示例地点 (30.000000, 111.000000)` |
| 名片 | 裸 XML 标签 | `[名片] 元宝` |

同时覆盖 `local_type` 高位子类型：微信把子类型编进低 16 位以上，`326417514545` 这类值仍能正确归一化到 49。

## 改动二：修复搜索在大分片上的 MemoryError

根因是 fallback 路径用 `hex(message_content)` 把二进制膨胀成两倍长的十六进制字符串，再叠加跨 200 张会话表的全局 `ORDER BY` 物化进排序临时表，峰值内存足以打爆进程。表现为**间歇性崩溃**。

四组查询形态对比（同一分片，11.1 万行）：

| 形态 | 耗时 | 结果 |
|---|---|---|
| hex + 全局 ORDER BY（修复前） | 7.4s | **间歇性 MemoryError** |
| hex + 无排序 | 1.8s | 通过 |
| 裸内容 + ORDER BY | 1.8s | 通过 |
| 裸内容 + 无排序 | 1.2s | 通过 |

三处修复：

1. **`db.py` 增加 `PRAGMA temp_store=FILE`** — 排序临时表落盘，峰值内存可控，且同查询从 7.1s 降到 2.9s
2. **fallback 改回传原始 blob 而非 hex 副本** — 消除 hex 往返，8.9s → 2.4s
3. **关键词预筛下沉到 Python 层** — 只让可能命中的行走完整格式化路径

实测效果：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 搜索「三峡」 | **600s 超时无结果** | 19s 出结果 |
| 搜索「视频号」 | 超时 | 25s 出结果 |
| 连续 4 个不同关键词 | 中途 MemoryError | 全部稳定通过（10.8s / 14.3s / 19.0s / 9.5s） |

结果正确性已核对：搜「三峡」命中的是聊天里提到三峡大坝的真实记录，搜「视频号」命中的是视频号卡片。

## 改动三：修复 capture_key 忽略调用方传入的 pids

`attach_new()` 自行调用 `tasklist` 重新扫描 Weixin.exe 进程，传入的 `pids` 参数形同虚设。带来两个问题：

- 调用方已知目标 pid 时仍要重新扫描，多一次进程枚举开销
- **依赖注入失效**，测试无法用桩 device 驱动 `capture_key`

这导致远端基线上 `test_windows_extractor.py` 两个用例直接失败（`RuntimeError: could not attach to any Weixin.exe process`）。已确认该失败在改动前就存在，非本次引入。

改为先 hook 调用方传入的 pid，再扫描 tasklist 补充新进程，并兼容 `pids` 传入单个 int 的形式。

## 测试

- 新增 `tests/test_xml_messages.py`：20 个用例，覆盖 5 类 XML 消息解析、高位子类型归一化、畸形 XML 降级、空消息体、标签命中预筛、zstd 判定
- 全量回归 **252 passed, 1 skipped**（修复前远端基线为 2 failed, 250 passed）

## 安全性

全部为只读改动，未触碰 `decrypted/` 等数据目录，未修改任何解密或密钥提取逻辑。




---

## PR #2 · feat: 让整套能力与具体微信号解绑，任何账号都能用

## 问题

这套东西此前是「为某一个微信号定做」的——解密目录、`wxread.py`、`refresh_key.sh` 都写死了某一个账号目录和某一台机器的路径。换个微信号、换台电脑就跑不起来。

## 实测结论

| 作用域 | 结论 |
|---|---|
| 技术内核（提 key / 解密 / zstd / XML 解析 / 搜索 / MCP / Skill） | 与账号**完全无关** |
| raw key | **按账号派生**，一把 key 只开得了它所属账号的库 |
| 明文库 / 导出产物 | 只属于本机本账号，已全部 gitignore |

本机两个账号实测：当前 key 能开活跃账号，开不了旧账号。

## 改动

**新增 `accounts.py`（账号作用域内核）**
- `key_opens_db()`：用页1 HMAC 认证判断「这把 key 开得了哪个库」
- `select_accounts()`：让**密钥自己认账号**，而不是靠目录时间戳猜

**修掉 4 处硬绑死**
- `decrypt_all.py`：不再用 mtime 猜活跃账号 → 拿 key 逐个探测所有账号目录，只解开能打开的那个；新增 `--account`
- `wxread.py`：解密目录从写死账号 → 动态解析（原来别人机器上直接废）
- `refresh_key.sh`：数据目录自动探测；删掉写死的账号目录段
- `verify_key.py`：原本**只认 macOS 路径**（Windows 上是废的）→ 跨平台，并能报告「这把 key 属于哪个账号」

**其他**
- `db.py`：支持 `WECHAT_ACCOUNT` 锁定账号；`get_my_wxid` 改用可靠 wxid 格式匹配（原正则会把无设备后缀的 `wxid_abc` 误剥成 `wxid`）
- `server.py`：新增 `wechat_list_accounts` 工具
- `find_ctx.py` / `hook_raw_key_broad.py`：去掉写死的 `D:/Document` 路径

**隐私**
公开仓库此前含真实 wxid、群 id `00000000000@chatroom`、联系人名等，已全部替换为占位符。

## 证明它真的通用

新增 `tests/test_e2e_new_account.py`：合成一个**代码从未见过**的账号目录名 + **全新随机密钥**的加密库，驱动真实的 `decrypt_all.py main()` 走完整流程，校验它认出了这个陌生账号并解出正确明文。另有一例验证不属于任何账号的 key 必须明确报错。

新增 `tests/test_account_portability.py`：源码里出现具体 wxid、机器专属路径、真实群 id 或联系人名 → 测试直接失败。

## 测试

**279 passed, 1 skipped**（原 263）

