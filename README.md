# aistudio-api

Google AI Studio 反向代理服务。提供原生 Gemini API 接口。

[English](./README_EN.md)

## 特性

- 兼容原生 Gemini API 协议规范（包含 Thinking、Multimodal、Function Calling 与图像生成）
- 动态获取可用模型列表（与 Google 官方同步）
- 多 Google 账号 Cookie 智能轮询调度与按模型独立冷却
- 支持单份 Cookie 自动无限向下探活多登录账号 (`u/0`, `u/1`...) 一键批量导入
- 支持官方工具调用（Google Search、Google Maps、代码执行沙箱等）
- 现代化 Web 管理控制台（账号管理、实时统计看板、在线规则热重载与独立鉴权）
- 基于纯 Python 异步 CDP 驱动的轻量化无头浏览器环境

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

## Web 控制面板与配置

服务启动后访问 `http://localhost:8080` 进入 Web 管理控制台：

- **控制面板**：实时查看各模型独立请求量、成功数、429 频率限制与最后调用时间，提供快速集成代码示例。
- **账号管理**：支持多账号 Cookie 导入与格式解析，支持单份 Cookie 无限向下探活多登录账号并自动分化建档；支持观测每个账号按模型的独立冷却状态与手动激活切换。
- **轮询调度策略**：支持四种轮询策略调度：
  - `sticky`（保持固定）：优先使用当前激活账号，直到遇到 429 限流才自动轮换（默认推荐）。
  - `round_robin`（顺序轮询）：按账号池顺序依次分发，自动跳过处于冷却期的账号。
  - `lru`（最近最少使用）：优先调用空闲时间最长的账号，均衡各账号负载。
  - `least_rl`（最小限流优先）：优先调用限流次数最少的健康账号，最大化服务稳定性。
- **模型规则配置**：在线查看与编辑 `config.yaml`，保存后自动完成热重载，无需重启服务即可调整默认工具与安全过滤等级。
- **安全鉴权**：提供独立的登录验证页面（`/login`）与路由守卫，在服务端设置 `AISTUDIO_WEB_PASSWORD` 环境变量时自动对未授权访问进行拦截；API 客户端访问密钥可在 `config.yaml` 或 Web 界面中集中分配与管理。

### 环境变量说明

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `AISTUDIO_PORT` | 服务监听端口 | `8080` |
| `AISTUDIO_WEB_PASSWORD` | 网页管理控制台登录密码 (亦支持 `AISTUDIO_ADMIN_PASSWORD`) | 空（免密直接进入） |
| `AISTUDIO_PROXY` | HTTP / SOCKS5 出口代理地址 | 空（直连） |
| `AISTUDIO_BROWSER_EXECUTABLE` | Chromium 浏览器可执行文件绝对路径 | 自动探测 / 默认路径 |
| `AISTUDIO_ACCOUNT_ROTATION_MODE` | 账号轮询模式 (`sticky`, `round_robin`, `lru`, `least_rl`) | `sticky` |
| `AISTUDIO_ACCOUNT_COOLDOWN_SECONDS` | 账号 429 限流后的默认冷却秒数 | `60` |
| `AISTUDIO_MAX_CONCURRENCY` | 浏览器并发请求信号量上限 | `3` |
| `AISTUDIO_SNAPSHOT_CACHE_TTL` | BotGuard 快照缓存有效期（秒） | `3600` |

模型默认参数与工具规则由根目录 `config.yaml` 定义。

### 前端开发与构建

Web 控制台源码位于 `web/` 目录（基于 Vite + Vue 3 + TypeScript 构建），如需进行前端二次开发：

```bash
cd web
bun install          # 安装依赖
bun run dev          # 启动开发服务器 (端口 3000，自动反代 8080 API)
bun run type-check   # 执行 TypeScript 类型检查
bun run lint         # 执行 ESLint 代码质量检查
bun run build        # 生产构建并同步产物至 src/aistudio_api/static
```

## 接口调用

支持的鉴权方式：
- URL 参数 `?key=YOUR_API_KEY`
- 请求头 `x-goog-api-key: YOUR_API_KEY`
- 请求头 `x-api-key: YOUR_API_KEY`
- 请求头 `Authorization: Bearer YOUR_API_KEY`

### 基础路由 (cURL)

**获取模型列表**：
```bash
curl http://localhost:8080/v1beta/models -H "x-goog-api-key: your-api-key"
```

**文本生成 (非流式)**：
```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'
```

**流式生成 (Server-Sent Events)**：
```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'
```

### 官方 Python SDK (google-genai)

安装依赖：
```bash
pip install google-genai
```

流式与非流式调用示例：
```python
from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

# 流式输出
response = client.models.generate_content_stream(
    model="gemini-3.7-flash",
    contents="Hello from Gemini"
)
for chunk in response:
    print(chunk.text, end="", flush=True)
```

## 许可证

MIT License
