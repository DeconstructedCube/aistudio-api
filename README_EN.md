# AI Studio API

Google AI Studio Playground reverse proxy service supporting Google Membership (Pro/Ultra) accounts and fully compatible with the Google Gemini native API protocol format, featuring image generation, tool calling, and real-time web search.

[中文](./README.md)

## Table of Contents

- [AI Studio API](#ai-studio-api)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Quick Start](#quick-start)
    - [Direct Launch](#direct-launch)
    - [Docker Deployment](#docker-deployment)
    - [Account Management & Login](#account-management--login)
  - [Usage Examples](#usage-examples)
    - [List Models](#list-models)
    - [Gemini Native API (curl)](#gemini-native-api-curl)
    - [Python (Google GenAI SDK)](#python-google-genai-sdk)
  - [Supported Models](#supported-models)
  - [Configuration](#configuration)
    - [Model Configuration](#model-configuration)
    - [Safety Settings](#safety-settings)
  - [Architecture](#architecture)
  - [How BotGuard Works](#how-botguard-works)
  - [TODO](#todo)
  - [Acknowledgements](#acknowledgements)
  - [License](#license)

## Features

- **Gemini Native API Compatibility** — Supports `/v1beta/models`, `/v1beta/models/{model}:generateContent`, and streaming endpoints
- **Flexible Authentication** — Supports `?key=` query parameter, `x-goog-api-key` header, `x-api-key`, and `Authorization: Bearer`
- **Streaming Output** — Real-time server-sent events (SSE) streaming responses
- **Multi-turn Conversations** — Properly maintains alternating `user`/`model` structure
- **Multimodal Image Input** — Supports base64 inline encoding and image uploads (single or multiple images)
- **Google Search** — Real-time web search via `googleSearchRetrieval`
- **Thinking Process** — Returns the model's chain-of-thought process via the `thinking` part
- **Image Generation** — High quality image generation with Gemini image models
- **Native Chromium CDP Bridge** — High-performance, lightweight Chromium remote debugging bridge
- **Dynamic BotGuard Resolution** — Automatically matches patterns to locate the `snapshot` function
- **Multi-account Rotation** — Round-robin / LRU / least rate-limited account selection

## Quick Start

### Direct Launch

```bash
# Clone the repository
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api

# Install dependencies
pip install -r requirements.txt

# Start the service (Chromium will be automatically launched)
python3 main.py server --port 8080
```

### Docker Deployment

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/chrysoljq/aistudio-api:latest
```

### Account Management & Login

After starting the server, visit `http://localhost:8080` to access the management dashboard:
1. Click the **"Import Cookies"** button.
2. Visit [Google My Account](https://myaccount.google.com/) or AI Studio and copy the full Cookie string.
3. Paste into the modal. The system supports **probing and batch importing multiple Google accounts from a single cookie string**.

![alt text](image/cookie.png)

## Usage Examples

### List Models

```bash
curl http://localhost:8080/v1beta/models \
  -H "x-goog-api-key: your-secret-token"
```

### Gemini Native API (curl)

```bash
# Standard Generation
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent?key=your-secret-token \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Hello! Introduce yourself."}]}]
  }'

# Web Search
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "How is the weather in Shanghai today?"}]}],
    "tools": [{"googleSearchRetrieval": {}}]
  }'

# Streaming Generation (SSE)
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Write quicksort in Python."}]}]
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
    contents="Hello!",
)
print(response.text)
```

## Supported Models

| Model | ID | Default Google Search | Description |
|-------|----|-----------------------|-------------|
| Gemini 3.7 Flash | `gemini-3.7-flash` | ❌ | Default text and multimodal model |
| Gemma 4 31B | `gemma-4-31b-it` | ✅ | Open weights large model |
| Gemma 4 26B A4B | `gemma-4-26b-a4b-it` | ✅ | MoE architecture |
| Gemini 3.5 Flash | `gemini-3.5-flash` | ❌ | Fast and efficient |
| Gemini 3.1 Pro | `gemini-3.1-pro-preview` | ❌ | Advanced reasoning |
| Gemini 3.1 Flash Lite | `gemini-3.1-flash-lite` | ❌ | Lightweight and low latency |
| Gemini 3.1 Flash Image | `gemini-3.1-flash-image-preview` | ❌ | Default image model, Pro/Ultra only |
| Gemini 3 Pro Image | `gemini-3-pro-image-preview` | ❌ | High-fidelity image model |

## Configuration

Configure via environment variables or a `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `AISTUDIO_PORT` | `8080` | API service port |
| `AISTUDIO_BROWSER_PORT` | `9222` | Chromium remote debugging port |
| `AISTUDIO_BROWSER_HEADLESS` | `1` | Run browser in headless mode (1=headless, 0=headed) |
| `AISTUDIO_BROWSER_EXECUTABLE` | None | Path to Chromium executable (auto-detected if empty) |
| `AISTUDIO_PROXY` | None | Browser proxy address |
| `AISTUDIO_API_KEY` | None | API authentication key (enables auth when set) |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | `gemini-3.7-flash` | Default chat model |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | `gemini-3.1-flash-image-preview` | Default image model |
| `AISTUDIO_TIMEOUT_REPLAY` | `120` | Request timeout (seconds) |
| `AISTUDIO_TIMEOUT_STREAM` | `120` | Stream timeout (seconds) |
| `AISTUDIO_SNAPSHOT_CACHE_TTL` | `3600` | BotGuard snapshot cache duration (seconds) |
| `AISTUDIO_ACCOUNTS_DIR` | `data/accounts` | Account storage persistence directory |
| `AISTUDIO_ACCOUNT_ROTATION_MODE` | `round_robin` | Rotation mode: `round_robin`, `lru`, `least_rl` |
| `AISTUDIO_ACCOUNT_COOLDOWN_SECONDS` | `60` | Cooldown duration after rate limit (seconds) |
| `AISTUDIO_DUMP_RAW_RESPONSE` | `0` | Save raw responses to disk (for debugging) |
### Model Configuration

An optional `config.yaml` is supported in the root directory to supply default parameters for different model families. By default, it reads the `config.yaml` in the project root, but you can use `AISTUDIO_CONFIG_FILE` to point to a different config file.

It is currently used for:

- Setting default behaviors for `gemma` / `gemini` / image models separately
- Supplementing `generation_config` default values for specific models
- Controlling which wire indexes to clear for certain image models
- Configuring default tools, such as `google_search`
- Configuring `safety_settings`

Built-in example in the repo:

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

`match` supports three matching modes:

- `exact`: Matches the exact model name.
- `prefixes`: Matches the prefix of the model name (e.g., `gemma-`, `gemini-`).
- `contains`: Matches if the model name contains the specified substring.

`generation_config_defaults` currently supports these common fields:

- `response_mime_type`
- `thinking_config`
- `image_output_mode`
- `media_resolution`

Several fields have human-readable wrappers:

- `thinking_config.level`: `LOW` / `MEDIUM` / `HIGH` / `MINIMAL`
- `image_output_mode`: `image_only` or `text_and_image`
- `media_resolution`: `LOW` / `MEDIUM` / `HIGH`

You can also override settings for a single model:

```yaml
model_defaults:
  models:
    gemini-3.1-flash-image-preview:
      generation_config_defaults:
        image_output_mode: text_and_image
        media_resolution: HIGH
```

### Safety Settings

`safety_settings` currently supports these four categories:

- `Harassment`
- `Hate`
- `Sexually Explicit`
- `Dangerous Content`

Values range from `1` to `5`:

- `1` represents the most restrictive (strictly block)
- `5` represents turning safety checks off

Example:

```yaml
safety_settings:
  Harassment: 1
  Hate: 2
  Sexually Explicit: 3
  Dangerous Content: 5
```

Notes:

- Text models will propagate this config group down to AI Studio wire requests.
- `safety_off=true` will directly set all four categories to `5`.
- The default image model config sets `disable_safety_settings: true`, so image models will clear safety setting fields.

## Docker Image CI

This repo includes a GitHub Actions workflow at `.github/workflows/docker.yml`.

- Changes under `src/**` trigger Docker builds on `push` and `pull_request`
- `pull_request` runs build validation only and does not push an image
- Pushes to `main` / `master` publish the image to `ghcr.io/chrysoljq/aistudio-api`
- You can also run it manually with `workflow_dispatch`

The workflow uses GitHub's built-in `GITHUB_TOKEN` for GHCR, so no separate Docker Hub account is required.

## Architecture

```
Client (Google GenAI SDK / curl / Web)
    │
    ▼
┌─────────────────────┐
│   FastAPI Server    │  ← /v1beta/models/... native API routes
│   /v1beta/...       │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   Wire Codec        │  ← Gemini API format ⇄ AI Studio gRPC body
│   + BotGuard        │     Auto-detects snapshot function via features
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Chromium Browser   │  ← Native Chromium CDP debugging bridge, injects cookies
│  (headless)         │     Maintains sessions and execution
└─────────┬───────────┘
          │
          ▼
    Google AI Studio
```

**How it works:**
1. An API request comes in and is converted into AI Studio's wire format.
2. A BotGuard snapshot is generated (auto-detects the check function, with caching).
3. The full gRPC body is constructed and transmitted via CDP session in the authenticated page context.
4. The browser sends the request to Google (with valid cookies + BotGuard).
5. The response is parsed and returned in standard Gemini native format.

Rotation modes:
- `round_robin` — Cycle through accounts
- `lru` — Least recently used
- `least_rl` — Least rate-limited

## BotGuard Works

Google requires a BotGuard "snapshot" with every request — an encrypted credential proving the request originates from a real browser. This project:

1. Hooks the frontend snapshot generation function at runtime.
2. Auto-detects it via feature matching (`.snapshot({` + `content` + `yield`), resisting Google bundle updates.
3. Generates valid snapshots for each request.

The snapshot function name constantly changes with Google bundle updates (Mv → Ov → Sv → ...), but the feature pattern remains identical.

## TODO
- [ ] Enhanced web management UI
- [ ] Additional multimodal audio/video input support
## Acknowledgements
- https://github.com/LuanRT/BgUtils
- https://github.com/iBUHub/AIStudioToAPI
- https://linux.do

## License

MIT
