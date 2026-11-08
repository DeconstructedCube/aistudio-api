# aistudio-api

<p align="center">
  <a href="https://github.com/DeconstructedCube/aistudio-api"><img src="https://img.shields.io/badge/API-Gemini%20v1beta-4285F4?style=flat-square&logo=google" alt="Gemini API"></a>
  <a href="https://hub.docker.com/r/bedrockblock/aistudio-api"><img src="https://img.shields.io/badge/Docker-bedrockblock%2Faistudio--api-2496ED?style=flat-square&logo=docker" alt="Docker Hub"></a>
  <a href="https://deepwiki.com/DeconstructedCube/aistudio-api"><img src="https://img.shields.io/badge/DeepWiki-Docs-5B68E4?style=flat-square" alt="DeepWiki"></a>
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
  - [Android (Termux)](#android-termux)
  - [Windows](#windows)
  - [Linux / macOS](#linux--macos)
  - [Docker Deployment](#docker-deployment)
- [Obtaining & Importing Account Credentials (Cookies)](#obtaining--importing-account-credentials-cookies)
- [Third-Party Client Setup Guide (NextChat / Cherry Studio)](#third-party-client-setup-guide-nextchat--cherry-studio--one-api)
- [Configuration & Environment Variables](#configuration--environment-variables)
- [API Usage Examples](#api-usage-examples)
  - [cURL](#curl)
  - [Python SDK (google-genai)](#python-sdk-google-genai)
  - [Function Calling](#function-calling)
- [Web Management Console](#web-management-console)
- [Development & Testing](#development--testing)
- [Credits & Acknowledgments](#credits--acknowledgments)
- [License](#license)

---

## Features

| Feature | Description |
|---|---|
| **Gemini API Compatibility** | Supports `/v1beta/...` endpoints including Thinking, multimodal inputs, function calling, and image generation |
| **Dynamic Model Discovery** | Automatically synchronizes and discovers available models from upstream Google AI Studio |
| **Multi-Account Rotation** | Manages multiple accounts with automatic failover, tracks 429 rate limits per model independently, and resets quotas at midnight Pacific Time |
| **Runtime State Persistence** | Model usage statistics (`stats.json`) and account 429 cooldown & quota status (`rotator_state.json`) are automatically persisted to disk across restarts |
| **Downstream Parameter Fault Tolerance** | `drop_unsupported_params` automatically drops unsupported safety categories (e.g. `HARM_CATEGORY_CIVIC_INTEGRITY`) and tools to prevent 400 errors |
| **Multi-Account Probing** | Extracts and verifies sub-accounts (`u/0`, `u/1`...) automatically when importing a multi-session Cookie |
| **Built-in Tools** | Supports official tools such as Google Search, Google Maps, and Code Execution sandbox |
| **Dual-Track Signing & Feature Alignment** | Browserless architecture supporting standalone Go signer (SpiderMonkey 152) or remote NodeVM signers with matched User-Agent/header binding |
| **Web Management Console** | Built-in web dashboard for account management (supporting text and JSON bundle file imports), live request metrics, and runtime `config.yaml` hot reloading |
> [!NOTE]
> **Memory Footprint**: Operating completely browserless, resident memory is only ~35 - 50 MB with sub-second cold start, running smoothly even on lightweight mobile Termux environments or entry-level servers.
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
│  (Sticky & 429 Isolation) │   (Protobuf-JSON / WAA)    │
└───────────────────────────┼────────────────────────────┘
                            │ Proof Mint & HTTP/2 Stream
┌───────────────────────────▼────────────────────────────┐
│       Browserless WAA Engine (waa-signer / Remote NodeVM)     │
│       Async Connection Pooling (HTTPStreamTransport)   │
└───────────────────────────┬────────────────────────────┘
                            │ HTTPS / RPC
┌───────────────────────────▼────────────────────────────┐
│         Google AI Studio (alkalimakersuite-pa)         │
└────────────────────────────────────────────────────────┘
```

Technical reference documentation:

- [System Architecture Specification (ARCHITECTURE.md)](./docs/ARCHITECTURE.md): Layered architecture, request lifecycle, wire codec, and concurrency model.
- [Wire Protocol Reverse Engineering Specification (WIRE_SPECIFICATION.md)](./docs/WIRE_SPECIFICATION.md): Protobuf-JSON array mappings, field indices, multimodal Part structures, and Schema encoding.
- [BotGuard Verification Chain (BOTGUARD_VERIFICATION_CHAIN.md)](./docs/BOTGUARD_VERIFICATION_CHAIN.md): WAA challenge handshake, content hashing, snapshot signing, and upstream validation.
- [WAA VM Architecture & Signing Specification (WAA_VM_ARCHITECTURE.md)](./docs/WAA_VM_ARCHITECTURE.md): Polymorphic instruction set, 5-stage ARX stream key derivation, DOM probes, and `!proof` TLV schema.
- [Agent & Engineering Conventions (AGENT.md)](./AGENT.md): Strict type specifications, testing standards, and development guidelines.

---

## Quick Start

> [!NOTE]
> **Out-of-the-Box Operation**: The service comes pre-configured with a remote WAA proof signing node. Once dependencies are installed, **start the server directly without compilation**! For offline operation or ultra-fast 10-30 ms local performance, compile the native engine at any time (see the optional advanced guides below).
### Android (Termux)

> [!TIP]
> **Download Notice**: Always install Termux from [F-Droid](https://f-droid.org/packages/com.termux/) or [GitHub Releases](https://github.com/termux/termux-app/releases). **Do NOT install from the Google Play Store** (the Play Store build is obsolete and will produce repository update errors).

Open Termux and run the following commands step by step:

```bash
# Step 1: Update Termux environment and install git
pkg update -y && pkg install -y git

# Step 2: Clone repository and enter directory
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api

# Step 3: Initialize Python environment & sync dependencies
bash scripts/setup-env.sh
uv sync

# Step 4: Start server (out-of-the-box)
uv run python main.py server --port 8080
```

<details>
<summary><b>[Optional Advanced] Build local native signer service (waa-signer)</b></summary>

To operate completely offline with 10-30 ms local proof minting:
```bash
pkg install -y golang zig
bash waa-signer/build.sh
```
Restart the service upon build completion; it will automatically detect and switch to the local native engine.
</details>

Once started:
1. Open your phone's browser and go to **`http://localhost:8080`** to access the Web Console;
2. Follow the [Cookie Import Guide](#obtaining--importing-account-credentials-cookies) below to import your credentials;
3. **Background Persistence Tip**: Pull down Android's notification bar, tap `Acquire Wakelock` on Termux's notification, and disable battery optimization for Termux in phone system settings to prevent sleep kills.
---

### Windows

Recommended: [Git](https://git-scm.com/) installed. Python environments are automatically managed by `uv`.

Open PowerShell or Windows Terminal:

```bat
# Step 1: Install uv package manager
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Step 2: Clone repository and enter directory
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api

# Step 3: Sync dependencies (uv downloads required Python automatically)
uv sync

# Step 4: Start server (out-of-the-box)
uv run python main.py server --port 8080
```

<details>
<summary><b>[Optional Advanced] Build local native signer service (waa-signer.exe)</b></summary>

To enable the local native proof engine:
```powershell
# Recommended compiler install (the script also guides automatic install):
winget install GoLang.Go zig.zig

# Run build script
.\waa-signer\build.bat
```
Restart the service upon completion.
</details>

Open **`http://localhost:8080`** in your browser to access the Web Console.

---

### Linux / macOS

Open a terminal and run:

```bash
# Step 1: Install uv package manager
curl -LsSf https://astral.sh/uv/install.sh | sh

# Step 2: Clone repository and enter directory
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api

# Step 3: Sync dependencies
uv sync

# Step 4: Start server (out-of-the-box)
uv run python3 main.py server --port 8080
```

<details>
<summary><b>[Optional Advanced] Build local native signer service</b></summary>

Prerequisite: Go 1.22+ and zig (or clang / gcc):
```bash
bash waa-signer/build.sh
```
</details>

Open **`http://localhost:8080`** in your browser to access the Web Console.
---

### Docker Deployment

> [!NOTE]
> The project's `Dockerfile` uses a multi-stage build that **automatically downloads dependencies and compiles** `waa-signer` inside the image. The container starts with the native local signing daemon enabled out of the box—no manual build steps required.

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/deconstructedcube/aistudio-api:latest
```
Or run with Docker Compose:

```bash
docker compose up -d
```
Open **`http://localhost:8080`** in your browser to access the Web Console.

---

## Obtaining & Importing Account Credentials (Cookies)

This service does not store your Google account password. It uses standard web session credentials with automated periodic Cookie rotation.

### Step 1: Obtain Google AI Studio Cookies

1. Visit and log in to [Google AI Studio](https://aistudio.google.com/) in your browser, ensuring you can reach the chat workspace;
2. Install the free **Cookie-Editor** browser extension (available on Chrome Web Store, Edge Add-ons, or Firefox Add-ons);
3. On the `aistudio.google.com` tab, click the Cookie-Editor extension icon;
4. Click **Export** in the bottom-right and select **Export as JSON** (or Export as Header); the data is automatically copied to your clipboard.

### Step 2: Import into Web Console

1. Access the Web Console (default `http://localhost:8080`);
2. Click **Accounts** in the left sidebar;
3. Click **"+ Import Credentials"** in the top right;
4. Paste your clipboard data into the input box and click **Confirm Import**;
5. The service will automatically test connectivity and probe any multi-login sub-accounts (`u/0`, `u/1`...) to register them for load balancing.

---

## Third-Party Client Setup Guide (NextChat / Cherry Studio / One API)

> [!WARNING]
> **Strictly Gemini Protocol Required**!
> This project is a dedicated reverse proxy strictly adhering to the Google Gemini API specification. It **does NOT provide OpenAI-compatible routes** (such as `/v1/chat/completions`). In all third-party clients, you MUST select **Google Gemini** (or **Gemini**) as the model provider type.

| Setting | Value | Description |
|---|---|---|
| **Provider / Model Type** | **Google Gemini** | Do NOT select OpenAI or custom OpenAI formats |
| **API Base URL** | `http://localhost:8080` | If the client mandates a version prefix, enter `http://localhost:8080/v1beta` |
| **API Key** | Any string (e.g. `aistudio-api`) | If `api_keys` is not set in `config.yaml`, any non-empty string works; otherwise enter your configured key |
| **Supported Models** | `gemini-3.7-flash`<br/>`gemini-3.8-flash`<br/>`gemini-4-argon`<br/>`gemini-3.1-flash-image` | Supports all active models discovered dynamically from Google AI Studio |

## Configuration & Environment Variables

Open `http://localhost:8080` in your browser to access the Web Console.

### Environment Variables
| Variable | Type | Default | Description |
|---|---|---|---|
| `AISTUDIO_PORT` | int | `8080` | HTTP service listening port |
| `AISTUDIO_WEB_PASSWORD` | string | `""` | Web Console access password (also checks `AISTUDIO_ADMIN_PASSWORD`) |
| `AISTUDIO_PROXY` | string | `""` | Outbound proxy URL (`http://`, `https://`, or `socks5://`) |
| `AISTUDIO_DATA_DIR` | string | `data/` | Main data directory for persistent files (`/app/data` in Docker) |
| `AISTUDIO_ACCOUNTS_DIR` | string | `data/accounts` | Directory for storing account credentials |
| `AISTUDIO_STATS_FILE` | string | `data/stats.json` | Persistent storage file for model usage metrics |
| `AISTUDIO_ROTATOR_STATE_FILE` | string | `data/rotator_state.json` | Persistent storage file for account 429 cooldowns and dispatch states |
| `AISTUDIO_CONFIG_FILE` | string | `config.yaml` | Path to model rules and default tools configuration YAML |
| `AISTUDIO_PERSIST_STATS` | bool | `true` | Persist request and token usage metrics across service restarts |
| `AISTUDIO_PERSIST_ROTATOR` | bool | `true` | Persist account 429 rate limit dates & cooldowns across restarts |
| `AISTUDIO_PROOF_SERVER_URL` | string | `http://127.0.0.1:8081` | Proof server URL override; recommended to configure servers & bound feature headers in `config.yaml` (`waa.servers`) with manual switching |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | string | `gemini-3.7-flash` | Default text model |
| `AISTUDIO_TIMEZONE` | string | Auto | Wire protocol timezone (`America/Los_Angeles` by default) |
| `AISTUDIO_DUMP_REQUESTS` | bool | `false` | Dump API request/response exchanges to console logs (enabled via `DEBUG=1`) |
| `AISTUDIO_DUMP_FILE` | bool | `false` | Dump request/response exchanges to individual disk files |
| `AISTUDIO_DUMP_DIR` | string | `dumps/` | Target directory for dumped exchange files |

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
- **Accounts**: Import Cookie credentials (single text or bundle files), auto-probe multiple accounts, switch the active account, or reset rate limits.
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

## Credits & Acknowledgments

During reverse engineering, attestation protocol analysis, and gateway implementation, this project referenced and built upon the following open-source projects and community works:

- **[Mag1cFall/AIStudio2API](https://github.com/Mag1cFall/AIStudio2API)**: Provided the embedded SpiderMonkey 152 runtime and Firefox 152 DOM / WebIDL host environment implementation, along with the uTLS transport stack design. The standalone `waa-signer` module is extracted and adapted from its core logic.
- **[Lianues/aistudio-pg-bridge](https://github.com/Lianues/aistudio-pg-bridge)**: Pioneered the lightweight browserless WAA proof signing service concept and established the `/v1/proof/mint` API interface specification.
- **[spinsnow](https://waa.spinsnow.fun)**: Provided a public, stable Node.js VM WAA attestation relay service.
- **[Camoufox](https://github.com/daijro/camoufox)** & **[go-browserforge](https://github.com/brainplusplus/go-browserforge)**: Provided real-world browser fingerprint datasets and anti-fingerprinting generation algorithms.

---

## License

This project is licensed under the [MIT License](./LICENSE).
