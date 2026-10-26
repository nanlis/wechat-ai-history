# wechat-decrypt 测试

## 文件
| 文件 | 作用 |
|---|---|
| `test_e2e.py` | 端到端测试（双端 platform 自动分派，含系统事件与 JSON 输出） |
| `check_consistency.py` | 开发仓与一个已安装运行单元的一致性校验；跨 wecom 检查按需启用 |

> 另有单元测试(pytest, **无需真实数据**)在 `tests/`(langlobal):`python3 -m pytest tests/ -v`

## 跑法
```bash
python3 e2e/test_e2e.py            # 端到端 13 项（轻量）
python3 e2e/test_e2e.py --full     # 15 项，增加 media/export（慢）
python3 e2e/check_consistency.py                         # 自动发现 .grok/.kimi-code/.agents/.codex/.claude
python3 e2e/check_consistency.py --skill-dir /path/to/skill
python3 e2e/check_consistency.py --with-vendored         # 额外检查 wecom 共享文件
```
退出码 0=全过 / 1=有失败,可接 CI。

## 端到端前提(不满足必挂)
先提 key + 解密:
- **macOS**: `scripts/macos/extract_key.sh`(扫码登录)
- **Windows**: `scripts/windows/extract_raw_key.py`(重启微信)→ `scripts/windows/decrypt_all.py`

## 改代码后 —— 确保一致性三步
```
改代码 → pytest(合成库) → check_consistency.py(运行单元漂移) → test_e2e.py(真实数据)
```
跨项目总指南见 `Langlobal/decrypt-shared/TESTING.md`;架构目标骨架见 `Langlobal/decrypt-shared/decrypt-modules-alignment.md`。
