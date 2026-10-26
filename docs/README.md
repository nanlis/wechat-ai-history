# docs/ 索引

按「想解决什么问题」来找文档。

## 先看这三篇

| 文档 | 讲什么 |
|---|---|
| [architecture.md](architecture.md) | 三层结构（内核 / MCP / Skill）为什么这么切，每层交付了什么 |
| [design-decisions.md](design-decisions.md) | 关键取舍 + **8 条已排除的死路** + SQLCipher 派生关系 |
| [windows-4.x-key-extraction.md](windows-4.x-key-extraction.md) | 微信 4.x 怎么拿到密钥（SHA-512 hook 方案） |
| [windows-4.x-chat-reader.md](windows-4.x-chat-reader.md) | 微信 4.x 数据模型（分表规则、消息类型、zstd 压缩） |

## 操作手册

| 文档 | 讲什么 |
|---|---|
| [windows-data-root.md](windows-data-root.md) | 数据根目录怎么定位、怎么改 |
| [windows-wal-snapshots.md](windows-wal-snapshots.md) | WAL 快照：为什么要做一致性读取 |
| [chat-list-pagination.md](chat-list-pagination.md) | 会话列表分页的坑 |
| [media-manifest-proposal.md](media-manifest-proposal.md) | 富媒体导出清单的设计提案 |
| [pull-request-archive.md](pull-request-archive.md) | 两个已合并 PR 的内容存档（XML 解析 + 搜索性能、账号无关化） |

## 研发过程记录

这几篇是**当时怎么摸出来的**，含具体试错过程。方案已经稳定成型的场景不用读，
换微信版本重做时再回来翻。

| 文档 | 讲什么 |
|---|---|
| [2026-06-05-windows-keyextract-findings.md](2026-06-05-windows-keyextract-findings.md) | 密钥提取的早期排查记录 |
| [2026-06-05-windows-port-design.md](2026-06-05-windows-port-design.md) | Windows 移植设计 |
| [2026-06-05-windows-port-plan.md](2026-06-05-windows-port-plan.md) | 移植实施计划 |
| [2026-06-06-windows-raw-key-journey.md](2026-06-06-windows-raw-key-journey.md) | 拿到 raw key 的完整过程 |

> 所有文档均已脱敏：不含任何真实账号 id、群 id、联系人名、聊天内容或机器专属路径。
> 这条规矩由 `tests/test_account_portability.py` 强制执行。
