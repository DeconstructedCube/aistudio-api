# WAA Signer (Google AI Studio 独立 Go 签名服务)

基于 [Mag1cFall/AIStudio2API](https://github.com/Mag1cFall/AIStudio2API) 核心内核抽离的独立 WAA (Web App Attestation / BotGuard) 证明签名服务。

## 方案特性
1. **独立进程与嵌入式运行时**：基于 SpiderMonkey 152 引擎与 Firefox 152 宿主环境，直接执行 BotGuard 挑战，无需启动完整无头浏览器。
2. **实测性能参考**：
   - 首次冷启动（加载页面与初始化 VM）：约 5-8 秒
   - 二次热复用：约 10-30 毫秒
3. **环境特征匹配**：
   - 提取与当前 VM 会话绑定的 `User-Agent`、`X-AIStudio-Visit-Id`、`X-Goog-Ext-519733851-Bin`、`X-Goog-Api-Key` 等动态头并在响应中返回。
   - 避免外层 HTTP 请求头与 Proof 加密声明的客户端环境不一致导致 403 错误。
4. **接口支持**：
   - `POST /v1/proof/mint`：与 `aistudio-api` 签名客户端接口兼容。
   - `POST /v1/rpc/send`：使用内部 Firefox 152 uTLS / HTTP2 连接池直接代理发送受保护 RPC。
---

## 跨平台一键编译

### 前置要求
- Go 1.22+
- `zig`（推荐，可自动交叉静态编译）或 `clang` / `gcc`

### 编译命令
在 `waa-signer/` 目录下直接执行：
```bash
bash build.sh
```
`build.sh` 会自动检测当前机器架构（x86_64 或 arm64），自动下载解压官方静态 `spidermonkey-v152.0.4` 依赖，并编译出无外部动态库依赖的纯静态单一二进制产物 `bin/waa-signer`。

---

## 运行与参数说明

```bash
./bin/waa-signer [参数]
```

| 参数 | 环境变量 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `-listen` | `LISTEN_ADDR` / `PORT` | `127.0.0.1:8081` | HTTP 服务监听地址 |
| `-proxy` | `AISTUDIO_PROXY` / `HTTPS_PROXY` | 自动探测 7890/10808 | 上游代理 URL (HTTP/HTTPS/SOCKS5) |
| `-state-dir` | `WAA_STATE_DIR` | 系统临时目录下的 `aistudio_waa_workers` | Worker 指纹与解释器缓存目录 |
| `-locale` | `WAA_LOCALE` | `en-US` | 浏览器环境语言 |
| `-timezone` | `AISTUDIO_TIMEZONE` | `America/Los_Angeles` | 浏览器环境时区 |

### 后台守护启动示例
```bash
nohup ./bin/waa-signer -listen "127.0.0.1:8081" -proxy "http://127.0.0.1:7890" > waa-signer.log 2>&1 &
```

---

## HTTP 接口定义

### 1. 探活 `GET /health`
```json
{
  "engine": "spidermonkey-152",
  "ok": true,
  "status": "ok",
  "workers": 1
}
```

### 2. 签名 `POST /v1/proof/mint`
**请求体 (JSON)**：
```json
{
  "cookies": "SID=...; SAPISID=...; __Secure-1PSID=...",
  "authUser": 0,
  "g1Tier": "TIER1",
  "model": "models/gemini-3.8-flash",
  "prompt": "用户提示词文本",
  "wireContents": []
}
```
**响应体 (JSON)**：
```json
{
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

### 3. 原生连接池转发 `POST /v1/rpc/send`
直接复用底层已建立的 Firefox 152 uTLS / HTTP2 / HTTP3 官方连接池向 Google MakerSuiteService 发送原生请求。
