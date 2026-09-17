# 智能体协作与开发规范 (AGENT.md)

本文档面向所有参与本项目的代码代理（Coding Agents）与开发者，明确核心架构共识、运行环境约束与代码规范。

---

## 目录

- [1. 核心架构与设计原则](#1-核心架构与设计原则)
- [2. 运行时与环境约束 (Termux)](#2-运行时与环境约束-termux)
- [3. 代码质量与类型规范](#3-代码质量与类型规范)
- [4. 并发安全与多账号守则](#4-并发安全与多账号守则)
- [5. 代码结构索引](#5-代码结构索引)

---

## 1. 核心架构与设计原则

`aistudio-api` 是 Google AI Studio 反向代理网关，提供原生 **Gemini API 兼容接口**（`/v1beta/...`）。

- **原生 Gemini 协议专一性**：服务于 Gemini 官方 API 规范（包含 thinking、multimodal、image-generation、function calling 等），不引入额外的跨厂商协议转译层。
- **轻量 CDP 驱动**：使用纯 Python 异步 WebSocket 直连 Chrome DevTools Protocol（CDP），不依赖 Node.js、Playwright 或 Selenium。
- **浏览器内 XHR Replay**：在 MakerSuite 页面上下文中执行携带 `withCredentials = true` 的异步 XHR 请求，天然复用完整的 Cookie 会话、BotGuard 快照与环境指纹。

---

## 2. 运行时与环境约束 (Termux)

Python 工具链运行在 Termux 宿主（通过 `uv` 管理虚拟环境）；Chromium（CloakBrowser）由于 glibc/bionic ABI 差异，运行在 `proot-distro` Linux 容器中。CDP 通过内置的 `cdp_client.py` 经由 WebSocket 直连。

### 2.1 环境准备与启动流程

```bash
# 1. 宿主依赖环境配置与同步
bash scripts/setup-env.sh
uv sync

# 2. 准备受控浏览器运行时（仅首次执行）
bash scripts/setup-browser.sh

# 3. 启动服务
uv run python3 main.py server --port 8080
```

> [!NOTE]
> - `uv` 会解析 `uv.lock` 中的跨平台镜像索引与预编译 wheel，避免在 Termux 环境因编译 Rust 消耗过多内存导致 OOM。
> - `scripts/cloakbrowser_termux/run-chrome.sh` 是 Termux 下的 Chromium 包装入口，内部调用 `proot-distro login aistudio-api -- /opt/cloakbrowser/chrome "$@"` 并自动绑定路径。

### 2.2 前端构建与管理

- 前端位于 `web/` 目录，基于 Vite + Vue 3 + TypeScript + Tailwind CSS。
- 依赖管理与构建使用 `bun`（`bun run dev`、`bun run build`、`bun run lint`、`bun run type-check`）。
- 生产构建产物直接输出至 `src/aistudio_api/static/`，由 FastAPI 静态托管。

### 2.3 常见排错

| 现象 | 原因 | 处理方案 |
|---|---|---|
| `[Errno 98] address already in use` | 端口被占用或上一次服务未完全退出 | 执行 `pkill -f 'main.py server'` 后重试，或指定 `--port <other_port>` |
| `Chromium CDP endpoint on port N not ready` | proot 容器未启动或缺少 apt 依赖 | 重新运行 `bash scripts/setup-browser.sh` |
| `proot-distro login: container 'aistudio-api' is missing` | 容器尚未创建或被改名 | 运行安装脚本创建容器，或通过 `AISTUDIO_PROOT_NAME` 指定现有容器 |
| `pydantic-core` 报 `GLIBC_X.Y not found` | 使用了系统 pip 安装而非 `uv` | 清理 `.venv/` 后重新执行 `uv sync` |

---

## 3. 代码质量与类型规范

本项目启用了严格的静态类型检查与代码质量工具链：

### 3.1 类型安全要求

- **避免 `Any` 滥用**：在公共接口、领域模型与核心流程中避免无约束的 `Any`。
- **未知数据收窄**：对于动态解析的数据，使用 `object` 配合 `isinstance` 进行类型收窄。
- **结构化定义**：数据结构使用具名 dataclass（如 `NormalizedGeminiRequest`、`AccountMeta`）或明确泛型类型标注（如 `dict[str, object]`）。

### 3.2 Ruff 代码风格与规范配置

项目采用 Ruff 作为统一的 Python 代码风格与 Lint 工具，并在 `pyproject.toml` 中开启了严格的规则检查：

- **激活规则集**：
  - `E` / `W` (pycodestyle 错误与警告)
  - `F` (Pyflakes 代码逻辑错误)
  - `I` (isort 自动 import 排序)
  - `B` (flake8-bugbear 常见陷阱与 Bug 预防)
  - `C4` (flake8-comprehensions 列表推导式优化)
  - `UP` (pyupgrade Python 3.11+ 语法升级)
  - `SIM` (flake8-simplify 代码精简，强制 `contextlib.suppress` 替代 `try-except-pass`)
  - `PTH` (flake8-use-pathlib 规范使用 Pathlib)
  - `RUF` (Ruff 专属严格检查)
  - `ASYNC` (flake8-async 异步编程反模式检查，如 `asyncio.Event` 替代轮询睡眠)
  - `PIE` (flake8-pie 冗余代码消除)
  - `Q` / `RSE` / `RET` (引号、raise 括号与返回值规范)
  - `A` (内建函数命名防覆盖)
  - `YTT` (flake8-2020 版本兼容性检查)
- **全局豁免（均有明确理由）**：
  - `B008`：FastAPI `Depends()` 注入参数的标准模式
  - `RUF001` / `RUF002` / `RUF003`：注释与文档字符串中的中文全角标点（项目文档语言为中文）
  - `ASYNC109` / `ASYNC230` / `ASYNC240`：异步函数中的超时参数与本地文件访问（单机自托管场景）
  - `E501`：行宽由 formatter 统一处理
- **按文件豁免**：`main.py` 豁免 `E402`（启动时需先注入 `sys.path`）；`tests/**` 豁免动态殊性规则
- **检查与格式化指令**：
  ```bash
  # 代码风格与 Lint 检查
  ruff check .
  # 自动格式化与 import 排序修复
  ruff check . --fix
  ```
### 3.3 自动化检查命令与执行守则

在提交代码改动前，必须确保以下工具链检查通过：

| 工具 / 阶段 | 命令 | 判定标准 |
|---|---|---|
| **Python 代码风格与 Lint** | `ruff check .` | 0 errors |
| **Python 类型检查** | `bun x pyright` | 0 errors |
| **Python 单元测试** | `uv run pytest` | 全部通过 |
| **前端代码规范** | `cd web && bun run lint` | 0 errors, 0 warnings |
| **前端类型检查** | `cd web && bun run type-check` | 0 errors |
| **前端生产构建** | `cd web && bun run build` | 构建成功并更新 static 产物 |

> [!NOTE]
> **跨平台原生二进制依赖与开发工具**：
> - `pydantic-core` 等生产核心依赖通过 `uv.lock` 显式注入 TUR 的 prebuilt Android wheel，配合 `setup-env.sh` 生成的项目级本地 `uv.toml`，实现全平台统一通过 `uv sync` 秒级安装且不触发源码构建。
> - `ruff` 作为开发阶段的 Lint 工具，在 `pyproject.toml` 的 dev 依赖中配置了平台标记 `ruff>=0.8.0; sys_platform != 'android'`；桌面平台（Linux / macOS / Windows）执行 `uv sync --extra dev` 时直接自 PyPI 下载预编译 wheel。而在 Android Termux 环境下，TUR 并未打包 ruff 的 PyPI wheel，开发者可通过 Termux 原生包管理器 `pkg install -y ruff` 直接获得编译好的 aarch64 native 二进制，`setup-env.sh` / `setup-browser.sh` 会自动建立 `.venv/bin/ruff` 软链以便在虚拟环境中直接调用，普通用户生产运行无需安装。

> [!TIP]
> **避免无效重跑**：若在当前交互轮次中未发生代码或配置文件的实质性变动，无需重复执行全量类型与测试套件检查。
---

## 4. 并发安全与多账号守则

> [!IMPORTANT]
> 1. **原子化文件持久化**：所有持久化文件（`registry.json`、`auth.json`、`meta.json` 等）操作必须使用 `account_store._atomic_write_json`（写入临时文件后通过 `os.replace` 原子替换），防止并发写入截断。
> 2. **账号切换排干机制**：切换账号时通过 `BrowserSession.request_scope()` 追踪在途请求，等待进行中的流式请求完成后再清理上下文。
> 3. **缓存与模板隔离**：账号切换或 429 限流重试时，调用 `clear_snapshot_cache()` 与 `capture_service.clear_templates()`，避免跨账号复用 BotGuard 快照或请求模板。
> 4. **无全局 DOM 污染**：页面内 JavaScript 交互使用局部闭包 `Promise` 返回数据，不在 `window` 对象上遗留全局共享状态。

---

## 5. 代码结构索引

```text
aistudio-api/
├── web/                           # Web 管理控制台 (Vite + Vue 3 + TS + Tailwind)
│   ├── src/
│   │   ├── api/                   # REST 请求封装 (auth, accounts, system, models)
│   │   ├── types/                 # TypeScript 类型契约
│   │   ├── stores/                # Pinia 状态管理
│   │   ├── components/            # UI 与业务解耦组件
│   │   ├── views/                 # 页面路由视图 (Dashboard, Accounts, Settings, Login)
│   │   └── router/                # Vue Router 路由与守卫
│   └── vite.config.ts             # 构建配置 (输出至 src/aistudio_api/static)
├── src/aistudio_api/
│   ├── api/                       # HTTP 接口层 (FastAPI)
│   │   ├── app.py                 # 应用生命周期与静态托管
│   │   ├── dependencies.py        # API Key 与 Token 鉴权注入
│   │   ├── routes_gemini.py       # /v1beta/models/*:generateContent 路由
│   │   ├── routes_models.py       # /v1beta/models 模型发现路由
│   │   ├── routes_accounts.py     # 账号管理与探活路由
│   │   ├── routes_system.py       # 监控指标与 config.yaml 热重载路由
│   │   └── response_models.py     # API Pydantic 响应模型
│   ├── application/               # 业务应用层
│   │   ├── account_rotator.py     # 黏性账号调度器 (按模型记录 429 与太平洋午夜重置)
│   │   ├── account_service.py     # 账号存储与激活上下文协调
│   │   ├── api_service_gemini.py  # Gemini 请求生命周期与流式处理
│   │   ├── api_service_common.py  # _switch_lock 防雪崩锁与统计汇聚
│   │   ├── api_service.py         # 应用层对外统一导出
│   │   └── chat_service.py        # 消息规范化与多模态数据处理
│   ├── domain/                    # 领域模型与异常体系
│   │   ├── errors.py              # 业务异常定义 (AuthError, UsageLimitExceeded 等)
│   │   └── models.py              # 响应 Candidate 与 Chunk 内部模型
│   ├── infrastructure/            # 基础设施层
│   │   ├── account/               # Cookie 解析与凭据持久化 (account_store)
│   │   ├── browser/               # 异步 CDP 客户端与 Chromium 进程管理
│   │   ├── cache/                 # 内存快照与元数据缓存
│   │   └── gateway/               # Wire Codec (Protobuf-JSON) 与流式网关
│   └── static/                    # 前端构建产物目录
├── config.yaml                    # 模型规则与工具默认行为配置 (支持在线热重载)
└── main.py                        # 本地统一启动入口
```
