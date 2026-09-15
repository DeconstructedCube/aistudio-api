# aistudio-api

Google AI Studio 反向代理服务，提供原生的 Google Gemini API 协议接口。

[English](./README_EN.md)

---

## 功能特性

- **协议兼容**：支持官方 Gemini API 规范（`/v1beta/...`），涵盖 Thinking 思维链、Multimodal 多模态输入、Function Calling 工具调用与图像生成。
- **模型同步**：自动从 Google 上游同步并动态获取最新可用模型列表。
- **账号调度与容灾**：多账号 Sticky 黏性调度，按模型独立记录 429 配额状态与自动故障转移。
- **Cookie 批量探活**：支持导入单份包含多个登录身份的 Cookie，自动递归探活子账号（`u/0`, `u/1`...）并分化建档。
- **内置工具支持**：支持 Google Search 联网搜索、Google Maps、代码执行沙箱等官方扩展工具。
- **Web 控制台**：内置轻量 Web 仪表盘，支持账号导入管理、实时调用监控、在线编辑 `config.yaml` 并热重载。
- **原生轻量 CDP**：纯 Python 异步 WebSocket 直连 Chrome DevTools Protocol，无需 Node.js、Playwright 或外置驱动。

> **内存占用参考**：纯 Python 服务常驻约 30 - 85 MB；启用内置 Chromium 后常驻约 500 - 650 MB。在 Android Termux 运行建议可用 RAM ≥ 1 GB。

---

## 技术文档

- [系统架构设计 (ARCHITECTURE.md)](./docs/ARCHITECTURE.md)：分层设计、请求生命周期、Wire Codec 编解码与并发控制模型。
- [BotGuard 验证链路机制 (BOTGUARD_VERIFICATION_CHAIN.md)](./docs/BOTGUARD_VERIFICATION_CHAIN.md)：WAA 挑战握手、Wasm 动态签名、内容哈希与服务端校验全链路。

---

## 安装与启动

### 前置要求

- Python 3.10+
- 系统安装有 Chromium / Chrome 浏览器

### Linux / macOS / Windows

项目依赖与版本锁定使用 [`uv`](https://docs.astral.sh/uv/) 管理：

```bash
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
uv sync
uv run python3 main.py server --port 8080
```

> **注意**：请使用 `uv sync` 同步依赖，避免使用 `pip install` 导致平台预编译二进制包不匹配。

### Android Termux

Termux 环境下浏览器运行在 `proot-distro` Linux 容器内，执行以下命令完成环境安装与启动：

```bash
pkg update
pkg install -y python git uv proot-distro
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
uv sync
bash scripts/install_termux_prereqs.sh --project-root "$PWD"
uv run python3 main.py server --port 8080
```

> 首次执行 `install_termux_prereqs.sh` 时会自动创建专属容器并准备浏览器运行时。

### Docker 部署

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/chrysoljq/aistudio-api:latest
```

使用 Docker Compose：

```bash
docker compose up -d
```

---

## 配置说明

服务启动后可访问 `http://localhost:8080` 进入 Web 控制台：

- **概览面板**：查看服务运行状态、各模型调用量、429 频控指标及调用代码示例。
- **账号管理**：导入 Cookie 凭据、查看子账号状态、手动切换或重置 429 锁定。
- **系统设置**：在线编辑 `config.yaml` 规则，保存后自动完成热重载。
- **访问控制**：设置 `AISTUDIO_WEB_PASSWORD` 后自动启用控制台登录鉴权。

### 环境变量

| 变量名 | 说明 | 默认值 |
|---|---|---|
| `AISTUDIO_PORT` | 服务监听端口 | `8080` |
| `AISTUDIO_WEB_PASSWORD` | 控制台登录密码（亦支持 `AISTUDIO_ADMIN_PASSWORD`） | 空（不启用鉴权） |
| `AISTUDIO_PROXY` | HTTP / SOCKS5 出口代理地址 | 空 |
| `AISTUDIO_BROWSER_EXECUTABLE` | Chromium 可执行文件路径 | 自动探测 |
| `AISTUDIO_SNAPSHOT_CACHE_TTL` | BotGuard 快照缓存有效时长（秒） | `3600` |

---

## API 调用示例

支持以下鉴权方式：
- Query 参数：`?key=YOUR_API_KEY`
- 请求头：`x-goog-api-key: YOUR_API_KEY`、`x-api-key: YOUR_API_KEY` 或 `Authorization: Bearer YOUR_API_KEY`

### cURL

**获取模型列表**：
```bash
curl http://localhost:8080/v1beta/models -H "x-goog-api-key: your-api-key"
```

**文本生成**：
```bash
curl http://localhost:8080/v1beta/models/gemini-3.8-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'
```

**流式生成 (SSE)**：
```bash
curl http://localhost:8080/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'
```

### Python SDK (`google-genai`)

```python
from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

# 流式调用
response = client.models.generate_content_stream(
    model="gemini-3.8-flash",
    contents="Hello from Gemini"
)
for chunk in response:
    print(chunk.text, end="", flush=True)

# 非流式调用
response = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="Hello from Gemini"
)
print(response.text)
```

---

## 前端构建与二次开发

Web 控制台位于 `web/` 目录（基于 Vite + Vue 3 + TypeScript）：

```bash
cd web
bun install          # 安装依赖
bun run dev          # 启动本地开发服务 (localhost:3000，反向代理 8080)
bun run type-check   # TypeScript 类型检查
bun run lint         # ESLint 代码检查
bun run build        # 生产构建并输出到 src/aistudio_api/static
```

---

## 许可证

MIT License
