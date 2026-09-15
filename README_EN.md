# aistudio-api

Google AI Studio API reverse proxy. Exposes Google Gemini native API protocol.

[中文文档](./README.md)

## Features

- Native Gemini API protocol compatibility (Thinking, Multimodal, Function Calling, and Image Generation)
- Dynamic model discovery synchronized with Google AI Studio
- Multi-account sticky rotation with per-model independent failover
- Automatic downward account probing for multi-login sessions (u/0, u/1...)
- Official tools support: Google Search, Google Maps, Code Execution Sandbox, URL Context
- Web Management Console (Account pool, real-time stats, online rule hot reloading, and auth guards)
- Lightweight headless browser environment driven by pure-Python asynchronous CDP

Memory footprint reference: pure-Python service layer runs at 30 - 85 MB; with the bundled CloakBrowser enabled, the daemon runs at 500 - 650 MB. On Termux ensure the device has at least 1 GB of free RAM.

## System Architecture & Technical Specifications

- [System Architecture Specification (ARCHITECTURE.md)](./docs/ARCHITECTURE.md): Layered clean architecture, concurrency pipeline, wire codec, and cross-platform resource model.
- [BotGuard Verification Chain Specification (BOTGUARD_VERIFICATION_CHAIN.md)](./docs/BOTGUARD_VERIFICATION_CHAIN.md): WAA challenge handshake, Wasm dynamic signature evaluation, content hashing, and server-side verification pipeline.

## Installation

### Prerequisites

- Python 3.10 or higher
- Chromium browser and required runtime libraries

### Linux / macOS / Windows

All platforms install through [`uv`](https://docs.astral.sh/uv/), which honours the pinned `uv.lock` / `pyproject.toml`. After installing `uv`, a single `uv sync` produces the version-locked virtual environment:

```bash
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
uv sync
uv run python3 main.py server --port 8080
```

> Please ensure you use `uv sync` to install dependencies. Do not use `pip install -r requirements.txt`, as it will cause aarch64 prebuilt package version mismatches.

### Android Termux

Running on Termux requires `proot-distro` to provide a standard Linux environment. Execute the following commands to install and start the service:

```bash
pkg update
pkg install -y python git uv proot-distro
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
uv sync
bash scripts/install_termux_prereqs.sh --project-root "$PWD"
uv run python3 main.py server --port 8080
```

> The `install_termux_prereqs.sh` script will automatically set up a dedicated `aistudio-api` container and download the required browser on its first run (this may take a few minutes depending on your network).
> For troubleshooting (e.g., port collisions, container errors), please check project Issues or discussions.

### Docker

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/chrysoljq/aistudio-api:latest
```

Docker Compose:

```bash
docker compose up -d
```

## Web Management Console & Configuration

Access `http://localhost:8080` to enter the Web Management Console:

- **Dashboard**: Real-time stats per model with independent request volumes, success rates, 429 rate limit events, and integration quick-start snippets.
- **Account Management**: Support for importing Google cookies, automatic probing for multi-login accounts, per-model quota status visibility, and manual activation.
- **Sticky Rotation**: Retains the active account until a 429 quota exhaustion event occurs, automatically failing over to the next healthy account.
- **Model Rules**: Inspect and edit config.yaml online with zero-downtime hot reloading.
- **Security**: Dedicated /login view and route guards protecting web endpoints when AISTUDIO_WEB_PASSWORD is set.

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `AISTUDIO_PORT` | Service listening port | `8080` |
| `AISTUDIO_WEB_PASSWORD` | Web Management Console login password (also supports `AISTUDIO_ADMIN_PASSWORD`) | None |
| `AISTUDIO_PROXY` | HTTP / SOCKS5 outbound proxy address | None |
| `AISTUDIO_BROWSER_EXECUTABLE` | Path to Chromium executable | Auto-detected |
| `AISTUDIO_SNAPSHOT_CACHE_TTL` | BotGuard snapshot cache TTL in seconds | `3600` |

Model default behaviors and tools are defined in `config.yaml`.

### Frontend Development & Build

The Web Console source code resides in `web/` (built with Vite + Vue 3 + TypeScript):

```bash
cd web
bun install          # Install dependencies
bun run dev          # Start Vite dev server (port 3000, proxies to 8080 API)
bun run type-check   # Run TypeScript type check
bun run lint         # Run ESLint code quality checks
bun run build        # Build and sync static assets to src/aistudio_api/static
```

## API Usage

Supported authentication methods:
- URL parameter `?key=YOUR_API_KEY`
- Header `x-goog-api-key: YOUR_API_KEY`
- Header `x-api-key: YOUR_API_KEY`
- Header `Authorization: Bearer YOUR_API_KEY`

### Endpoints (cURL)

**List Models**:
```bash
curl http://localhost:8080/v1beta/models -H "x-goog-api-key: your-api-key"
```

**Text Generation (Non-streaming)**:
```bash
curl http://localhost:8080/v1beta/models/gemini-3.8-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'
```

**Streaming Generation (Server-Sent Events)**:
```bash
curl http://localhost:8080/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'
```

### Official Python SDK (google-genai)

Install dependency:
```bash
pip install google-genai
```

Streaming and non-streaming usage example:
```python
from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

# Streaming generation
response = client.models.generate_content_stream(
    model="gemini-3.8-flash",
    contents="Hello from Gemini"
)
for chunk in response:
    print(chunk.text, end="", flush=True)

# Non-streaming generation
response = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="Hello from Gemini"
)
print(response.text)
```

## License

MIT License
