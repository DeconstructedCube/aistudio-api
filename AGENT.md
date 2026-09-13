# AI Studio API - 智能体协作与工程规范 (AGENT.md)

本文档面向所有维护、开发及使用本仓库的代码代理（Coding Agents）与开发者，用于提供核心架构共识、运行环境约束与严格的工程实践标准。

---

## 1. 核心架构与设计原则

`aistudio-api` 是一个纯粹的 Google AI Studio 逆向代理网关，提供原生 **Gemini API 兼容接口**（`/v1beta/...`）。

- **原生 Gemini 专一性**：本项目仅服务于 Gemini 官方 API 协议规范（包含 thinking、multimodal、image-generation、function calling 等），严禁引入任何 OpenAI / Anthropic 等多重协议转译层。
- **轻量 CDP 驱动**：使用纯 Python 异步 WebSocket 实现原生 Chrome DevTools Protocol（CDP），不依赖 Node.js / Playwright / Camoufox。
- **浏览器内 XHR Replay**：通过在 MakerSuite 页面内执行携带 `withCredentials = true` 的异步 XHR 请求，天然携带完整的 Cookie、BotGuard 快照及 Google 反爬指纹。

---

## 2. 运行时与环境约束 (Termux / PRoot)

本工程运行在移动端/轻量 Linux 容器（Termux + PRoot Ubuntu）环境中，必须遵守以下执行规范：

1. **Python 环境**：
   - 必须使用 PRoot 容器内的 Python 3.14 虚拟环境执行 Python 命令：
     ```bash
     proot-distro login ubuntu -- bash -c 'cd /data/data/com.termux/files/home/aistudio-api && source .venv/bin/activate && <COMMAND>'
     ```
   - 严禁在 Termux 宿主外部直接混用系统 pip / uv 安装平台不兼容的依赖。
2. **JavaScript / 前端工具链**：
   - 前端脚本及语法检查统一使用 **`bun`** 处理（如 `bun build`、`bun x pyright`）。

---

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
