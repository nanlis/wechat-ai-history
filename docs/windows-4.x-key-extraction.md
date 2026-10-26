# 微信 4.x 本地聊天记录提取（Windows）

## 一句话
微信 4.x 数据库用 SQLCipher 4 加密（PBKDF2-HMAC-SHA512 **256,000 次**），密钥每次登录随机生成、只存在于运行中进程的内存里，静态文件无法解开。必须从**运行中的**微信进程里拿到密钥。

## 环境（已实测）
- 微信安装：`<微信安装目录>\Weixin.exe`，版本 `4.1.15.56`，核心 DLL `<微信安装目录>\<版本号>\Weixin.dll`（约 200MB）
- 数据目录：`<WECHAT_DATA_ROOT>/<wxid>_<device>/db_storage`（微信设置 → 文件管理里改过位置）
- 账号目录：每个微信账号一个目录；一台机器可能同时存在多个（换过号就会留下旧的）
- **账号怎么认**：raw key 按账号派生，一把 key 只开得了它所属的账号。
  别用 mtime 猜活跃账号——用 `decrypt_all.py`，它会拿 key 逐个探测所有账号目录，只解开能打开的那个。
  需要锁定某个账号时：`--account <目录名或wxid>`，或环境变量 `WECHAT_ACCOUNT`。
- 工具：仓库根目录即工作目录，venv 在 `<REPO>/.venv`

## 关键参数
```python
PBKDF2-HMAC-SHA512, 256000 iterations, dklen=32   # KDF，别用 2 次
AES-256-CBC, HMAC-SHA512, page_size=4096, reserve=80 (IV 16 + HMAC 64)
```

## 三条提取路线（按可靠性排序）

### 路线 A：hook `sqlite3_key`（最稳，不用重启）
`scripts/windows/extract_key_live.py` —— attach 到已运行的微信，hook SQLCipher 导出的
`sqlite3_key` / `sqlite3_key_v2`，微信每次开库都会调用，密钥作为参数直接到手。
**前提：微信已在运行。**

### 路线 B：内存 salt 扫描（同样不用重启）
`extract_key_live.py --scan` —— 在进程内存里搜 `message_0.db` 首页的 16 字节 salt，
SQLCipher 把 salt 和派生密钥放在一起，扫到 salt 周围的 32 字节窗口，
AES 验证成 K1 / PBKDF2 验证成 raw key。

### 路线 C：hook SHA-512 抓 PBKDF2 的 HMAC ipad（需要重启）
`scripts/windows/extract_raw_key.py` —— raw key 内存里有三重保护（AES-NI 轮密钥、
用后擦除、安全堆），暴力扫不出来；但 PBKDF2 派生 K1 时，HMAC 的 ipad 块就是
明文 `key XOR 0x36`，hook `Weixin.dll` 里的 sha512_block 入口能截获。
**必须从桌面手动启动微信**（SSH/schtasks 起的微信是空壳，不开库），且要在微信
开库之前挂钩上——这就是它容易失败的原因。

## 各路线踩过的坑
1. `328336690/wechat-decrypt` 的内存扫描法对 4.1.15.56 **无效**：新版把 raw key
   加密缓存，内存里只匹配到 8 个 hex 模式全不匹配；而且它把 KDF 迭代写成 2 次（错）。
2. `jackwener/wx-cli`（本机 `~/bin/wx.exe`）`init` 卡在自动检测微信目录失败——它硬编码
   探测 `APPDATA\Tencent\xwechat` 和 `WeChat Files`，认不出改到 D 盘的路径；
   daemon 又要求 `all_keys.json` 已存在。仓库已被 DMCA 无法克隆。
3. frida 的 **spawn gating 在 Windows 上不支持**（`ERR: not yet supported on this OS`），
   Linux/macOS 才有。别在 Windows 上浪费时间试这条路。
4. attach 单个进程会漏：微信 4.x 多进程，真正开库做 PBKDF2 的不一定是内存最大的那个。
   要对所有 Weixin.exe 进程挂钩，或持续轮询补挂新进程。
5. GitHub 直连失败（`SSL_read unexpected eof` errno 10004），克隆要走 karing 代理：
   ```bash
   GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=http.proxy GIT_CONFIG_VALUE_0=http://127.0.0.1:3067 \
   GIT_CONFIG_KEY_1=https.proxy GIT_CONFIG_VALUE_1=http://127.0.0.1:3067 git clone ...
   ```

## 权限
Git Bash 里的 Python 可以 `OpenProcess(PROCESS_VM_READ)` 成功打开 Weixin.exe，
frida attach 也成功——本机 Git Bash 已带管理员令牌。技能文档里"Git Bash 无 admin token"
的说法在本次实测不成立。（`reg.exe` 在沙箱里被策略拦截，别用。）

## 拿到密钥之后
```powershell
$env:WECHAT_DATA_ROOT = '<你的数据根目录>'
& "<仓库目录>\.venv\Scripts\python.exe" scripts\windows\decrypt_all.py
```
产出明文库在 `<仓库目录>\decrypted\<wxid>\db_storage\`，之后用
`scripts\common\query.py`（查）/ `export_chat.py`（导出）读。

## 安全红线
- 密钥落盘在 `key_windows.txt`（0o600），**不可外传、不可提交到 git**。
- `decrypted\` 明文库同样敏感，不要上传/分享。
- 只处理用户本人设备上的、用户授权访问的数据。
