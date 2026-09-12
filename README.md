# AI Studio API

Google AI Studio Playground 反代服务，支持 Google 会员（Pro/Ultra）账号，兼容 Google Gemini 原生 API 协议格式，支持生图、工具调用与联网搜索。

[English](./README_EN.md)

## 目录

- [AI Studio API](#ai-studio-api)
  - [目录](#目录)
  - [功能](#功能)
  - [快速开始](#快速开始)
    - [直接启动](#直接启动)
    - [Docker 部署](#docker-部署)
    - [账号管理与登录](#账号管理与登录)
  - [使用示例](#使用示例)
    - [查看模型列表](#查看模型列表)
    - [Gemini 原生接口 (curl)](#gemini-原生接口-curl)
    - [Python (Google GenAI SDK)](#python-google-genai-sdk)
  - [支持的模型](#支持的模型)
  - [配置](#配置)
    - [模型配置](#模型配置)
    - [安全设置](#安全设置)
  - [Docker 镜像 CI](#docker-镜像-ci)
  - [架构](#架构)
  - [BotGuard 原理](#botguard-原理)
  - [TODO](#todo)
  - [致谢](#致谢)
  - [License](#license)

## 功能

- **Gemini 原生 API 兼容** — 支持 `/v1beta/models`、`/v1beta/models/{model}:generateContent` 与流式接口
- **灵活的鉴权方式** — 支持 `?key=` Query 参数、`x-goog-api-key` 请求头、`x-api-key` 及 `Authorization: Bearer`
- **流式输出** — SSE 实时流式响应
- **多轮对话** — 正确的 `user`/`model` 交替结构与上下文处理
- **多模态图片输入** — 支持 base64 内联与图片上传，支持单图/多图
- **Google 搜索** — 通过 `googleSearchRetrieval` 支持联网搜索
- **Thinking 思考过程** — 返回模型思维链过程（`thinking` Part）
- **图片生成** — 通过 Gemini 图片生成模型生成图片
- **Chromium CDP 直连** — 基于原生 Chromium 调试协议，性能强劲、资源占用更低
- **BotGuard 动态解析** — 自动特征匹配定位 snapshot 函数
- **多账号智能轮询** — 支持 round-robin / LRU / least_rl（最少限流）

## 快速开始

### 直接启动

```bash
# 克隆项目
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api

# 安装依赖
pip install -r requirements.txt

# 启动服务（首次会自动检测并拉起 Chromium 浏览器）
python3 main.py server --port 8080
```

### Docker 部署

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/chrysoljq/aistudio-api:latest
```

### 账号管理与登录

服务启动后，访问 `http://localhost:8080` 进入管理控制面板：
1. 点击 **“导入 Cookies”** 按钮。
2. 访问 [Google 账号管理页](https://myaccount.google.com/) 或 AI Studio 页面，复制完整 Cookie 字符串。
3. 粘贴到控制面板中，支持**单份 Cookie 无限向下探活多账号并一键批量导入**。

![alt text](image/cookie.png)

## 使用示例

### 查看模型列表

```bash
curl http://localhost:8080/v1beta/models \
  -H "x-goog-api-key: your-secret-token"
```

### Gemini 原生接口 (curl)

```bash
# 普通对话
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent?key=your-secret-token \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "你好！请做个自我介绍。"}]}]
  }'

# 联网搜索
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "今天上海天气怎么样？"}]}],
    "tools": [{"googleSearchRetrieval": {}}]
  }'

# 流式对话 (SSE)
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "用 Python 写一个快速排序。"}]}]
  }'
```

### Python (Google GenAI SDK)

```python
from google import genai

client = genai.Client(
    api_key="your-secret-token",
    http_options={"api_version": "v1beta", "base_url": "http://localhost:8080"},
)

response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="你好！",
)
print(response.text)
```

## 支持的模型

| 模型 | ID | 默认 Google Search | 说明 |
|------|-----|-------------------|------|
| Gemini 3.7 Flash | `gemini-3.7-flash` | ❌ | 默认文本/多模态模型 |
| Gemma 4 31B | `gemma-4-31b-it` | ✅ | 开源大模型 |
| Gemma 4 26B A4B | `gemma-4-26b-a4b-it` | ✅ | MoE 架构 |
| Gemini 3.5 Flash | `gemini-3.5-flash` | ❌ | 快速高效 |
| Gemini 3.1 Pro | `gemini-3.1-pro-preview` | ❌ | 强推理能力 |
| Gemini 3.1 Flash Lite | `gemini-3.1-flash-lite` | ❌ | 轻量快速 |
| Gemini 3.1 Flash Image | `gemini-3.1-flash-image-preview` | ❌ | 生图模型，仅限 Pro/Ultra |
| Gemini 3 Pro Image | `gemini-3-pro-image-preview` | ❌ | 高画质生图模型 |

## 配置

通过环境变量或 `.env` 文件配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AISTUDIO_PORT` | `8080` | API 服务端口 |
| `AISTUDIO_BROWSER_PORT` | `9222` | Chromium 浏览器远程调试端口 |
| `AISTUDIO_BROWSER_HEADLESS` | `1` | 是否以无头模式运行浏览器（1=无头，0=有头） |
| `AISTUDIO_BROWSER_EXECUTABLE` | 空 | Chromium 可执行文件路径（留空则自动探测系统安装） |
| `AISTUDIO_PROXY` | 空 | 浏览器网络代理地址 |
| `AISTUDIO_API_KEY` | 空 | API 鉴权密钥，配置后启用 API 鉴权 |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | `gemini-3.7-flash` | 默认对话模型 |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | `gemini-3.1-flash-image-preview` | 默认生图模型 |
| `AISTUDIO_TIMEOUT_REPLAY` | `120` | 请求超时时间（秒） |
| `AISTUDIO_TIMEOUT_STREAM` | `120` | 流式超时时间（秒） |
| `AISTUDIO_SNAPSHOT_CACHE_TTL` | `3600` | BotGuard snapshot 缓存有效时间（秒） |
| `AISTUDIO_ACCOUNTS_DIR` | `data/accounts` | 账号数据持久化存储目录 |
| `AISTUDIO_ACCOUNT_ROTATION_MODE` | `round_robin` | 账号轮询模式：`round_robin`、`lru`、`least_rl` |
| `AISTUDIO_ACCOUNT_COOLDOWN_SECONDS` | `60` | 账号遇到限流后的冷却时间（秒） |
| `AISTUDIO_DUMP_RAW_RESPONSE` | `0` | 是否保存原始响应到磁盘（用于调试） |

### 模型配置

项目根目录支持一个额外的 `config.yaml`，用于给不同模型族补默认参数。默认读取项目根目录的 `config.yaml`，也可以用 `AISTUDIO_CONFIG_FILE` 指向别的配置文件。

当前主要用于这几类场景：

- 给 `gemma` / `gemini` / 生图模型分别设置默认行为
- 给特定模型补 `generation_config` 默认值
- 控制某些生图模型需要清空哪些 wire 下标
- 配置默认工具，例如 `google_search`
- 配置安全设置 `safety_settings`

当前仓库内置的示例：

```yaml
model_defaults:
  profiles:
    - name: image_models
      match:
        contains:
          - image
      is_image_model: true
      generation_config_defaults:
        response_mime_type: null
        image_output_mode: image_only
        thinking_config:
          level: MINIMAL
          mode: 1
      clear_generation_config_indexes:
        - 7
        - 13
        - 17
      disable_safety_settings: true

    - name: gemma_models
      match:
        prefixes:
          - gemma-
      default_tools:
        - google_search
      safety_settings:
        Harassment: 5
        Hate: 5
        Sexually Explicit: 5
        Dangerous Content: 5

    - name: gemini_models
      match:
        prefixes:
          - gemini-
      safety_settings:
        Harassment: 5
        Hate: 5
        Sexually Explicit: 5
        Dangerous Content: 5

  models: {}
```

`match` 支持三种方式：

- `exact`: 精确命中模型名
- `prefixes`: 前缀命中，适合 `gemma-`、`gemini-`
- `contains`: 模型名包含指定片段时命中

`generation_config_defaults` 目前支持这些常用字段：

- `response_mime_type`
- `thinking_config`
- `image_output_mode`
- `media_resolution`

其中几个字段已经做了可读化转换：

- `thinking_config.level`: `LOW` / `MEDIUM` / `HIGH` / `MINIMAL`
- `image_output_mode`: `image_only` 或 `text_and_image`
- `media_resolution`: `LOW` / `MEDIUM` / `HIGH`

也可以对单个模型单独覆盖：

```yaml
model_defaults:
  models:
    gemini-3.1-flash-image-preview:
      generation_config_defaults:
        image_output_mode: text_and_image
        media_resolution: HIGH
```

### 安全设置

`safety_settings` 目前支持这四类：

- `Harassment`
- `Hate`
- `Sexually Explicit`
- `Dangerous Content`

值范围是 `1` 到 `5`：

- `1` 表示最严格，尽量完全拦截
- `5` 表示关闭

示例：

```yaml
safety_settings:
  Harassment: 1
  Hate: 2
  Sexually Explicit: 3
  Dangerous Content: 5
```

说明：

- 文本模型会把这组配置下发到 AI Studio wire 请求
- `safety_off=true` 会直接把这四项都设为 `5`
- 当前默认的图片模型配置里 `disable_safety_settings: true`，所以生图模型会直接清空安全设置字段

## Docker 镜像 CI

本项目包含 GitHub Actions 工作流（`.github/workflows/docker.yml`）：

- `src/**` 路径下的代码变动将在 `push` 和 `pull_request` 时自动触发 Docker 构建
- `pull_request` 仅执行构建验证，不推送镜像
- 推送到 `main` / `master` 分支时会自动打包并发布镜像至 `ghcr.io/chrysoljq/aistudio-api`
- 支持通过 `workflow_dispatch` 手动触发

工作流使用 GitHub 内置的 `GITHUB_TOKEN` 推送至 GHCR，无需额外配置 Docker Hub 账号。

## 架构

```
客户端（Google GenAI SDK / curl / Web）
    │
    ▼
┌─────────────────────┐
│   FastAPI 服务器      │  ← /v1beta/models/... 原生 API 路由
│   /v1beta/...        │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   Wire Codec         │  ← Gemini API 格式 ⇄ AI Studio gRPC body
│   + BotGuard         │     自动特征匹配 snapshot 函数
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   Chromium 浏览器    │  ← 原生 Chromium CDP 调试桥，注入 cookies
│   （无头模式）        │     执行通信与 session 保持
└─────────┬───────────┘
          │
          ▼
    Google AI Studio
```

**工作原理：**
1. API 请求进入，转换为 AI Studio 的 wire 格式
2. 生成 BotGuard snapshot（自动检测函数，带缓存）
3. 构造完整的 gRPC body，通过 CDP session 经由已登录的页面上下文发送请求到 Google
4. 解析响应，按标准 Gemini 原生响应格式返回

轮询模式：
- `round_robin` — 轮流使用
- `lru` — 最久未使用
- `least_rl` — 最少被限流

## BotGuard 原理

Google 每次请求都要求一个 BotGuard "snapshot" —— 证明请求来自真实浏览器的加密凭证。本项目：

1. 在运行时 hook 前端的 snapshot 生成函数
2. 通过特征匹配自动定位（`.snapshot({` + `content` + `yield`），无惧 Google 更新
3. 为每个请求生成合法的 snapshot

snapshot 函数名随 Google bundle 更新持续变化（Mv → Ov → Sv → ...），但特征模式保持不变。

## TODO
- [ ] 完整 webui 控制台增强
- [ ] 更多多模态音频/视频输入支持
## 致谢
- https://github.com/LuanRT/BgUtils
- https://github.com/iBUHub/AIStudioToAPI
- https://linux.do

## License

MIT
