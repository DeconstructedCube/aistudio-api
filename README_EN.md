# aistudio-api

Google AI Studio API reverse proxy. Exposes Google Gemini native API protocol.

[中文文档](./README.md)

## Features

- Gemini API protocol compatibility
- Dynamic model discovery
- Cookie-based multi-account polling
- Tools: Google Search, Maps, Code Execution, URL Context, Function Calling
- Thinking process output
- Image generation
- Chrome DevTools Protocol based headless browser session

## Installation

### Dependencies

- Python 3.10 or higher
- Chromium
- Headless browser dependencies: libnss3, libgbm1, libasound2, etc.

### Linux macOS Windows

```bash
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api
pip install -r requirements.txt
python3 main.py server --port 8080
```

### Android Termux

```bash
pkg update
pkg install -y python git glibc glibc-runner patchelf-glibc
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api
pip install -r requirements.txt
python3 scripts/bootstrap_cloakbrowser_termux.py
python3 main.py server --port 8080
```

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

## Configuration

Web interface available at `http://localhost:8080` for account and rotation management.

Environment variables:

- `AISTUDIO_PORT`: 8080
- `AISTUDIO_API_KEYS`: API keys separated by comma
- `AISTUDIO_PROXY`: HTTP or SOCKS5 proxy
- `AISTUDIO_BROWSER_EXECUTABLE`: Path to Chromium executable
- `AISTUDIO_ACCOUNT_ROTATION_MODE`: round_robin, lru, least_rl

Config file `config.yaml` controls default model behaviors.

## Usage

Authentication via `x-goog-api-key` header, `x-api-key` header, `Authorization: Bearer` or `?key=` URL parameter.

### Endpoints

List models:
```bash
curl http://localhost:8080/v1beta/models -H "x-goog-api-key: your-api-key"
```

Generate text:
```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'
```

Streaming generation requires `?alt=sse` parameter and `streamGenerateContent` endpoint.

### Python SDK

```bash
pip install google-genai
```

```python
from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="Hello"
)
print(response.text)
```

## License

MIT License
