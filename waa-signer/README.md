# WAA Signer (Google AI Studio 独立原生签名服务)

基于 [Mag1cFall/AIStudio2API](https://github.com/Mag1cFall/AIStudio2API) 核心内核抽离的轻量级、纯原生 WAA (Web App Attestation / BotGuard) 签名与协议服务。

## 特性亮点
1. **纯净脱离无头浏览器**：嵌入式 SpiderMonkey 152 运行时 + Firefox 152 真实 DOM / WebIDL 宿主现场，单机毫秒级极速生成高精度 snapshot proof (`!` 开头官方原生 token)。
2. **极速热缓存**：
   - 首次冷启动（加载页面与初始化 VM）：~5-8 秒
   - 二次热复用：**~10-30 毫秒**
3. **100% 协议与环境对齐**：
   - 自动获取与当前 VM 会话强绑定的 `User-Agent`、`X-AIStudio-Visit-Id`、`X-Goog-Ext-519733851-Bin`、`X-Goog-Api-Key` 等动态公共头。
   - 杜绝因外层 HTTP 请求头与 Proof 内部加密声明指纹不一致引发的 403 权限拒绝。
4. **双模接口支持**：
   - `POST /v1/proof/mint`：与 `aistudio-api` 的 `RemoteProofSigner` 规范 100% 兼容。
   - `POST /v1/rpc/send`：直接使用 Firefox 152 原生 uTLS / HTTP2 / HTTP3 连接池代理发送受保护 RPC。

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
