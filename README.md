# aistudio-api

<p align="center">
  <a href="https://github.com/DeconstructedCube/aistudio-api"><img src="https://img.shields.io/badge/API-Gemini%20v1beta-4285F4?style=flat-square&logo=google" alt="Gemini API"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/Framework-FastAPI-009688?style=flat-square&logo=fastapi" alt="FastAPI"></a>
  <a href="https://vuejs.org/"><img src="https://img.shields.io/badge/Frontend-Vue%203%20%2B%20Vite-4FC08D?style=flat-square&logo=vuedotjs" alt="Vue 3"></a>
  <a href="https://docs.astral.sh/uv/"><img src="https://img.shields.io/badge/Python-3.11%2B%20%7C%20uv-261230?style=flat-square&logo=python" alt="Python uv"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License"></a>
</p>

<p align="center">
  Google AI Studio 反向代理服务，将 Web 端 AI Studio 转换为标准 Google Gemini API 协议接口。
</p>

<p align="center">
  <a href="./README_EN.md">English Documentation</a>
</p>

---

## 目录

- [核心特性](#核心特性)
- [技术架构与文档](#技术架构与文档)
- [快速开始](#快速开始)
  - [前置要求](#前置要求)
  - [Linux / macOS / Windows](#linux--macos--windows)
  - [Android (Termux)](#android-termux)
  - [Docker 部署](#docker-部署)
- [配置与环境变量](#配置与环境变量)
- [API 调用示例](#api-调用示例)
  - [cURL 调用](#curl-调用)
  - [Python SDK (google-genai)](#python-sdk-google-genai)
  - [工具调用 (Function Calling)](#工具调用-function-calling)
- [Web 管理控制台](#web-管理控制台)
- [开发与测试](#开发与测试)
- [开源协议](#开源协议)

---

## 核心特性

| 功能模块 | 说明 |
|---|---|
| **原生 Gemini 协议** | 完整兼容 `/v1beta/...` 接口规范，支持 Thinking 思维链、Multimodal 多模态、Function Calling 工具调用及图片生成 |
| **动态模型发现** | 自动向上游同步可用模型列表，支持 `gemini-3.7-flash`、`gemini-3.8-flash` 等最新模型 |
| **多账号黏性调度** | 维护账号状态，支持按模型独立记录 429 配额并在限流时自动切换，每日美西午夜自动重置冷却 |
| **批量子账号探活** | 导入单个包含多个 Google 身份的 Cookie 后，自动递归探活子账号（`u/0`, `u/1`...）并分别建档 |
| **内置搜索与工具** | 支持 Google Search 联网搜索、Google Maps、代码执行沙箱等官方扩展能力 |
| **轻量 CDP 驱动** | 基于纯 Python 异步 WebSocket 直连 Chrome DevTools Protocol，无需 Node.js、Playwright 或 Selenium |
| **Web 管理面板** | 提供现代化的管理控制台，支持账号管理、调用量监控、在线编辑与热重载 `config.yaml` |

> [!NOTE]
> **内存占用参考**：纯 Python 后端服务常驻内存约 30 - 85 MB；拉起内置单实例受控 Chromium 后总内存约 150 - 250 MB。Android Termux 环境建议空闲 RAM ≥ 1 GB。
---

## 技术架构与文档

```
┌────────────────────────────────────────────────────────┐
│               Client (cURL / Python SDK)               │
└───────────────────────────┬────────────────────────────┘
                            │ /v1beta/models/...
┌───────────────────────────▼────────────────────────────┐
│              FastAPI HTTP Gateway (Port 8080)          │
├───────────────────────────┬────────────────────────────┤
│    Account Rotator        │    Chat & Wire Codec       │
│  (Sticky & 429 Isolation) │ (Protobuf-JSON & BotGuard) │
└───────────────────────────┼────────────────────────────┘
                            │ CDP WebSocket
┌───────────────────────────▼────────────────────────────┐
│      Controlled Chromium Instance (CloakBrowser)       │
│       In-Page XHR Replay (withCredentials=true)        │
└───────────────────────────┬────────────────────────────┘
                            │ HTTPS / RPC
┌───────────────────────────▼────────────────────────────┐
│         Google AI Studio (alkalimakersuite-pa)         │
└────────────────────────────────────────────────────────┘
```

详细的设计与协议分析文档：

- [系统架构设计 (ARCHITECTURE.md)](./docs/ARCHITECTURE.md)：分层架构、请求生命周期、Wire Codec 编解码与并发控制模型。
- [BotGuard 验证链路机制 (BOTGUARD_VERIFICATION_CHAIN.md)](./docs/BOTGUARD_VERIFICATION_CHAIN.md)：WAA 挑战握手、Wasm 动态签名、内容哈希与服务端校验流程。
- [智能体与协作规范 (AGENT.md)](./AGENT.md)：类型系统要求、代码质量检查与开发指南。

---

## 快速开始

### 前置要求

- Python 3.11 及以上版本
- 推荐安装 [`uv`](https://docs.astral.sh/uv/) 作为包管理工具
- 宿主系统安装有 Chromium / Google Chrome（Termux 环境通过脚本在 proot 容器内准备）

### Linux / macOS / Windows

```bash
# 1. 克隆代码仓库
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api

# 2. 同步依赖
uv sync

# 3. 启动服务
uv run python3 main.py server --port 8080
```

> [!TIP]
> 推荐使用 `uv sync` 同步依赖，避免使用裸 `pip` 导致跨平台预编译二进制包解析不一致。

### Android (Termux)

Termux 环境下由于 Bionic 与 Glibc ABI 差异，浏览器运行在轻量 `proot-distro` 容器内：

```bash
# 1. 准备依赖环境与安装
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
bash scripts/setup-env.sh
uv sync

# 2. 准备受控浏览器运行时（仅首次执行）
bash scripts/setup-browser.sh

# 3. 启动服务
uv run python3 main.py server --port 8080
```
### Docker 部署

使用官方 Docker 镜像直接启动：

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/chrysoljq/aistudio-api:latest
```

使用 Docker Compose 启动：

```bash
docker compose up -d
```

---

## 配置与环境变量

服务启动后可访问 `http://localhost:8080` 进入 Web 控制台完成可视化管理。

### 环境变量支持

| 变量名 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `AISTUDIO_PORT` | int | `8080` | API 服务监听端口 |
| `AISTUDIO_WEB_PASSWORD` | string | `""` | Web 控制台访问密码（亦可设置 `AISTUDIO_ADMIN_PASSWORD`） |
| `AISTUDIO_PROXY` | string | `""` | 出口代理地址，支持 `http://`、`https://` 或 `socks5://` |
| `AISTUDIO_BROWSER_EXECUTABLE` | string | 自动探测 | 自定义 Chromium 可执行文件路径 |
| `AISTUDIO_BROWSER_PORT` | int | `9222` | Chromium 远程调试端口 (CDP) |
| `AISTUDIO_BROWSER_HEADLESS` | bool | `true` | 是否以无头模式运行 Chromium |
| `AISTUDIO_SNAPSHOT_CACHE_TTL` | int | `3600` | BotGuard 快照有效缓存时间（秒） |
| `AISTUDIO_PROOT_NAME` | string | `aistudio-api` | Termux 环境下专用的 proot-distro 容器名称 |
| `AISTUDIO_ACCOUNTS_DIR` | string | `data/accounts` | 账号持久化凭据目录 |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | string | `gemini-3.7-flash` | 默认文本模型 |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | string | `gemini-3.1-flash-image-preview` | 默认图像生成模型 |

---

## API 调用示例

支持以下鉴权方式：
- Query 参数：`?key=YOUR_API_KEY`
- 请求头：`x-goog-api-key: YOUR_API_KEY`、`x-api-key: YOUR_API_KEY` 或 `Authorization: Bearer YOUR_API_KEY`

### cURL 调用

<details>
<summary><b>1. 查询模型列表</b></summary>

```bash
curl http://localhost:8080/v1beta/models \
  -H "x-goog-api-key: your-api-key"
```
</details>

<details>
<summary><b>2. 文本生成 (Non-streaming)</b></summary>

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "用简短的一句话介绍量子计算。"}]
      }
    ]
  }'
```
</details>

<details>
<summary><b>3. 流式生成 (Server-Sent Events)</b></summary>

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "介绍一下 Python 协程的工作原理。"}]
      }
    ]
  }'
```
</details>

---

### Python SDK (google-genai)

使用 Google 官方 `google-genai` SDK 直连本代理服务：

```python
from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

# 1. 流式响应
response = client.models.generate_content_stream(
    model="gemini-3.7-flash",
    contents="解释一下什么是反应式编程？",
)
for chunk in response:
    print(chunk.text, end="", flush=True)

# 2. 非流式响应
result = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="用 Python 写一个快速排序算法。",
)
print(result.text)
```

---

### 工具调用 (Function Calling)

```python
from google import genai
from google.genai import types

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

# 定义工具声明
weather_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_current_weather",
            description="获取指定地点的实时天气状况",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "location": types.Schema(
                        type="STRING",
                        description="城市或地区名称，例如 'Beijing' 或 'Tokyo'",
                    ),
                    "unit": types.Schema(
                        type="STRING",
                        enum=["celsius", "fahrenheit"],
                        description="温度单位",
                    ),
                },
                required=["location"],
            ),
        )
    ]
)

response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="今天杭州天气怎么样？",
    config=types.GenerateContentConfig(tools=[weather_tool]),
)

if response.function_calls:
    for call in response.function_calls:
        print(f"触发工具调用: {call.name}")
        print(f"调用参数: {call.args}")
```

---

## Web 管理控制台

系统内置静态打包的 Web 前端管理控制台（访问 `http://localhost:8080`）：

- **仪表盘 (Dashboard)**：实时展示服务健康状况、各模型请求量、成功率及 429 冷却指标。
- **账号管理 (Accounts)**：支持单行或 JSON 格式 Cookie 导入、自动识别并探活多账号、手动切换当前活跃账号或重置冷却。
- **系统设置 (Settings)**：在线编辑 `config.yaml`（支持配置 profiles 与 models 默认规则），保存后服务端即时热重载。
- **访问鉴权**：设置环境变量 `AISTUDIO_WEB_PASSWORD` 后自动开启登录鉴权守卫。

---

## 开发与测试

前端源码位于 `web/`，采用 Vue 3 + TypeScript + Vite + Tailwind CSS：

```bash
# 启动前端开发调试服务
cd web
bun install
bun run dev          # 启动开发服务（localhost:3000，代理至 8080）
bun run type-check   # 执行 TypeScript 类型校验
bun run lint         # 执行 ESLint 代码规范检查
bun run build        # 生产构建，产物输出至 src/aistudio_api/static
```

Python 端质量检查与测试：

```bash
# 执行代码风格与 Lint 检查
uv run ruff check .
# 执行静态类型检查
bun x pyright

# 执行单元测试套件
uv run pytest
```

---

## 开源协议

本项目基于 [MIT License](./LICENSE) 许可协议开源。
