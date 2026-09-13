# AI Studio API

High-performance Google AI Studio Playground reverse proxy service. Fully compatible with the **Google Gemini Native API protocol**, supporting Google Membership (Pro/Ultra) account privileges, multi-account intelligent rotation, single-cookie infinite account probing, dynamic model discovery, high-fidelity image generation, Thinking reasoning chains, multimodal image/text interaction, and comprehensive tool calling (Function Calling, Google Search, Google Maps, Code Execution, and URL Context).

Powered by native asynchronous Chromium CDP debugging protocol and kernel-level static resource blocking, featuring ultra-low memory consumption—perfectly adapted for Linux VPS, Docker containers, and Android Termux / PRoot mobile environments.

[中文文档](./README.md)

---

## Table of Contents

- [AI Studio API](#ai-studio-api)
  - [Table of Contents](#table-of-contents)
  - [✨ Key Features](#-key-features)
  - [🚀 Quick Start](#-quick-start)
    - [1. Direct Local Launch](#1-direct-local-launch)
    - [2. Docker Deployment](#2-docker-deployment)
    - [3. Docker Compose Deployment](#3-docker-compose-deployment)
  - [👥 Account Management & Web Dashboard](#-account-management--web-dashboard)
    - [Dashboard Capabilities](#dashboard-capabilities)
    - [Multi-format Cookie Import & Infinite Probing](#multi-format-cookie-import--infinite-probing)
  - [💡 API Usage Examples](#-api-usage-examples)
    - [Authentication Methods](#authentication-methods)
    - [List Available Models (Dynamic Discovery)](#list-available-models-dynamic-discovery)
    - [Standard Generation (generateContent)](#standard-generation-generatecontent)
    - [Real-time Streaming (streamGenerateContent SSE)](#real-time-streaming-streamgeneratecontent-sse)
    - [Thinking Reasoning & Budget (Thinking Chain)](#thinking-reasoning--budget-thinking-chain)
    - [Live Web Search (Google Search)](#live-web-search-google-search)
    - [Extended Built-in Tools (Maps / Code Execution / URL Context)](#extended-built-in-tools-maps--code-execution--url-context)
    - [Function Calling (Custom Tools)](#function-calling-custom-tools)
    - [Multimodal Image Understanding (Image Input)](#multimodal-image-understanding-image-input)
    - [High-Fidelity Image Generation](#high-fidelity-image-generation)
    - [System Instructions](#system-instructions)
    - [Python SDK (Google GenAI SDK)](#python-sdk-google-genai-sdk)
    - [System Monitoring & Rotation REST APIs](#system-monitoring--rotation-rest-apis)
  - [🤖 Supported Models](#-supported-models)
  - [⚙️ Configuration](#️-configuration)
    - [Environment Variables List](#environment-variables-list)
    - [Model Default Behavior Profile (config.yaml)](#model-default-behavior-profile-configyaml)
    - [Safety Review Settings (safety_settings)](#safety-review-settings-safety_settings)
  - [🏗️ Architecture & BotGuard Mechanism](#️-architecture--botguard-mechanism)
    - [Architecture Diagram](#architecture-diagram)
    - [How BotGuard Dynamic Resolution Works](#how-botguard-dynamic-resolution-works)
    - [Lightweight Kernel Blocking Optimization](#lightweight-kernel-blocking-optimization)
  - [CI/CD](#cicd)
  - [Acknowledgements](#acknowledgements)
  - [License](#license)

---

## ✨ Key Features

- **Pure Gemini Native API Protocol**: Faithfully implements standard `/v1beta/models`, `/v1beta/models/{model}:generateContent`, and `/v1beta/models/{model}:streamGenerateContent` endpoints for drop-in compatibility with official SDKs and ecosystems.
- **Dynamic Model Discovery**: Automatically fetches and syncs all models from Google AI Studio via active CDP page sessions or HTTP RPC on startup and runtime (supporting Gemini 3.8 / 3.7 / 3.5, Gemma 4, Nano Banana image models, and preview builds), eliminating hardcoded limitations.
- **Single-Cookie Infinite Multi-Account Probing**: Simply paste one cookie string containing multiple logged-in Google sessions; the engine will automatically and concurrently probe `authuser=0, 1, 2...` to discover all accounts and batch-import them with distinct identities.
- **Multi-Format Cookie Parsing**: Out-of-the-box support for JSON arrays exported from browser extensions (e.g. *Cookie-Editor*), Netscape 7-column formats, and raw HTTP header strings (`Cookie: SID=...`).
- **Multi-Account Intelligent Rotation & Disaster Recovery**:
  - Supports **Round Robin (`round_robin`)**, **Least Recently Used (`lru`)**, and **Least Rate Limited (`least_rl`)** strategies.
  - Automatically isolates accounts encountering 429 rate limits or quota depletion with customizable cooldown timers, and seamlessly retries/fails over on subsequent requests.
- **Comprehensive Tool Calling & Built-in Services**:
  - Official Built-in Tools: Real-time Google Search (`googleSearch` / `googleSearchRetrieval`), Google Maps (`googleMaps`), Code Execution Sandbox (`codeExecution`), and URL Context Extraction (`urlContext`).
  - Custom Function Calling: Full support for structured `functionDeclarations` and `functionResponse` results feedback.
- **Deep Reasoning Process (Thinking Chain)**: Returns thought process blocks with `thought: true` in Parts, allowing configurable reasoning levels (`LOW` / `MEDIUM` / `HIGH` / `MINIMAL`) and token budgets (`thinkingBudget`).
- **Professional Multimodal & Image Generation**:
  - Multimodal Vision: Accepts multiple Base64 inline images (`inlineData`) with rich textual context.
  - Studio Image Generation: Supports Gemini image models (`gemini-3.1-flash-image-preview` / `gemini-3-pro-image-preview`), with granular control over `aspectRatio` (1:1, 16:9, 9:16, etc.), `imageSize` (1K, 2K, 4K), and `responseModalities`.
- **Ultra-Lightweight Asynchronous CDP Engine**:
  - Completely eliminates heavy Selenium, Playwright, and Camoufox dependencies by utilizing native async Chrome DevTools Protocol (CDP) over headless Chromium.
  - Built-in network resource blocker drops unnecessary media, fonts, and tracking scripts, reducing memory footprint by over 80% (~80MB RAM), running smoothly on 1GB VPS and Android Termux / PRoot environments.
- **Modern Responsive Web Dashboard**: Built-in WebUI for real-time traffic statistics, account pool management, rotation strategy adjustments, and API token configuration.

---

## 🚀 Quick Start & Multi-Platform Installation Guide

This project supports deployments on **Linux (x86_64 / arm64)**, **macOS (Apple Silicon / Intel)**, **Windows (x64)**, **Docker containers**, and **Android Termux (Bare-Metal / PRoot)** environments.

### 1. Cross-Platform Prerequisites & Launch

#### 📱 Android Termux Bare-Metal Setup (Recommended)
The Termux native Chromium build lacks SwiftShader / ANGLE, resulting in `NO_GL` (no WebGL context), failing Google BotGuard verification. We recommend running **CloakBrowser via Glibc-Runner**:

```bash
# 1. Install base utilities and Glibc runtime layer
pkg update && pkg install -y python git glibc glibc-runner patchelf-glibc

# 2. Clone the repository
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api

# 3. Install Python dependencies and isolate cache to project folder
export CLOAKBROWSER_CACHE_DIR="$(pwd)/.cloakbrowser"
pip install -r requirements.txt

# 4. Start the server (automatically locates and launches project-scoped .cloakbrowser)
python3 main.py server --port 8080
```

#### 🐧 Linux (Ubuntu / Debian / CentOS / Arch)
```bash
# 1. Install system runtime dependencies
# Ubuntu / Debian:
sudo apt-get update && sudo apt-get install -y \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2

# 2. Clone and install Python dependencies
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api
export CLOAKBROWSER_CACHE_DIR="$(pwd)/.cloakbrowser"
pip install -r requirements.txt

# 3. Start API Server
python3 main.py server --port 8080
```

#### 🍎 macOS (Apple Silicon M1-M4 / Intel)
```bash
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api
export CLOAKBROWSER_CACHE_DIR="$(pwd)/.cloakbrowser"
pip install -r requirements.txt
python3 main.py server --port 8080
```

#### 🪟 Windows (PowerShell)
```powershell
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api
$env:CLOAKBROWSER_CACHE_DIR = "$PWD\.cloakbrowser"
pip install -r requirements.txt
python main.py server --port 8080
```

> **Browser Search Priority**: When the service launches, it prioritizes searching for the **project-scoped `.cloakbrowser` cache**. If not found, it falls back to `~/.cloakbrowser` or system-installed Chromium/Chrome. You can also specify an exact binary with `AISTUDIO_BROWSER_EXECUTABLE`.
### 2. Docker Deployment

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/chrysoljq/aistudio-api:latest
```

### 3. Docker Compose Deployment

A ready-to-use `docker-compose.yml` is provided in the root directory:

```bash
# Start in the background
docker compose up -d

# Check live logs
docker compose logs -f
```

---

## 👥 Account Management & Web Dashboard

After starting the service, open your browser and navigate to:

👉 **`http://localhost:8080`**

![Web Dashboard](image/cookie.png)

### Dashboard Capabilities

1. **Live Analytics**: Monitor the active account, rotation policy, total request counts, 429 rate limit events, and latency breakdown per model.
2. **Account Pool Control**: Inspect all imported accounts, their linked emails, sub-account indices (`u/0`, `u/1`...), request success rates, and manually switch active accounts with one click.
3. **Rotation Configuration**: Switch between `round_robin`, `lru`, and `least_rl` algorithms, configure cooldown periods, and force manual rotation.
4. **Credential Security**: Set API Tokens via the top-right settings dropdown to protect the dashboard and endpoints from unauthorized access.

### Multi-format Cookie Import & Infinite Probing

Click the **"+ Import Cookies"** button in the WebUI:

1. **Extract Cookie**: Log in to your Google Account, visit [Google AI Studio](https://aistudio.google.com/) or [Google My Account](https://myaccount.google.com/), and copy the full Cookie string.
2. **Accepted Formats**:
   - **HTTP Header String**: Paste `Cookie: SID=xxx; HSID=xxx; ...`
   - **JSON Array**: Exported directly from browser extensions like *Cookie-Editor*.
   - **Netscape 7-Column**: Standard plaintext format.
3. **⚡ Auto Infinite Multi-Account Probing**:
   - Check the **"Auto probe and import multiple accounts"** option.
   - If your cookie contains multiple active Google profiles (e.g. personal, secondary, or workspace), the system probes from `authuser=0` downward concurrently, identifying each account's email and registering them as separate accounts in the rotation pool!

---

## 💡 API Usage Examples

### Authentication Methods

When `AISTUDIO_API_KEY` (or comma-separated `AISTUDIO_API_KEYS`) is configured, supply the key in any of the following standard formats:

- **Query Parameter**: `?key=YOUR_API_KEY` (Standard Google Gemini convention)
- **Google Header**: `x-goog-api-key: YOUR_API_KEY`
- **Generic Header**: `x-api-key: YOUR_API_KEY`
- **Bearer Token**: `Authorization: Bearer YOUR_API_KEY`

---

### List Available Models (Dynamic Discovery)

Dynamically queries available models associated with the current Google session:

```bash
curl http://localhost:8080/v1beta/models \
  -H "x-goog-api-key: your-api-key"
```

Or retrieve a specific model's metadata:

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash \
  -H "x-goog-api-key: your-api-key"
```

---

### Standard Generation (generateContent)

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent?key=your-api-key \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "Hello! Introduce yourself in one concise sentence."}
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

### Real-time Streaming (streamGenerateContent SSE)

Append `alt=sse` to receive standard Server-Sent Events (SSE) data streams:

```bash
curl -N http://localhost:8080/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "Write a Python implementation of QuickSort with clear comments."}
        ]
      }
    ]
  }'
```

---

### Thinking Reasoning & Budget (Thinking Chain)

For models supporting deep reasoning, adjust thinking parameters inside `generationConfig`:

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "Which is bigger: 9.11 or 9.8? Think step by step before answering."}
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

> Response Parts containing `thought: true` encapsulate the full chain-of-thought process.

---

### Live Web Search (Google Search)

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "Search for the latest breaking news in Artificial Intelligence today and provide a summary."}
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

### Extended Built-in Tools (Maps / Code Execution / URL Context)

```bash
curl http://localhost:8080/v1beta/models/gemini-3.5-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "Plan a walking tour route from Tokyo Station to Ginza and write code to compute the estimated distance."}
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

### Function Calling (Custom Tools)

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "What is the current temperature in Tokyo?"}
        ]
      }
    ],
    "tools": [
      {
        "functionDeclarations": [
          {
            "name": "get_current_weather",
            "description": "Fetch real-time weather and temperature for a given location",
            "parameters": {
              "type": "object",
              "properties": {
                "location": {
                  "type": "string",
                  "description": "The city name, e.g. Tokyo, San Francisco"
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

### Multimodal Image Understanding (Image Input)

Pass Base64-encoded image data via `inlineData`:

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
            "text": "Describe the contents of this image in detail and extract all visible text."
          }
        ]
      }
    ]
  }'
```

---

### High-Fidelity Image Generation

Call Gemini image models (such as `gemini-3.1-flash-image-preview` or `gemini-3-pro-image-preview`, requiring Pro/Ultra account tier):

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

> **Supported Aspect Ratios (`aspectRatio`)**: `1:1`, `3:4`, `4:3`, `9:16`, `16:9`.  
> **Supported Resolutions (`imageSize`)**: `1K`, `2K`, `4K`.  
> **Response Modalities (`responseModalities`)**: `["IMAGE"]` (image only) or `["IMAGE", "TEXT"]` (mixed).

---

### System Instructions

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "systemInstruction": {
      "parts": [
        {"text": "You are a senior Linux kernel engineer. Answer concisely with technical depth."}
      ]
    },
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "Explain the difference between eBPF and kernel modules."}
        ]
      }
    ]
  }'
```

---

### Python SDK (Google GenAI SDK)

Install the official modern Google GenAI SDK:

```bash
pip install google-genai
```

Usage snippet:

```python
from google import genai
from google.genai import types

# Point base_url to this proxy server's /v1beta root
client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

# 1. Basic Generation
response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="Write an asynchronous Python HTTP client example",
)
print("Response:\n", response.text)

# 2. Live Web Search
search_response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="What are the top breakthroughs in AI this week?",
    config=types.GenerateContentConfig(
        tools=[{"google_search": {}}],
    ),
)
print("Search Response:\n", search_response.text)

# 3. Streaming Generation
for chunk in client.models.generate_content_stream(
    model="gemini-3.7-flash",
    contents="Tell a short science fiction story about deep space exploration",
):
    print(chunk.text, end="", flush=True)
print()
```

---

### System Monitoring & Rotation REST APIs

In addition to the WebUI dashboard, full REST APIs are exposed for automation:

| Method | Endpoint | Description | Auth Required |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Service health status | Public |
| `GET` | `/stats` | Request, 429 rate limit, and latency statistics | Yes |
| `GET` | `/accounts` | List all registered accounts in the pool | Yes |
| `GET` | `/accounts/active` | Get currently active account metadata | Yes |
| `POST` | `/accounts/{id}/activate` | Manually activate a specific account | Yes |
| `DELETE` | `/accounts/{id}` | Remove an account from storage | Yes |
| `POST` | `/accounts/import-cookies` | Import a single account cookie | Yes |
| `POST` | `/accounts/probe-import` | ⚡ Probe and batch-import all multi-session accounts | Yes |
| `GET` | `/rotation` | Get rotator state and per-account stats | Yes |
| `POST` | `/rotation/mode` | Update rotation mode and cooldown seconds | Yes |
| `POST` | `/rotation/next` | Force switch to the next available account | Yes |

---

## 🤖 Supported Models

The proxy features **Dynamic Model Discovery**. Any model available to your Google account will automatically appear in `/v1beta/models` and can be invoked directly.

Common baseline and flagship models include:

| Display Name | Model ID | Web Search | Description |
| :--- | :--- | :---: | :--- |
| **Gemini 3.8 Flash** | `gemini-3.8-flash` | Optional | Latest flagship ultra-fast model, high throughput |
| **Gemini 3.7 Flash** | `gemini-3.7-flash` | Optional | Default text & multimodal model, deep reasoning |
| **Gemini 3.6 Flash** | `gemini-3.6-flash` | Optional | High concurrency lightweight model |
| **Gemini 3.5 Flash** | `gemini-3.5-flash` | Optional | Balanced high-performance model |
| **Gemini 3.5 Flash Lite** | `gemini-3.5-flash-lite` | Optional | Ultra-low latency model |
| **Gemini 3.1 Pro Preview** | `gemini-3.1-pro-preview` | Optional | Complex reasoning, mathematics & coding |
| **Gemma 4 31B IT** | `gemma-4-31b-it` | ✅ Default ON | Google open weights flagship text model |
| **Gemma 4 26B A4B IT** | `gemma-4-26b-a4b-it` | ✅ Default ON | Efficient MoE open architecture model |
| **Gemini 3.1 Flash Image** | `gemini-3.1-flash-image-preview` | Optional | High-quality image generation (Pro/Ultra tier) |
| **Gemini 3 Pro Image** | `gemini-3-pro-image-preview` | Optional | Flagship image creation and editing |
| **Veo 3.1** | `veo-3.1-generate-preview` | ❌ | Video generation preview model |

---

## ⚙️ Configuration

Configure via environment variables or a `.env` file in the project root.

### Environment Variables List

| Variable | Default | Description |
| :--- | :--- | :--- |
| `AISTUDIO_PORT` | `8080` | API service port |
| `AISTUDIO_BROWSER_PORT` | `9222` | Chromium remote debugging port (CDP) |
| `AISTUDIO_BROWSER_HEADLESS` | `1` | Run browser in headless mode (`1` = headless, `0` = headed) |
| `AISTUDIO_BROWSER_EXECUTABLE` | *Empty* | Custom Chromium executable path (auto-detected if empty) |
| `AISTUDIO_PROXY` | *Empty* | Network proxy URL (`http://`, `socks5://`) |
| `AISTUDIO_API_KEY` | *Empty* | Single API key for endpoint authentication |
| `AISTUDIO_API_KEYS` | *Empty* | Multiple API keys, comma-separated (`key1,key2,key3`) |
| `AISTUDIO_AUTH_FILE` | *Empty* | Path to manual auth file (defaults to `data/accounts`) |
| `AISTUDIO_TMP_DIR` | `/tmp` | Temporary working directory for image conversion cache |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | `gemini-3.7-flash` | Default text generation model |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | `gemini-3.1-flash-image-preview` | Default image generation model |
| `AISTUDIO_TIMEOUT_REPLAY` | `120` | Request timeout in seconds |
| `AISTUDIO_TIMEOUT_STREAM` | `120` | Streaming timeout in seconds |
| `AISTUDIO_TIMEOUT_CAPTURE` | `30` | Capture timeout in seconds |
| `AISTUDIO_ACCOUNTS_DIR` | `data/accounts` | Persistent accounts storage directory |
| `AISTUDIO_ACCOUNT_ROTATION_MODE` | `round_robin` | Account rotation mode: `round_robin`, `lru`, `least_rl` |
| `AISTUDIO_ACCOUNT_COOLDOWN_SECONDS` | `60` | Cooldown duration (seconds) after receiving a 429 rate limit |
| `AISTUDIO_ACCOUNT_MAX_RETRIES` | `3` | Maximum automatic account failover retries per request |
| `AISTUDIO_MAX_CONCURRENCY` | `3` | Maximum concurrent request semaphore limit |
| `AISTUDIO_CONFIG_FILE` | *Empty* | Path to custom YAML model defaults profile (`config.yaml`) |
| `AISTUDIO_DUMP_RAW_RESPONSE` | `0` | Save raw Protobuf responses to disk for debugging (`1` = on) |
| `AISTUDIO_DUMP_RAW_RESPONSE_DIR` | `/tmp` | Directory for dumped raw debug responses |

---

### Model Default Behavior Profile (config.yaml)

A `config.yaml` file in the project root allows defining default parameters, default tools, and index cleanups across model families:

```yaml
model_defaults:
  profiles:
    # Specialized profile for Image generation models
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

    # Gemma models (automatically attach Google Search by default)
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

    # Gemini text models (relax safety filters by default)
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
    # Model-specific exact overrides
    gemini-3.1-flash-image-preview:
      generation_config_defaults:
        image_output_mode: text_and_image
        media_resolution: HIGH
```

- **`match`**: `exact` (exact match), `prefixes` (prefix match like `gemini-`), `contains` (substring match like `image`).
- **`thinking_config.level`**: `LOW` / `MEDIUM` / `HIGH` / `MINIMAL`.
- **`media_resolution`**: `LOW` / `MEDIUM` / `HIGH`.

---

### Safety Review Settings (safety_settings)

Supports four major safety categories:
- `Harassment`
- `Hate`
- `Sexually Explicit`
- `Dangerous Content`

Values range from **1 to 5**:
- **`1`**: Strict filtering (block most content).
- **`5`**: Disable filter completely.

When `safety_off=true` is sent or configured as 5, all four categories are relaxed to level 5 to ensure unrestrained generation.

---

## 🏗️ Architecture & BotGuard Mechanism

### Architecture Diagram

```
Client Request (Google GenAI SDK / curl / Custom Client)
                       │
                       ▼
  ┌──────────────────────────────────────────────┐
  │               FastAPI Server                 │
  │     /v1beta/models/... native routing & auth │
  └──────────────────────┬───────────────────────┘
                         │
                         ▼
  ┌──────────────────────────────────────────────┐
  │       Wire Codec & Model Discovery           │
  │  Gemini format ⇄ AI Studio Protobuf wire     │
  │  Dynamic model sync ⇄ Account Rotator        │
  └──────────────────────┬───────────────────────┘
                         │
                         ▼
  ┌──────────────────────────────────────────────┐
  │         Chromium CDP Native Bridge           │
  │  • Kernel-level resource interceptor         │
  │  • Ultra-low memory, persistent Page context │
  │  • Fast-path BotGuard Snapshot generator     │
  └──────────────────────┬───────────────────────┘
                         │
                         ▼
             Google AI Studio Official Service
```

### How BotGuard Dynamic Resolution Works

Google AI Studio validates an encrypted **BotGuard Snapshot** token with each inference call to guarantee requests originate from a authentic browser runtime. Rather than relying on fragile hardcoded function names that change every week (`Mv`, `Ov`, `Sv`, etc.), this project uses **AST & Pattern Fingerprint Matching**:
1. Evaluates scope closures inside the live page context via lightweight JavaScript probes.
2. Identifies the snapshot generator using structural patterns (`.snapshot({` + `content` + `yield`).
3. Produces authentic tokens in milliseconds per request, immune to Google frontend updates.

### Lightweight Kernel Blocking Optimization

To enable long-term stability on low-resource environments (1-core 1GB VPS or Android Termux), kernel-level request blocking is applied directly through the CDP session:
- Rejects non-essential `Image`, `Media`, `Font`, and `Stylesheet` network requests.
- Blocks tracking and telemetry domains (Google Analytics, Sentry, etc.).
- Keeps only the JavaScript execution core necessary for authentication and API communication, reducing RAM usage from 500MB+ down to **~80MB**.

---

## CI/CD

An automated GitHub Actions workflow (`.github/workflows/docker.yml`) is integrated:

- Pushing to `master` or `main` automatically builds and publishes multi-arch Docker images to GitHub Container Registry:  
  `ghcr.io/chrysoljq/aistudio-api:latest`
- Pull Requests trigger automated container build checks and lint verification.

---

## Acknowledgements

- [LuanRT/BgUtils](https://github.com/LuanRT/BgUtils) — Inspired the BotGuard token generation approach
- [iBUHub/AIStudioToAPI](https://github.com/iBUHub/AIStudioToAPI) — Reference for early session structure
- [LINUX DO](https://linux.do) — High quality technical community discussion

---

## License

This project is licensed under the [MIT License](./LICENSE).
