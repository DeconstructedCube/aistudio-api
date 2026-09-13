# aistudio-api

Google AI Studio 反向代理服务。提供原生 Gemini API 接口。

[English](./README_EN.md)

## 特性

- 兼容原生 Gemini API 协议
- 动态获取可用模型列表
- 多账号 Cookie 轮询
- 支持官方工具调用，包含 Google Search、Google Maps、代码执行沙箱
- 支持 Function Calling
- 支持输出思维链
- 支持图像生成
- 基于 CDP 协议的轻量化无头浏览器环境

## 安装部署

### 前置依赖

- Python 3.10 以上版本
- Chromium 浏览器及其运行库

### Linux macOS Windows 环境

```bash
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api
pip install -r requirements.txt
python3 main.py server --port 8080
```

### Android Termux 环境

```bash
pkg update
pkg install -y python git glibc glibc-runner patchelf-glibc
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api
pip install -r requirements.txt
python3 scripts/bootstrap_cloakbrowser_termux.py
python3 main.py server --port 8080
```

### Docker 环境

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/chrysoljq/aistudio-api:latest
```

通过 docker compose 启动：

```bash
docker compose up -d
```

## 配置管理

服务启动后访问 `http://localhost:8080` 进入 Web 控制面板管理账号 Cookie 与轮询策略。

主要环境变量：

- `AISTUDIO_PORT`: 服务监听端口，默认 8080
- `AISTUDIO_API_KEYS`: 鉴权密钥，多个密钥用逗号分隔
- `AISTUDIO_PROXY`: HTTP 或 SOCKS5 代理地址
- `AISTUDIO_BROWSER_EXECUTABLE`: Chromium 可执行文件绝对路径
- `AISTUDIO_ACCOUNT_ROTATION_MODE`: 轮询模式，支持 round_robin、lru、least_rl

模型默认行为由根目录 `config.yaml` 控制。

## 接口调用

支持的鉴权方式：
- URL 参数 `?key=YOUR_API_KEY`
- 请求头 `x-goog-api-key: YOUR_API_KEY`
- 请求头 `x-api-key: YOUR_API_KEY`
- 请求头 `Authorization: Bearer YOUR_API_KEY`

### 基础路由

获取模型列表：
```bash
curl http://localhost:8080/v1beta/models -H "x-goog-api-key: your-api-key"
```

文本生成：
```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'
```

流式输出需添加 `?alt=sse` 参数并调用 `streamGenerateContent` 路由。

### Python SDK

安装依赖：
```bash
pip install google-genai
```

调用示例：
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

## 许可证

MIT License
