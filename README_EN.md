# aistudio-api

Google AI Studio API reverse proxy providing native Google Gemini API interfaces.

[中文文档](./README.md)

---

## Features

- **Protocol Compatibility**: Full compatibility with official Gemini API specifications (`/v1beta/...`), including Thinking process, Multimodal input, Function Calling, and Image Generation.
- **Dynamic Model Discovery**: Automatically synchronizes and discovers available models from upstream Google AI Studio.
- **Account Scheduling & Failover**: Multi-account sticky rotation with per-model 429 quota isolation and automatic failover.
- **Multi-Account Probing**: Automatically probes and extracts multi-login sub-accounts (`u/0`, `u/1`...) from a single session Cookie.
- **Built-in Tools**: Supports Google Search, Google Maps, and Code Execution Sandbox.
- **Web Management Console**: Lightweight dashboard for account management, real-time metrics, and live `config.yaml` editing with hot reloading.
- **Native Lightweight CDP**: Direct Chrome DevTools Protocol communication via asynchronous pure-Python WebSockets, without Playwright, Puppeteer, or Node.js.

> **Memory Footprint**: Python service runs at ~30 - 85 MB RAM; with managed Chromium active, total memory stays around 500 - 650 MB. On Android Termux, ≥ 1 GB free RAM is recommended.

---

## Technical Specifications

- [System Architecture Specification (ARCHITECTURE.md)](./docs/ARCHITECTURE.md): Layered architecture, request lifecycle, wire codec, and concurrency control.
- [BotGuard Verification Chain (BOTGUARD_VERIFICATION_CHAIN.md)](./docs/BOTGUARD_VERIFICATION_CHAIN.md): WAA challenge handshake, Wasm dynamic signature, content hashing, and upstream validation pipeline.

---

## Installation & Quick Start

### Prerequisites

- Python 3.10+
- Chromium / Google Chrome browser installed on host

### Linux / macOS / Windows

Dependencies and lockfiles are managed using [`uv`](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
uv sync
uv run python3 main.py server --port 8080
```

> **Note**: Always use `uv sync` rather than `pip install` to ensure prebuilt platform wheels are correctly resolved.

### Android Termux

On Termux, Chromium runs inside a lightweight `proot-distro` Linux container. Run the following to set up and start:

```bash
pkg update
pkg install -y python git uv proot-distro
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
uv sync
bash scripts/install_termux_prereqs.sh --project-root "$PWD"
uv run python3 main.py server --port 8080
```

> The initial run of `install_termux_prereqs.sh` will set up the container and download the browser runtime automatically.

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

---

## Configuration

Access `http://localhost:8080` once the service is running:

- **Dashboard**: Overview of service health, per-model request stats, rate-limit indicators, and integration code snippets.
- **Accounts**: Import Cookie credentials, view sub-account statuses, and manually activate or reset quotas.
- **Settings**: View and edit `config.yaml` rules with instant hot reload on save.
- **Authentication**: Set `AISTUDIO_WEB_PASSWORD` to enable login authentication for the Web Console.

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `AISTUDIO_PORT` | Service port | `8080` |
| `AISTUDIO_WEB_PASSWORD` | Web Console login password (also supports `AISTUDIO_ADMIN_PASSWORD`) | None (auth disabled) |
| `AISTUDIO_PROXY` | HTTP / SOCKS5 outbound proxy | None |
| `AISTUDIO_BROWSER_EXECUTABLE` | Path to Chromium executable | Auto-detected |
| `AISTUDIO_SNAPSHOT_CACHE_TTL` | BotGuard snapshot cache TTL in seconds | `3600` |

---

## API Usage

Supported authentication headers/parameters:
- Query param: `?key=YOUR_API_KEY`
- Headers: `x-goog-api-key: YOUR_API_KEY`, `x-api-key: YOUR_API_KEY`, or `Authorization: Bearer YOUR_API_KEY`

### cURL

**List Models**:
```bash
curl http://localhost:8080/v1beta/models -H "x-goog-api-key: your-api-key"
```

**Generate Content**:
```bash
curl http://localhost:8080/v1beta/models/gemini-3.8-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'
```

**Streaming (SSE)**:
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

# Stream
response = client.models.generate_content_stream(
    model="gemini-3.8-flash",
    contents="Hello from Gemini"
)
for chunk in response:
    print(chunk.text, end="", flush=True)

# Non-stream
response = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="Hello from Gemini"
)
print(response.text)
```

---

## Frontend Development

The Web Console lives in `web/` (built with Vite + Vue 3 + TypeScript):

```bash
cd web
bun install          # Install dependencies
bun run dev          # Start local Vite dev server (localhost:3000 -> 8080)
bun run type-check   # Type-check TypeScript
bun run lint         # Lint code
bun run build        # Build production bundle into src/aistudio_api/static
```

---

## License

MIT License
