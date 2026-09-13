# AI Studio API - 智能体协作与工程规范 (AGENT.md)

本文档面向所有维护、开发及使用本仓库的代码代理（Coding Agents）与开发者，用于提供核心架构共识、运行环境约束与严格的工程实践标准。

---

## 1. 核心架构与设计原则

`aistudio-api` 是一个纯粹的 Google AI Studio 逆向代理网关，提供原生 **Gemini API 兼容接口**（`/v1beta/...`）。

- **原生 Gemini 专一性**：本项目仅服务于 Gemini 官方 API 协议规范（包含 thinking、multimodal、image-generation、function calling 等），严禁引入任何 OpenAI / Anthropic 等多重协议转译层。
- **轻量 CDP 驱动**：使用纯 Python 异步 WebSocket 实现原生 Chrome DevTools Protocol（CDP），不依赖 Node.js / Playwright / Camoufox。
- **浏览器内 XHR Replay**：通过在 MakerSuite 页面内执行携带 `withCredentials = true` 的异步 XHR 请求，天然携带完整的 Cookie、BotGuard 快照及 Google 反爬指纹。

---

## 2. 运行时与环境约束 (Termux)

Python 与 Go 风格的工具链运行在 Termux 宿主（`uv` 管理虚拟环境）；Chromium（CloakBrowser）由于 glibc/bionic ABI 不兼容，必须跑在 `proot-distro` Linux 容器里。**严禁引入 Playwright（Puppeteer/Node 链路在 Android 上不可用）**，CDP 通过仓库自带的 `cdp_client.py` 直连。

1. **系统级准备（仅 Termux 宿主执行一次）**：
   ```bash
   pkg update
   pkg install -y python git uv proot-distro
   ```
   - `uv` 负责解析 `uv.lock` 中 termux-user-repository 镜像索引；不可改用 `pip`。
   - `proot-distro` 用来拉取 Ubuntu rootfs；安装器默认容器名为 `aistudio-api`（**不会触碰用户已有的 `ubuntu` / `debian` 等容器**），可用 `--proot-name` 改名。
2. **仓库依赖与浏览器运行时（克隆后执行一次；后续升级版本再跑）**：
   ```bash
   git clone https://github.com/DeconstructedCube/aistudio-api.git
   cd aistudio-api
   uv sync                                              # 读 uv.lock，构建 .venv
   bash scripts/install_termux_prereqs.sh --project-root "$PWD"
   uv run python3 main.py server --port 8080
   ```
   - 安装器幂等：proot 容器、apt 依赖、CloakBrowser 二进制均按"已就绪则跳过"逻辑短路；可重复运行。
   - 不要用 `pip install -r requirements.txt`；会绕过 `pydantic-core==2.41.5` 的锁和 termux-user-repository 镜像索引，破坏 aarch64 二进制 ABI。
   - `scripts/cloakbrowser_termux/run-chrome.sh` 是唯一被引擎接受的启动入口；它只做 `proot-distro login aistudio-api -- /opt/cloakbrowser/chrome "$@"` 加必要的 `--bind`。不要在仓库里新增其它 Chromium 启动器（会破坏多账号的进程隔离约定）。
3. **JavaScript / 前端工具链**：前端脚本及语法检查统一使用 `bun` 处理（如 `bun build`、`bun x pyright`）。
4. **常见故障与诊断**：
   - `[Errno 98] address already in use`：上一次 `main.py server` 未退出。`pkill -f 'main.py server'` 后重试，或临时切换 `--port`。
   - `Chromium CDP endpoint on port N not ready after 15.0s`：proot 容器没起来或没装 apt 依赖。重跑 `bash scripts/install_termux_prereqs.sh --project-root "$PWD"`。
   - `proot-distro login: container 'aistudio-api' is missing`：要么重跑安装器，要么 `AISTUDIO_PROOT_NAME=...` 显式指定容器名。
   - `pydantic-core` 报 `undefined symbol` / `version 'GLIBC_X.Y' not found`：违反 §2，用了 `pip` 而非 `uv`。删掉 `.venv/` 后 `uv sync` 重建。
   - Chromium 启动后 BotGuard 仍被拦截：通常是 BotGuard 上下文里检测到自动化痕迹（headless 标签、User-Agent）。确认是用 `bash scripts/install_termux_prereqs.sh` 装的完整 CloakBrowser，不是仓库外的零散 chromium。

## 3. 严格类型与代码质量规范

为杜绝类型松散与代码腐化，本项目开启了严格的静态类型检查：

- **拒绝 `Any` 滥用**：
  - 严禁在任何公共接口、数据模型及数据处理流程中使用无约束的 `Any`。
  - 对于通用未知数据，必须使用 `object` 并配合显式的 `isinstance` / `str()` / `int()` 类型收窄（Type Narrowing）。
  - 结构化数据使用具名数据类（如 `NormalizedGeminiRequest`、`AccountMeta` 等）或具体泛型（如 `dict[str, object]`、`list[object]`）。
- **静态检查工具链验证**：
  - 提交任何改动前，必须执行并通过以下三项检查：
    1. **Pyright / Pylance**：`bun x pyright src`（要求 **0 errors**）。
    2. **MyPy**：`mypy src`（要求 **0 issues**）。
    3. **Pytest**：`pytest -v`（要求 **全部通过**）。

---

## 4. 多账号隔离与并发安全守则

1. **原子化文件写入**：
   - 所有涉及持久化配置（`registry.json`、`auth.json`、`meta.json` 等）的操作必须使用 `account_store._atomic_write_json`（写临时文件后 `os.replace` 原子替换），严禁裸写 `write_text()` 造成并发破坏。
2. **账号切换请求排干 (Drain-First)**：
   - 切换账号时严禁直接强杀 Chromium 进程。必须通过 `BrowserSession.request_scope()` 追踪在途请求，等待进行中的流式长连接安全结束后再执行上下文清理。
3. **缓存与模板隔离**：
   - 账号切换或 429 限流重试时，必须同步调用 `clear_snapshot_cache()` 与 `capture_service.clear_templates()`，严禁不同账号复用 BotGuard 快照或请求模板。
4. **无全局 DOM 污染**：
   - 浏览器上下文内的 JavaScript 交互严禁在 `window` 对象上挂载共享状态（如禁止使用 `window.__sr` 轮询求值），必须使用纯局部闭包 `Promise` 直接返回。

---

## 5. 核心代码结构索引

```text
src/aistudio_api/
├── api/                    # HTTP 接入层 (FastAPI)
│   ├── app.py              # 应用生命周期与路由挂载
│   ├── dependencies.py     # 鉴权依赖与单例注入
│   ├── routes_gemini.py    # /v1beta/models/*:generateContent 路由
│   ├── routes_models.py    # /v1beta/models 动态模型发现路由
│   ├── routes_accounts.py  # 账号注册、探活与切换路由
│   └── routes_system.py    # 监控指标与轮询策略路由
├── application/            # 业务编排层
│   ├── account_rotator.py  # 账号多策略轮询器 (Round-Robin, LRU, Least-RL)
│   ├── account_service.py  # 账号存储与上下文切换协调
│   ├── api_service_gemini.py# Gemini 请求全生命周期与流式管理
│   └── chat_service.py     # 消息规范化与 Protobuf 数组编排
├── domain/                 # 领域模型与异常
│   ├── errors.py           # 统一异常体系 (AuthError, UsageLimitExceeded 等)
│   └── models.py           # 响应 Candidate 解析器与流式 Chunk 分类器
├── infrastructure/         # 基础设施层
│   ├── account/            # Cookie 多格式解析与 SAPISIDHASH 计算
│   ├── browser/            # 原生 Python 异步 CDP 客户端与 Chromium 进程管理
│   ├── cache/              # 内存快照与元数据缓存
│   └── gateway/            # 核心逆向编解码器 (wire_codec) 与流式网关
└── static/                 # 现代化轻量 WebUI 控制台 (Alpine.js)
```
