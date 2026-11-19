# WAA Signer (Google AI Studio 本地 IPC 签名进程)

基于 [Mag1cFall/AIStudio2API](https://github.com/Mag1cFall/AIStudio2API) 核心内核抽离的本地 WAA (Web App Attestation / BotGuard) 证明签名进程，通过 `stdin`/`stdout` 多路复用 NDJSON IPC 与主网关通信。

## 方案特性
1. **无端口纯 IPC 与嵌入式运行时**：基于 SpiderMonkey 152 引擎与 Firefox 152 宿主环境，不监听任何 TCP 端口，随主进程 `stdin` EOF 自动退出，彻底避免端口冲突与孤儿进程。
2. **实测性能与热复用寿命**：
   - 首次冷启动（加载页面与初始化 VM）：约 5-8 秒
   - 二次热复用：约 10-30 毫秒
   - 热 Worker 最长复用时长为 **6 小时**（`maxWorkerAge`），超时后自动回收并冷启动刷新页面构建号与 `visitId`，防止长驻老化触发 403
3. **环境特征匹配**：
   - 提取与当前 VM 会话绑定的 `User-Agent`、`X-AIStudio-Visit-Id`、`X-Goog-Ext-519733851-Bin`、`X-Goog-Api-Key` 等动态头并在响应中返回。
   - 避免外层 HTTP 请求头与 Proof 加密声明的客户端环境不一致导致 403 错误。
4. **IPC 方法支持**：
   - `health`：返回引擎状态与当前活跃 Worker 数。
   - `proof.mint`：并发多路复用签名，按 `id` 关联返回 `!proof` 与会话头。
   - `shutdown`：优雅回收 WorkerPool 并退出。
---

## 跨平台一键编译

### 前置要求（编译脚本可自动检测并下载安装）
- Go 1.27+（或 Go 1.21+ 配合脚本自动启用 `GOTOOLCHAIN=auto`）
- C/C++ 编译器：强烈推荐 `zig`（免配置静态链接，脚本会自动补齐；macOS 优先使用 Xcode Command Line Tools `clang`）

### 编译命令

#### Linux / macOS / Android (Termux)
在 `waa-signer/` 目录下执行：
```bash
bash build.sh
```
脚本会自动识别 CPU 架构与操作系统（Linux x64/arm64、macOS arm64/amd64），自动拉取对应的官方静态 `spidermonkey-v152.0.4` 依赖，并产出单一无外部依赖的二进制文件 `bin/waa-signer`。

#### Windows (CMD / PowerShell)
在 `waa-signer\` 目录下直接运行：
```bat
build.bat
```
或在 PowerShell 中执行：
```powershell
.\build.ps1
```
脚本会自动完成依赖下载与编译，产出 `bin\waa-signer.exe`。
> **Windows 编译小贴士**：推荐使用轻量免配置的 Zig 编译器（执行 `winget install zig.zig` 即可一键就绪）。

#### Docker
本项目主 Dockerfile 已内置多阶段自动编译阶段，直接 `docker build -t aistudio-api .` 即可自动构建并内置 `waa-signer`，容器启动时自动拉起，无需手动操作。
---

## 运行与参数说明

```bash
./bin/waa-signer [参数]
```

| 参数 | 环境变量 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `-check` | - | `false` | 自检二进制可用性并输出 JSON 后退出 |
| `-proxy` | `AISTUDIO_PROXY` / `HTTPS_PROXY` | 自动探测常见本地代理端口 | 上游代理 URL (HTTP/HTTPS/SOCKS5) |
| `-state-dir` | `WAA_STATE_DIR` | 系统临时目录下的 `aistudio_waa_workers` | Worker 指纹与解释器缓存目录 |
| `-locale` | `WAA_LOCALE` | `en-US` | 浏览器环境语言 |
| `-timezone` | `AISTUDIO_TIMEZONE` | `America/Los_Angeles` | 浏览器环境时区 |

测试套件（自动配置 CGO 环境变量）：
```bash
bash waa-signer/build.sh --test
```

---

## Stdio NDJSON IPC 协议定义

进程启动后立即在 `stdout` 输出一行就绪事件（日志均输出至 `stderr`）：
```json
{"id":0,"event":"ready","ok":true,"engine":"spidermonkey-152"}
```

### 1. 探活 `health`
请求：`{"id":1,"method":"health"}`
响应：`{"id":1,"ok":true,"engine":"spidermonkey-152","workers":1}`

### 2. 签名 `proof.mint`
**请求 (`params`)**：
```json
{
  "id": 2,
  "method": "proof.mint",
  "params": {
    "cookies": "SID=...; SAPISID=...; __Secure-1PSID=...",
    "authUser": 0,
    "g1Tier": "TIER1",
    "model": "models/gemini-3.8-flash",
    "prompt": "用户提示词文本",
    "wireContents": [],
    "proxy": "可选单次请求覆盖代理 URL",
    "forceRecreate": false
  }
}
```
**响应**：
```json
{
  "id": 2,
  "ok": true,
  "poolState": "hot",
  "digest": "7cf1b1f9ef045379fb27a3177f0dd73f671839da3db441cbaae0cac58145d80f",
  "proof": "!enmleR3NAAY187QsFfNCJe3GmEsUzL87AEABEArZ1OshTDyK2...",
  "headers": {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
    "Authorization": "SAPISIDHASH ...",
    "Cookie": "...",
    "X-AIStudio-G1-Tier": "TIER1",
    "X-Aistudio-Visit-Id": "v1_...",
    "X-Goog-Api-Key": "AIzaSy...",
    "X-Goog-Authuser": "0",
    "X-Goog-Ext-519733851-Bin": "CAASAUIwATgEQABQBFgDYgJKUHAAeAGQAQCYAQE="
  }
}
```
