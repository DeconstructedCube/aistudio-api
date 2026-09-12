# AI Studio API

高性能 Google AI Studio Playground 反代服务。全面兼容 **Google Gemini 原生 API 协议**，支持 Google 会员（Pro/Ultra）账号特权，具备多账号智能轮询、单 Cookie 无限向下探活、动态模型发现、高画质生图、Thinking 思考链、多模态图文交互以及完整的工具调用（Function Calling 与 Google Search 等）。

基于原生异步 Chromium CDP 调试协议与内核级静态资源拦截，超低内存占用，完美适配 Linux VPS、Docker 容器以及 Android Termux / PRoot 移动端环境。

[English](./README_EN.md)

---

## 目录

- [AI Studio API](#ai-studio-api)
  - [目录](#目录)
  - [✨ 核心特性](#-核心特性)
  - [🚀 快速开始](#-快速开始)
    - [1. 本地直接运行](#1-本地直接运行)
    - [2. Docker 部署](#2-docker-部署)
    - [3. Docker Compose 部署](#3-docker-compose-部署)
  - [👥 账号管理与 Web 控制面板](#-账号管理与-web-控制面板)
    - [控制面板功能](#控制面板功能)
    - [多格式 Cookie 导入与无限探活](#多格式-cookie-导入与无限探活)
  - [💡 接口使用示例](#-接口使用示例)
    - [鉴权方式](#鉴权方式)
    - [查看可用模型 (动态发现)](#查看可用模型-动态发现)
    - [标准对话生成 (generateContent)](#标准对话生成-generatecontent)
    - [实时流式输出 (streamGenerateContent SSE)](#实时流式输出-streamgeneratecontent-sse)
    - [思考模型与思考预算 (Thinking Chain)](#思考模型与思考预算-thinking-chain)
    - [联网搜索 (Google Search)](#联网搜索-google-search)
    - [其他内置工具 (Maps / 代码执行 / URL 上下文)](#其他内置工具-maps--代码执行--url-上下文)
    - [函数调用 (Function Calling)](#函数调用-function-calling)
    - [多模态图片理解 (Image Input)](#多模态图片理解-image-input)
    - [高质量生图 (Image Generation)](#高质量生图-image-generation)
    - [系统提示词 (System Instruction)](#系统提示词-system-instruction)
    - [Python SDK (Google GenAI SDK)](#python-sdk-google-genai-sdk)
    - [系统监控与轮询管理 API](#系统监控与轮询管理-api)
  - [🤖 支持的模型](#-支持的模型)
  - [⚙️ 配置说明](#️-配置说明)
    - [环境变量清单](#环境变量清单)
    - [模型默认行为配置 (config.yaml)](#模型默认行为配置-configyaml)
    - [内容安全审查设置 (safety_settings)](#内容安全审查设置-safety_settings)
  - [🏗️ 系统架构与 BotGuard 原理](#️-系统架构与-botguard-原理)
    - [架构流程图](#架构流程图)
    - [BotGuard 动态解析原理](#botguard-动态解析原理)
    - [轻量化内核拦截优化](#轻量化内核拦截优化)
  - [CI/CD](#cicd)
  - [致谢](#致谢)
  - [开源协议](#开源协议)

---

## ✨ 核心特性

- **纯粹的 Gemini 原生 API**：深度还原 `/v1beta/models`、`/v1beta/models/{model}:generateContent` 及 `/v1beta/models/{model}:streamGenerateContent` 接口标准，即插即用接入官方生态。
- **动态模型发现**：启动及运行时直接通过 CDP 页面会话或 HTTP RPC 同步 Google AI Studio 官方最新上线的所有模型（包括 Gemini 3.8 / 3.7 / 3.5、Gemma 4、Nano Banana 生图模型等），无需手动硬编码。
- **单 Cookie 无限探活多账号**：只需粘贴一份包含多个 Google 登录会话的 Cookie，系统即可自动并发向下探活 `authuser=0, 1, 2...` 并自动批量分化导入全部关联账号。
- **多格式 Cookie 解析**：原生支持浏览器 Cookie 插件导出的 JSON 数组、Netscape 7 列标准格式以及 HTTP 请求头 Key-Value 字符串（如 `Cookie: SID=...`）。
- **多账号智能轮询与容灾**：
  - 支持 **顺序轮询 (round_robin)**、**最近最少使用 (lru)**、**最少被限流优先 (least_rl)** 三种策略。
  - 遇到 429 频率超限或配额耗尽时自动加入冷却队列，并在下一次请求自动重试故障转移。
- **全功能工具调用 (Tools & Function Calling)**：
  - 官方内置工具：实时 Google 搜索（`googleSearch` / `googleSearchRetrieval`）、Google Maps（`googleMaps`）、代码执行沙箱（`codeExecution`）、网页上下文抓取（`urlContext`）。
  - 自定义函数调用：支持完整的 `functionDeclarations` 定义与 `functionResponse` 执行结果回传。
- **深度思考能力 (Thinking Process)**：原生输出思考链内容（Part 中的 `thought: true`），支持通过 `thinkingConfig` 自定义思考强度等级（`LOW` / `MEDIUM` / `HIGH` / `MINIMAL`）或设定思考 Token 预算（`thinkingBudget`）。
- **专业级多模态与生图**：
  - 多模态理解：支持 Base64 内联图片（`inlineData`）多图混合输入。
  - 高画质生图：支持 Gemini 官方图像模型（`gemini-3.1-flash-image-preview` / `gemini-3-pro-image-preview`），支持控制 `aspectRatio`（宽高比）、`imageSize`（1K/2K/4K）及 `responseModalities`（纯图或图文混合）。
- **极致轻量化 CDP 引擎**：
  - 摆脱臃肿的 Selenium / Playwright / Camoufox，采用轻量级原生异步 Chrome DevTools Protocol (CDP) 驱动无头 Chromium。
  - 内置网络层拦截规则，直接阻止媒体、字体、分析器等非必要资源加载，内存占用直降 80% 以上，在 Android Termux 和 1G 内存 VPS 上均能丝滑运行。
- **现代化 Web 控制面板**：内置响应式 WebUI，支持实时查看请求量/成功率/限流数统计、直观管理账号状态、调整轮询模式、配置访问 Token。

---

## 🚀 快速开始

### 1. 本地直接运行

确保系统已安装 **Python 3.11+** 与 **Chromium / Google Chrome**：

```bash
# 1. 克隆项目仓库
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api

# 2. 安装依赖（推荐使用 uv 或 pip）
pip install -r requirements.txt
# 或者使用 uv: uv pip install -r requirements.txt

# 3. 启动 API 服务（默认端口 8080，首次会自动探测并拉起本地 Chromium）
python3 main.py server --port 8080
```

> **提示**：如果在无桌面环境的 Linux 服务器或 Termux 上运行，服务会自动探测环境变量与系统路径中的 Chromium（如 `chromium-browser` 或 `google-chrome`），以无头模式运行。也可以通过 `AISTUDIO_BROWSER_EXECUTABLE` 手动指定浏览器可执行文件路径。

### 2. Docker 部署

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/chrysoljq/aistudio-api:latest
```

### 3. Docker Compose 部署

项目根目录下已提供预配置的 `docker-compose.yml`：

```bash
# 后台启动服务
docker compose up -d

# 查看运行日志
docker compose logs -f
```

---

## 👥 账号管理与 Web 控制面板

服务启动成功后，在浏览器中访问：

👉 **`http://localhost:8080`**

![Web 控制面板](image/cookie.png)

### 控制面板功能

1. **运行状态看板**：实时查看当前激活的主账号、轮询策略、总请求数、429 限流次数以及各模型的详细调用耗时。
2. **账号池管理**：直观展示已导入的全部账号，包括关联邮箱、子账号序号（`u/0`, `u/1`...）、请求成功率与限流状态，支持手动一键激活指定账号。
3. **轮询策略调整**：在线切换 `round_robin`、`lru`、`least_rl` 轮询算法，支持动态设置账号限流冷却时间与手动强制切换下一个账号。
4. **安全凭据隔离**：右上角支持设置 API Token，避免未授权访问控制面板。

### 多格式 Cookie 导入与无限探活

在控制面板点击 **“+ 导入 Cookies”** 按钮：

1. **获取 Cookie**：登录 Google 账号后，访问 [Google AI Studio](https://aistudio.google.com/) 或 [Google 账号管理页](https://myaccount.google.com/)，复制完整的 Cookie 内容。
2. **支持格式**：
   - **HTTP 请求头文本**：直接粘贴请求头中的 `Cookie: SID=xxx; HSID=xxx; ...`
   - **JSON 数组**：通过 Chrome 扩展（如 *Cookie-Editor*）导出的 JSON 格式
   - **Netscape 7 列文本**：通过传统 Cookie 导出工具导出的文本
3. **⚡ 自动无限向下探活多账号**：
   - 勾选 **“自动无限向下探活多登录账号”**。
   - 单份 Cookie 中若包含多个已切换登录的 Google 账号（如个人号、工作号），系统将自动从 `authuser=0` 开始向下并发探查所有有效账号的邮箱身份，并一键分化为多个独立账号录入轮询池中！

---

## 💡 接口使用示例

### 鉴权方式

如果配置了 `AISTUDIO_API_KEY`（或多 Key `AISTUDIO_API_KEYS`），向接口发送请求时支持以下任意一种鉴权格式：

- **URL 查询参数**：`?key=YOUR_API_KEY`（与 Google 官方习惯一致）
- **Header 请求头**：`x-goog-api-key: YOUR_API_KEY`
- **通用 Header**：`x-api-key: YOUR_API_KEY`
- **Bearer Token**：`Authorization: Bearer YOUR_API_KEY`

---

### 查看可用模型 (动态发现)

服务会实时获取当前账号在 Google AI Studio 中可用的完整模型列表：

```bash
curl http://localhost:8080/v1beta/models \
  -H "x-goog-api-key: your-api-key"
```

也可以查询特定模型的详细信息：

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash \
  -H "x-goog-api-key: your-api-key"
```

---

### 标准对话生成 (generateContent)

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent?key=your-api-key \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "你好！请用一句话介绍你自己。"}
        ]
      }
    ],
    "generationConfig": {
      "temperature": 0.7,
      "maxOutputTokens": 2048
    }
  }'
```

---

### 实时流式输出 (streamGenerateContent SSE)

使用 `alt=sse` 参数开启标准的 Server-Sent Events 流式返回：

```bash
curl -N http://localhost:8080/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "请写一段 Python 代码实现快速排序，并附带详细注释。"}
        ]
      }
    ]
  }'
```

---

### 思考模型与思考预算 (Thinking Chain)

针对支持深度推理的模型，支持在 `generationConfig` 中调节思考模式或预算：

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "9.11 和 9.8 哪个数字更大？请仔细思考后再回答。"}
        ]
      }
    ],
    "generationConfig": {
      "thinkingConfig": {
        "thinkingLevel": "HIGH",
        "thinkingBudget": 4096
      }
    }
  }'
```

> 返回响应中包含 `thought: true` 标记的 Part，即为模型输出的完整思维链。

---

### 联网搜索 (Google Search)

支持官方 `googleSearch` 或 `googleSearchRetrieval` 工具配置：

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "请搜索今天最新的人工智能行业重大新闻，并给出简要总结。"}
        ]
      }
    ],
    "tools": [
      {
        "googleSearch": {}
      }
    ]
  }'
```

---

### 其他内置工具 (Maps / 代码执行 / URL 上下文)

```bash
curl http://localhost:8080/v1beta/models/gemini-3.5-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "帮我规划从上海虹桥火车站到外滩的路线并写代码计算距离。"}
        ]
      }
    ],
    "tools": [
      {"googleMaps": {}},
      {"codeExecution": {}}
    ]
  }'
```

---

### 函数调用 (Function Calling)

支持定义函数列表，模型将在命中时返回结构化的 `functionCall`：

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "请帮我查询东京现在的气温是多少？"}
        ]
      }
    ],
    "tools": [
      {
        "functionDeclarations": [
          {
            "name": "get_current_weather",
            "description": "获取指定城市的实时天气与气温",
            "parameters": {
              "type": "object",
              "properties": {
                "location": {
                  "type": "string",
                  "description": "城市名称，例如 Tokyo, Shanghai"
                },
                "unit": {
                  "type": "string",
                  "enum": ["celsius", "fahrenheit"]
                }
              },
              "required": ["location"]
            }
          }
        ]
      }
    ]
  }'
```

---

### 多模态图片理解 (Image Input)

通过 `inlineData` 字段传递图片的 Base64 编码与 MIME 类型：

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {
            "inlineData": {
              "mimeType": "image/jpeg",
              "data": "'$(base64 -w 0 /path/to/your/image.jpg)'"
            }
          },
          {
            "text": "请详细描述这张图片的内容，并提取图片中的文字。"
          }
        ]
      }
    ]
  }'
```

---

### 高质量生图 (Image Generation)

调用 Google 官方生图模型（如 `gemini-3.1-flash-image-preview` 或 `gemini-3-pro-image-preview`，需要 Pro/Ultra 订阅账号），支持配置宽高比与分辨率：

```bash
curl http://localhost:8080/v1beta/models/gemini-3.1-flash-image-preview:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "A futuristic cyberpunk city with neon lights and flying cars, raining night, cinematic lighting, 8k"}
        ]
      }
    ],
    "generationConfig": {
      "responseModalities": ["IMAGE"],
      "imageConfig": {
        "aspectRatio": "16:9",
        "imageSize": "2K"
      }
    }
  }'
```

> **可选宽高比 (`aspectRatio`)**：`1:1`、`3:4`、`4:3`、`9:16`、`16:9`。  
> **可选画质 (`imageSize`)**：`1K`、`2K`、`4K`。  
> **返回模式 (`responseModalities`)**：`["IMAGE"]`（仅返回图片）或 `["IMAGE", "TEXT"]`（返回图文）。

---

### 系统提示词 (System Instruction)

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "systemInstruction": {
      "parts": [
        {"text": "你是一名精通古汉语与诗词的学者，回答请使用温润优雅的文言风格。"}
      ]
    },
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "如何看待人生的起伏与得失？"}
        ]
      }
    ]
  }'
```

---

### Python SDK (Google GenAI SDK)

安装官方推荐的新一代 SDK：

```bash
pip install google-genai
```

调用示例：

```python
from google import genai
from google.genai import types

# 将 base_url 指向本反代服务的 /v1beta 根路径
client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

# 1. 基础对话
response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="用 Python 写一个异步 HTTP 请求示例",
)
print("回答:\n", response.text)

# 2. 启用实时搜索
search_response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="今天科技界有哪些重大进展？",
    config=types.GenerateContentConfig(
        tools=[{"google_search": {}}],
    ),
)
print("搜索结果:\n", search_response.text)

# 3. 流式生成
for chunk in client.models.generate_content_stream(
    model="gemini-3.7-flash",
    contents="请讲一个关于星际探险的短篇故事",
):
    print(chunk.text, end="", flush=True)
print()
```

---

### 系统监控与轮询管理 API

除了 Web 控制面板外，系统还提供完整的 REST API 方便进行脚本自动化管理：

| 请求方法 | 路径 | 说明 | 鉴权要求 |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | 健康检查与在线状态 | 开放无需鉴权 |
| `GET` | `/stats` | 系统请求量、429 与延迟统计 | 需要鉴权 |
| `GET` | `/accounts` | 查询已导入的全部账号列表 | 需要鉴权 |
| `GET` | `/accounts/active` | 查询当前激活的账号信息 | 需要鉴权 |
| `POST` | `/accounts/{id}/activate` | 手动切换激活指定账号 | 需要鉴权 |
| `DELETE` | `/accounts/{id}` | 删除指定账号 | 需要鉴权 |
| `POST` | `/accounts/import-cookies` | 导入单个账号的 Cookie | 需要鉴权 |
| `POST` | `/accounts/probe-and-import` | ⚡ 探活并批量导入全部多会话账号 | 需要鉴权 |
| `GET` | `/rotation` | 获取当前轮询状态与各账号统计 | 需要鉴权 |
| `POST` | `/rotation/mode` | 调整轮询模式与冷却时间 | 需要鉴权 |
| `POST` | `/rotation/next` | 强制切换至下一个可用账号 | 需要鉴权 |

---

## 🤖 支持的模型

系统支持 **动态模型发现**。无论 Google AI Studio 何时上新模型，只要已登录的账号拥有权限，即可直接在调用时传入模型名，系统也会自动同步到 `/v1beta/models` 列表中。

以下为常用与保底支持的主流模型：

| 模型显示名 | 模型标识 (ID) | 联网搜索 | 适用场景与说明 |
| :--- | :--- | :---: | :--- |
| **Gemini 3.8 Flash** | `gemini-3.8-flash` | 选配 | Google 最新极速主力模型，高吞吐低延迟 |
| **Gemini 3.7 Flash** | `gemini-3.7-flash` | 选配 | 默认文本与多模态模型，支持深度 Thinking 思考 |
| **Gemini 3.6 Flash** | `gemini-3.6-flash` | 选配 | 高并发高吞吐轻量模型 |
| **Gemini 3.5 Flash** | `gemini-3.5-flash` | 选配 | 平衡型高性能模型 |
| **Gemini 3.5 Flash Lite** | `gemini-3.5-flash-lite` | 选配 | 极低延迟响应模型 |
| **Gemini 3.1 Pro Preview** | `gemini-3.1-pro-preview` | 选配 | 强逻辑推理模型，适合复杂编程与数理推导 |
| **Gemma 4 31B IT** | `gemma-4-31b-it` | ✅ 默认开启 | Google 开源旗舰指令微调大模型 |
| **Gemma 4 26B A4B IT** | `gemma-4-26b-a4b-it` | ✅ 默认开启 | 高效 MoE 架构开源模型 |
| **Gemini 3.1 Flash Image** | `gemini-3.1-flash-image-preview` | 选配 | 官方高画质生图模型（需 Pro/Ultra 账号） |
| **Gemini 3 Pro Image** | `gemini-3-pro-image-preview` | 选配 | 旗舰级生图与图像微调模型 |
| **Veo 3.1** | `veo-3.1-generate-preview` | ❌ | 视频生成预览模型 |

---

## ⚙️ 配置说明

可以通过环境变量或项目根目录下的 `.env` 文件进行灵活配置。

### 环境变量清单

| 环境变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `AISTUDIO_PORT` | `8080` | API 服务监听端口 |
| `AISTUDIO_BROWSER_PORT` | `9222` | Chromium 远程调试端口 (CDP) |
| `AISTUDIO_BROWSER_HEADLESS` | `1` | 是否以无头模式运行 Chromium（`1` 为无头，`0` 为有头） |
| `AISTUDIO_BROWSER_EXECUTABLE` | 空 | 自定义 Chromium 可执行文件路径（留空自动探测系统安装） |
| `AISTUDIO_PROXY` | 空 | 浏览器与网络请求代理（支持 `http://`、`socks5://`） |
| `AISTUDIO_API_KEY` | 空 | 单个 API 鉴权密钥（配置后启用接口鉴权） |
| `AISTUDIO_API_KEYS` | 空 | 多个 API 密钥，用逗号分隔（如 `key1,key2,key3`） |
| `AISTUDIO_AUTH_FILE` | 空 | 手动指定认证凭证文件路径（默认扫描 `data/accounts`） |
| `AISTUDIO_TMP_DIR` | `/tmp` | 临时文件存储目录（如多模态图片转换缓存） |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | `gemini-3.7-flash` | 默认对话模型 |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | `gemini-3.1-flash-image-preview` | 默认生图模型 |
| `AISTUDIO_TIMEOUT_REPLAY` | `120` | 普通对话请求超时时间（秒） |
| `AISTUDIO_TIMEOUT_STREAM` | `120` | 流式响应超时时间（秒） |
| `AISTUDIO_TIMEOUT_CAPTURE` | `30` | 捕获请求超时时间（秒） |
| `AISTUDIO_ACCOUNTS_DIR` | `data/accounts` | 账号数据持久化存储目录 |
| `AISTUDIO_ACCOUNT_ROTATION_MODE` | `round_robin` | 账号轮询模式：`round_robin`、`lru`、`least_rl` |
| `AISTUDIO_ACCOUNT_COOLDOWN_SECONDS` | `60` | 账号遇到 429 频率限制后的冷却恢复时间（秒） |
| `AISTUDIO_ACCOUNT_MAX_RETRIES` | `3` | 单次请求失败或限流时自动切换账号重试的最大次数 |
| `AISTUDIO_MAX_CONCURRENCY` | `3` | 并发请求信号量控制上限 |
| `AISTUDIO_CONFIG_FILE` | 空 | 自定义 YAML 配置文件路径（默认读取根目录 `config.yaml`） |
| `AISTUDIO_DUMP_RAW_RESPONSE` | `0` | 是否在本地调试转储 Google 原始 Protobuf 响应（`1` 为开启） |
| `AISTUDIO_DUMP_RAW_RESPONSE_DIR` | `/tmp` | 调试响应保存目录 |

---

### 模型默认行为配置 (config.yaml)

项目根目录下支持 `config.yaml` 配置文件，用于为不同的模型家族设定默认的生成参数、默认挂载的工具以及清理特定的协议下标。

系统内置了优化的默认配置：

```yaml
model_defaults:
  profiles:
    # 生图模型特化配置
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

    # Gemma 模型特化配置（默认附加 Google 搜索）
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

    # Gemini 文本模型默认放宽安全限制
    - name: gemini_models
      match:
        prefixes:
          - gemini-
      safety_settings:
        Harassment: 5
        Hate: 5
        Sexually Explicit: 5
        Dangerous Content: 5

  models:
    # 针对特定单模型的覆盖规则
    gemini-3.1-flash-image-preview:
      generation_config_defaults:
        image_output_mode: text_and_image
        media_resolution: HIGH
```

- **`match`**：支持 `exact`（精准名称）、`prefixes`（前缀匹配，如 `gemini-`）、`contains`（包含子串，如 `image`）。
- **`thinking_config.level`**：支持 `LOW` / `MEDIUM` / `HIGH` / `MINIMAL`。
- **`media_resolution`**：支持 `LOW` / `MEDIUM` / `HIGH`。

---

### 内容安全审查设置 (safety_settings)

支持以下四大安全拦截分类：
- `Harassment` (骚扰)
- `Hate` (仇恨言论)
- `Sexually Explicit` (性露骨内容)
- `Dangerous Content` (危险内容)

数值范围为 **1 到 5**：
- **`1`**：最严格审查，强力拦截。
- **`5`**：完全关闭该类别的安全拦截。

如果传入 `safety_off=true` 或在配置文件中设为 5，系统会自动将所有安全类别放开至等级 5，确保创作与输出不受不必要的限制。

---

## 🏗️ 系统架构与 BotGuard 原理

### 架构流程图

```
客户端请求 (Google GenAI SDK / curl / 其它客户端)
                    │
                    ▼
  ┌──────────────────────────────────────────────┐
  │              FastAPI 服务端                   │
  │     /v1beta/models/... 原生路由与鉴权         │
  └──────────────────────┬───────────────────────┘
                         │
                         ▼
  ┌──────────────────────────────────────────────┐
  │       Wire Codec 协议编解码与模型发现         │
  │  Gemini 格式 ⇄ AI Studio Protobuf Wire 格式   │
  │  动态模型同步 ⇄ 多账号轮询器 (Round-Robin/LRU) │
  └──────────────────────┬───────────────────────┘
                         │
                         ▼
  ┌──────────────────────────────────────────────┐
  │           Chromium CDP 原生调试引擎           │
  │  • 内核级资源拦截器（阻断媒体/图片/字体/统计）   │
  │  • 极低内存占用，常驻已登录的 Page 会话       │
  │  • 快速路径 BotGuard Snapshot 特征匹配生成   │
  └──────────────────────┬───────────────────────┘
                         │
                         ▼
             Google AI Studio 官方服务
```

### BotGuard 动态解析原理

Google AI Studio 在每次调用核心推理接口时，均强制校验由前端虚拟机生成的 **BotGuard Snapshot** 加密签名凭证。传统的逆向方式往往依赖硬编码函数名，但在 Google 前端频繁发布 Bundle（函数名持续在 `Mv`、`Ov`、`Sv` 等之间随机轮转）时极易失效。

本项目采用**动态特征指纹定位机制**：
1. 注入轻量 JavaScript 探针分析前端加载的作用域。
2. 依据 AST 与特征指纹（`.snapshot({` + `content` + `yield`）精准命中并 Hook 真实的 Snapshot 生成器。
3. 每次请求均能在几十毫秒内完成合法凭证的高速运算，无惧 Google 前端更新。

### 轻量化内核拦截优化

为了保障在 1核 1G 的轻量 VPS 或 Android Termux 等严苛算力环境下的长久稳定运行，本项目直接在 Chromium CDP 会话中配置了**内核级资源拦截策略**：
- 严格拦截非必要的 `Image`、`Media`、`Font`、`Stylesheet` 加载。
- 阻断 Google Analytics、Sentry 等第三方遥测追踪请求。
- 仅保留执行核心鉴权与 API 交换的 JavaScript 沙箱，将常规 Chromium 的内存开销从 500MB+ 大幅缩减至 **80MB 左右**。

---

## CI/CD

本项目配置了自动化 GitHub Actions 工作流（`.github/workflows/docker.yml`）：

- 代码推送到 `master` 或 `main` 分支时自动触发构建并推送多架构镜像至 GitHub Container Registry：  
  `ghcr.io/chrysoljq/aistudio-api:latest`
- Pull Request 提交时自动执行构建检查与静态验证。

---

## 致谢

- [LuanRT/BgUtils](https://github.com/LuanRT/BgUtils) — 启发了 BotGuard 凭证的解析思路
- [iBUHub/AIStudioToAPI](https://github.com/iBUHub/AIStudioToAPI) — 参考了早期会话结构
- [LINUX DO](https://linux.do) — 优质技术社区的支持与交流

---

## 开源协议

本项目基于 [MIT License](./LICENSE) 协议开源。
