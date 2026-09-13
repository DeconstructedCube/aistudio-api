# aistudio-api

Google AI Studio 反向代理服务。提供原生 Gemini API 接口。

[English](./README_EN.md)

## 特性

- 兼容原生 Gemini API 协议
- 动态获取可用模型列表
- 多账号 Cookie 轮询
- 支持官方工具调用，包含 Google Search、Google Maps、代码执行沙箱
- 支持 Function Calling
- 支持图像生成
- 基于 CDP 协议的轻量化无头浏览器环境

> 💡 **内存占用参考（实测）**：纯 Python 服务部分约 30~85 MB；启用内置 CloakBrowser (Chromium) 后整体常驻约 500~650 MB。在 Termux 上请确保设备剩余可用 RAM ≥ 1 GB。

## 安装部署

### 前置依赖

- Python 3.10 以上版本
- Chromium 浏览器及其运行库

### Linux macOS Windows 环境

所有平台统一使用 [`uv`](https://docs.astral.sh/uv/) 作为依赖与虚拟环境管理器（与本仓库的 `uv.lock` / `pyproject.toml` 一致）。安装 `uv` 后，从源码同步即可获得受版本锁保护的可运行虚拟环境：

```bash
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
uv sync
uv run python3 main.py server --port 8080
```

> 请务必使用 `uv sync` 安装依赖，不要使用 `pip install -r requirements.txt`，否则会导致 aarch64 预编译包版本不匹配。

### Android Termux 环境

在 Termux 上运行需要借助 `proot-distro` 提供必要的 Linux 运行环境。请按顺序执行以下命令进行完整安装与启动：
```bash
pkg update
pkg install -y python git uv proot-distro
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
uv sync
bash scripts/install_termux_prereqs.sh --project-root "$PWD"
uv run python3 main.py server --port 8080
```

> 首次运行 `install_termux_prereqs.sh` 时会自动配置一个名为 `aistudio-api` 的专属容器并下载浏览器，过程视网络情况可能需要几分钟。
> 常见问题排查（如端口冲突、容器报错等）请查阅项目 Issues 或讨论区。

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
