# aistudio-api

<p align="center">
  <a href="https://github.com/DeconstructedCube/aistudio-api"><img src="https://img.shields.io/badge/API-Gemini%20v1beta-4285F4?style=flat-square&logo=google" alt="Gemini API"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/Framework-FastAPI-009688?style=flat-square&logo=fastapi" alt="FastAPI"></a>
  <a href="https://vuejs.org/"><img src="https://img.shields.io/badge/Frontend-Vue%203%20%2B%20Vite-4FC08D?style=flat-square&logo=vuedotjs" alt="Vue 3"></a>
  <a href="https://docs.astral.sh/uv/"><img src="https://img.shields.io/badge/Python-3.11%2B%20%7C%20uv-261230?style=flat-square&logo=python" alt="Python uv"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License"></a>
</p>

<p align="center">
  Google AI Studio reverse proxy gateway that exposes native Google Gemini API interfaces.
</p>

<p align="center">
  <a href="./README.md">中文文档</a>
</p>

---

## Table of Contents

- [Features](#features)
- [Architecture & Documentation](#architecture--documentation)
- [Quick Start](#quick-start)
  - [Prerequisites](#prerequisites)
  - [Linux / macOS / Windows](#linux--macos--windows)
  - [Android (Termux)](#android-termux)
  - [Docker Deployment](#docker-deployment)
- [Configuration & Environment Variables](#configuration--environment-variables)
- [API Usage Examples](#api-usage-examples)
  - [cURL](#curl)
  - [Python SDK (google-genai)](#python-sdk-google-genai)
  - [Function Calling](#function-calling)
- [Web Management Console](#web-management-console)
- [Development & Testing](#development--testing)
- [License](#license)

---

## Features

| Feature | Description |
|---|---|
| **Native Gemini API** | Full compatibility with the official `/v1beta/...` specification, supporting Thinking process, Multimodal inputs, Function Calling, and Image Generation |
| **Dynamic Model Discovery** | Automatically synchronizes and discovers available models from upstream Google AI Studio |
| **Sticky Account Dispatch** | Maintains account state per model, tracks 429 rate-limit quotas & 403 authorization isolation independently, and resets cooldowns at midnight Pacific Time |
| **Multi-Account Probing** | Extracts and verifies sub-accounts (`u/0`, `u/1`...) automatically when importing a multi-session Cookie |
| **Built-in Tools** | Supports official tools such as Google Search, Google Maps, and Code Execution sandbox |
| **Pure Python CDP** | Direct Chrome DevTools Protocol communication over asynchronous WebSockets without Node.js, Playwright, or Selenium |
| **Web Management Console** | Built-in web dashboard for account management, live request metrics, and runtime `config.yaml` hot reloading |

> [!NOTE]
> **Memory Footprint**: The standalone Python backend consumes ~35 - 45 MB RAM. With a single controlled Chromium instance, total actual system memory (PSS) is ~350 - 450 MB on Termux proot (~250 - 350 MB on native Linux). On Android Termux, ≥ 1 GB free RAM is recommended.

## Architecture & Documentation

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

Technical reference documentation:

- [System Architecture Specification (ARCHITECTURE.md)](./docs/ARCHITECTURE.md): Layered architecture, request lifecycle, wire codec, and concurrency model.
- [Wire Protocol Reverse Engineering Specification (WIRE_SPECIFICATION.md)](./docs/WIRE_SPECIFICATION.md): Protobuf-JSON array mappings, field indices, multimodal Part structures, and Schema encoding.
- [BotGuard Verification Chain (BOTGUARD_VERIFICATION_CHAIN.md)](./docs/BOTGUARD_VERIFICATION_CHAIN.md): WAA challenge handshake, dynamic Wasm signing, content hashing, and upstream validation.
- [Agent & Engineering Conventions (AGENT.md)](./AGENT.md): Strict type specifications, testing standards, and development guidelines.

---

## Quick Start

### Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) package manager (recommended)
- Chromium / Google Chrome installed on host (Termux installs CloakBrowser via proot container automatically)

### Linux / macOS / Windows

```bash
# 1. Clone repository
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api

# 2. Sync dependencies
uv sync

# 3. Start server
uv run python3 main.py server --port 8080
```

> [!TIP]
> Using `uv sync` ensures exact wheel resolution across platforms and prevents compilation issues.

### Android (Termux)

On Termux, Chromium runs inside a lightweight `proot-distro` Ubuntu container to resolve Glibc/Bionic compatibility:

```bash
# 1. Setup environment and sync dependencies
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
bash scripts/setup-env.sh
uv sync

# 2. Setup managed browser runtime (run once)
bash scripts/setup-browser.sh

# 3. Start server
uv run python3 main.py server --port 8080
```

### Docker Deployment

Run with Docker CLI:

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/chrysoljq/aistudio-api:latest
```

Or with Docker Compose:

```bash
docker compose up -d
```

---

## Configuration & Environment Variables

Open `http://localhost:8080` in your browser to access the Web Console.

### Environment Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `AISTUDIO_PORT` | int | `8080` | HTTP service listening port |
| `AISTUDIO_WEB_PASSWORD` | string | `""` | Web Console access password (also checks `AISTUDIO_ADMIN_PASSWORD`) |
| `AISTUDIO_PROXY` | string | `""` | Outbound proxy URL (`http://`, `https://`, or `socks5://`) |
| `AISTUDIO_BROWSER_EXECUTABLE` | string | Auto | Path to Chromium executable |
| `AISTUDIO_BROWSER_PORT` | int | `9222` | Chromium remote debugging port (CDP) |
| `AISTUDIO_BROWSER_HEADLESS` | bool | `true` | Run Chromium in headless mode |
| `AISTUDIO_SNAPSHOT_CACHE_TTL` | int | `3600` | BotGuard snapshot cache TTL in seconds |
| `AISTUDIO_PROOT_NAME` | string | `aistudio-api` | Dedicated proot-distro container name for Termux |
| `AISTUDIO_ACCOUNTS_DIR` | string | `data/accounts` | Directory for storing account credentials |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | string | `gemini-3.7-flash` | Default text model |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | string | `gemini-3.1-flash-image-preview` | Default image generation model |

---

## API Usage Examples

Supported authentication methods:
- Query param: `?key=YOUR_API_KEY`
- Header: `x-goog-api-key: YOUR_API_KEY`, `x-api-key: YOUR_API_KEY`, or `Authorization: Bearer YOUR_API_KEY`

### cURL

<details>
<summary><b>1. List Models</b></summary>

```bash
curl http://localhost:8080/v1beta/models \
  -H "x-goog-api-key: your-api-key"
```
</details>

<details>
<summary><b>2. Generate Content (Non-streaming)</b></summary>

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "Explain quantum computing in one sentence."}]
      }
    ]
  }'
```
</details>

<details>
<summary><b>3. Stream Generate Content (SSE)</b></summary>

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "Explain how Python asyncio works."}]
      }
    ]
  }'
```
</details>

---

### Python SDK (google-genai)

Using the official `google-genai` Python SDK:

```python
from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

# 1. Streaming response
response = client.models.generate_content_stream(
    model="gemini-3.7-flash",
    contents="What is reactive programming?",
)
for chunk in response:
    print(chunk.text, end="", flush=True)

# 2. Non-streaming response
result = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="Write a quicksort implementation in Python.",
)
print(result.text)
```

---

### Function Calling

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

# Tool declaration
weather_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_current_weather",
            description="Get real-time weather conditions for a given location",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "location": types.Schema(
                        type="STRING",
                        description="City name, e.g. 'San Francisco' or 'Tokyo'",
                    ),
                    "unit": types.Schema(
                        type="STRING",
                        enum=["celsius", "fahrenheit"],
                        description="Temperature unit",
                    ),
                },
                required=["location"],
            ),
        )
    ]
)

response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="What is the weather in Tokyo right now?",
    config=types.GenerateContentConfig(tools=[weather_tool]),
)

if response.function_calls:
    for call in response.function_calls:
        print(f"Tool called: {call.name}")
        print(f"Arguments: {call.args}")
```

---

## Web Management Console

The service includes a built-in static Web Console served at `http://localhost:8080`:

- **Dashboard**: Real-time view of service health, per-model request stats, success rates, and rate-limit cooldown status.
- **Accounts**: Import Cookie credentials (single-line or JSON), auto-probe multiple sub-accounts, switch active account, or manually reset cooldown.
- **Settings**: Online editor for `config.yaml` with instant hot-reloading on save.
- **Access Control**: Enable authentication by setting `AISTUDIO_WEB_PASSWORD`.

---

## Development & Testing

Frontend code is located in `web/` (Vue 3 + TypeScript + Vite + Tailwind CSS):

```bash
cd web
bun install
bun run dev          # Start local Vite dev server (localhost:3000 -> 8080)
bun run type-check   # Run TypeScript type check
bun run lint         # Run ESLint check
bun run build        # Build production assets into src/aistudio_api/static
```

Python quality checks and tests:

```bash
# Run code style & lint check
uv run ruff check .
# Run static type checking
bun x pyright

# Run test suite
uv run pytest
```

---

## License

This project is licensed under the [MIT License](./LICENSE).
