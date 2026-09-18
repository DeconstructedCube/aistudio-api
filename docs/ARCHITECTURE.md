# aistudio-api 系统架构设计

本文档说明 `aistudio-api` 反向代理服务的整体系统架构、各模块职责分工、数据流转管线以及并发调度与反爬绕过机制。

---

## 目录

- [1. 整体架构与分层设计](#1-整体架构与分层设计)
- [2. 核心模块与职责划分](#2-核心模块与职责划分)
- [3. 请求生命周期与执行时序](#3-请求生命周期与执行时序)
- [4. 并发控制与高可用设计](#4-并发控制与高可用设计)
- [5. 跨平台支持与依赖隔离](#5-跨平台支持与依赖隔离)

---

## 1. 整体架构与分层设计

系统采用分层设计（Layered Architecture），划分为 **API 接入层**、**Application 应用服务层**、**Domain 领域层** 与 **Infrastructure 基础设施层**：

```mermaid
flowchart TD
    Client["客户端调用方<br/>(cURL / Python SDK / Web 控制台)"]

    subgraph APILayer ["1. API 接入与路由层 (FastAPI)"]
        RoutesGemini["routes_gemini.py<br/>Gemini v1beta 兼容接口"]
        RoutesModels["routes_models.py<br/>模型发现与能力查询"]
        RoutesAccounts["routes_accounts.py<br/>账号与 Cookie 管理"]
        RoutesSystem["routes_system.py<br/>系统监控与配置热重载"]
    end

    subgraph AppLayer ["2. Application 应用服务层"]
        APISvc["api_service_gemini.py<br/>请求生命周期与流式处理"]
        Orchestrator["account_orchestrator.py<br/>_switch_lock 防雪崩切号编排"]
        ChatSvc["chat_service.py<br/>多模态请求规范化"]
        AccountRotator["account_rotator.py<br/>模型级 Sticky 调度与 429 隔离"]
        AccountSvc["account_service.py<br/>账号激活与存储用例封装"]
    end

    subgraph InfraLayer ["3. Infrastructure 基础设施层"]
        subgraph GatewaySub ["协议转换与网关"]
            CaptureSvc["capture.py<br/>单例模板缓存与捕获"]
            WireCodec["wire_codec.py<br/>Protobuf-JSON 请求构造"]
            WireParser["wire_parser.py<br/>Protobuf-JSON 响应解析"]
            StreamingGateway["streaming.py<br/>增量 SSE 流式管道"]
            Transport["transport.py<br/>CDP Native Binding 推送传输"]
        end
        subgraph BrowserSub ["浏览器与 CDP 通信"]
            BrowserSession["session.py<br/>单实例会话与锁管理"]
            CDPClient["cdp_client.py<br/>纯 Python 异步 WebSocket CDP 客户端"]
            BrowserEngine["browser_engine.py<br/>跨平台 Chromium 探测与进程管理"]
        end

        subgraph StorageSub ["持久化与缓存"]
            AccountStore["account_store.py<br/>原子 JSON 文件凭据库"]
            SnapshotCache["snapshot_cache.py<br/>内存快照与元数据缓存"]
        end
    end

    subgraph Upstream ["4. Google 上游服务"]
        AIStudio["Google AI Studio<br/>alkalimakersuite-pa"]
        Waa["Google WAA 反作弊网关<br/>waa-pa"]
    end

    Client --> APILayer
    APILayer --> AppLayer
    AppLayer --> InfraLayer
    BrowserSub --> Upstream
    GatewaySub --> Upstream
```

---

## 2. 核心模块与职责划分

### 2.1 API 接入层 (`src/aistudio_api/api/`)

| 文件 | 核心职责 |
|---|---|
| `routes_gemini.py` | 对外暴露 `/v1beta/models/{model}:generateContent` 和 `:streamGenerateContent` 端点 |
| `routes_models.py` | 动态向上游拉取并缓存可用模型列表（如 `gemini-3.7-flash`、`gemini-3.8-flash` 等） |
| `routes_accounts.py` | 提供 Cookie 导入、多账号递归探活（`u/0`, `u/1`...）、手动激活与删除接口 |
| `routes_system.py` | 监控指标查询、在线编辑与热重载 `config.yaml` 规则 |
| `dependencies.py` | 统一 API Key 鉴权拦截（支持 Query 参数 `?key=`、Header `x-goog-api-key`、`x-api-key` 或 `Bearer`） |

### 2.2 应用服务层 (`src/aistudio_api/application/`)

| 文件 | 核心职责 |
|---|---|
| `chat_service.py` | 将客户端提交的 Gemini 标准请求转换为内部通用结构，内存处理 Base64 媒体、系统提示与工具参数 |
| `account_rotator.py` | 负责多账号的 Sticky 黏性调度，为每个账号按模型维护独立的 429 限流状态，每日美西午夜重置 |
| `account_orchestrator.py` | 提供全局防雪崩互斥锁（`_switch_lock`），在并发 429 时实现安全有序故障转移切号，避免级联风暴 |
| `account_service.py` | 账号领域用例（Cookie 保存、批量探活导入、凭据激活）封装，杜绝路由层穿透访问存储私有属性 |
| `api_service_gemini.py` | 编排请求全生命周期，协调捕获模板、签名、传输重放及 SSE 流式响应 |

### 2.3 基础设施层 (`src/aistudio_api/infrastructure/`)

- **浏览器与 CDP 子系统 (`browser/`)**：
  - `cdp_client.py`：基于纯 Python 异步 WebSocket 的 Chrome DevTools Protocol 客户端，支持网络层黑名单拦截与自动清理监听器。
  - `browser_engine.py`：负责 Chromium 跨平台路径探测，启用 `--max-old-space-size=128 --expose-gc` 进行严格内存压降。
  - `scripts.py`：浏览器端注入脚本，包含 Fetch + ReadableStream 分块推送、DOM GC 与停止生成控制。
- **网关与编解码子系统 (`gateway/`)**：
  - `transport.py`：CDP 原生 Binding 实时流式事件推送，辅以有界异步队列反压控制与 Python 端 SAPISIDHASH 鉴权注入。
  - `wire_codec.py`：负责 Google 内部 Protobuf-over-JSON 数组结构构造与请求重写。
  - `wire_parser.py`：从领域模型剥离出的纯粹 Protobuf-over-JSON 响应解析器，将上游分块与使用量转换为领域对象。
  - `capture.py`：集中统一的请求模板单例缓存管理（Single Source of Truth）。
- **凭据与存储子系统 (`account/`)**：
  - `account_store.py`：基于文件系统的原子持久化凭据库（紧凑 JSON 格式，避免无效磁盘刷写）。
---

## 3. 请求生命周期与执行时序

从客户端请求到获取流式响应的端到端调用时序：

```mermaid
sequenceDiagram
    autonumber
    actor Client as 客户端调用方
    participant API as FastAPI 路由层
    participant Rotator as AccountRotator
    participant Session as BrowserSession
    participant Codec as WireCodec
    participant Page as Chromium (CDP)
    participant Google as Google AI Studio

    Client->>API: POST /v1beta/models/gemini-3.7-flash:streamGenerateContent
    API->>Rotator: 获取目标模型可用账号 (Sticky 检查)
    Rotator-->>API: 返回当前可用账号 (如 u/0)
    API->>Session: 进入 request_scope (追踪在途活跃请求)

    opt 首次请求该模型
        Session->>Page: 捕获该模型 GenerateContent 模板
        Page-->>Session: 提取 URL、Headers 与结构基础
    end

    Session->>Page: 计算内容哈希并请求 BotGuard 快照
    Page-->>Session: 返回 !dXaldhL... (Wasm 签名)
    Session->>Codec: 组装修改后的请求体 (注入 Prompt + 快照)
    Codec-->>Session: 生成最终 Wire Payload

    Session->>Page: 在页面上下文发起原生 XHR (withCredentials=true)
    Page->>Google: 发送携带当前 Cookie 与 X-Goog-AuthUser 的 POST 请求
    Google-->>Page: 分块推送数据流
    Page-->>Session: CDP Runtime 事件推送 Chunk
    Session->>API: 解析 EventStream (提取 thinking / text / tool_calls)
    API-->>Client: SSE 流式响应 (data: {"candidates": ...})
```

---

## 4. 并发控制与高可用设计

### 4.1 单进程受控 Chromium 架构

- 全局维持 **单个受控 Chromium 进程**，避免多实例多进程导致的内存膨胀。
- 启动限制参数：`--renderer-process-limit=1`、`--js-flags=--max-old-space-size=128`、`--disable-gpu`。
- 并发请求通过 CDP 客户端在同一个浏览器页面上下文内以多路复用 XHR（`XMLHttpRequest`）执行，兼具低内存开销与高并发能力。

### 4.2 模型独立限流与 Sticky 调度

- **模型级配额隔离**：各账号针对不同模型（如 `gemini-3.7-flash`、`gemini-3.8-flash`）的 429 状态独立记录，单模型额度耗尽不影响其他模型的正常调用。
- **Sticky 黏性调度**：默认保持当前活跃账号，直到该账号针对当前模型遭遇 429 限流时，才自动顺延切换至下一个健康账号。
- **自动配额重置**：每日美西时间午夜（00:00 PST / 16:00 CST）自动重置 RPD 限制，无需重启服务。

### 4.3 防切号雪崩互斥锁 (`_switch_lock`)

当多个并发协程同时遭遇 429 限流时：
1. 率先获得 `_switch_lock` 的协程执行实质性切号与页面上下文刷新；
2. 后续排队获得锁的协程通过双重检查（Double-Checked Locking），识别到账号已被切换至健康账号且对当前模型可用，直接复用重试，避免并发 429 导致多次无谓切号。

---

## 5. 跨平台支持与依赖隔离

| 平台 | 运行模式 | 浏览器后端 | 内存基准 |
|---|---|---|---|
| **Android (Termux)** | `proot-distro` Linux 容器隔离运行 | CloakBrowser (aarch64) | 约 500 - 650 MB（建议空闲 RAM ≥ 1 GB） |
| **Linux (x86_64 / arm64)** | 原生宿主运行 | 系统 Chrome / Chromium / CloakBrowser | 约 350 - 500 MB |
| **macOS (Apple Silicon / Intel)** | 原生宿主运行 | Google Chrome / Chromium / Edge | 约 400 MB |
| **Windows (x64)** | 原生宿主运行 | Chrome / Edge | 约 450 MB |
| **Docker 容器** | Debian 基础镜像 | 容器内 headless Chromium | 约 400 MB |

> [!TIP]
> 依赖管理推荐使用 `uv`。在 Android Termux 环境下定向适配预编译 wheel，在 Linux/macOS/Windows 下解析官方 wheel，保障全平台构建的一致性与稳定性。
