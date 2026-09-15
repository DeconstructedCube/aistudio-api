# Google AI Studio / BotGuard 完整验证与运行全链路技术规范

本文档详尽记录 **Google AI Studio** 官方前端与 **Google WAA (Web Attestation & Anti-abuse / BotGuard)** 反作弊系统在**正常未经 Hook / 逆向**情况下的完整运行逻辑、网络交互时序、数据协议与底层验证机制，供行为研究、协议兼容性分析与仿真调试参考。

---

## 目录
1. [系统整体架构与参与主体](#1-系统整体架构与参与主体)
2. [阶段一：页面加载与 Angular DI 服务初始化](#2-阶段一页面加载与-angular-di-服务初始化)
3. [阶段二：Waa (Web Attestation) 挑战握手与 Wasm 动态加载](#3-阶段二waa-web-attestation-挑战握手与-wasm-动态加载)
4. [阶段三：用户交互、内容哈希与 BotGuard 动态签名](#4-阶段三用户交互内容哈希与-botguard-动态签名)
5. [阶段四：Wire Codec 组包与浏览器内原生 XHR 传输](#5-阶段四wire-codec-组包与浏览器内原生-xhr-传输)
6. [阶段五：Google 服务端多维交叉审计与放行逻辑](#6-阶段五google-服务端多维交叉审计与放行逻辑)
7. [生命周期管理、会话保活与异常失效](#7-生命周期管理会话保活与异常失效)
8. [核心数据结构与报文参考](#8-核心数据结构与报文参考)

---

## 1. 系统整体架构与参与主体

在正常用户访问过程中，涉及三大核心参与主体：

```mermaid
flowchart TD
    subgraph Browser ["用户浏览器环境 (Client Browser)"]
        UI["Google AI Studio UI<br/>(Angular 18+ SPA)"]
        BGSvc["BotGuardService<br/>(WAA 前端容器)"]
        BGWasm["BotGuard Wasm Runtime<br/>(动态加载的隔离虚拟机)"]
        UI <--> BGSvc
        BGSvc <--> BGWasm
    end

    subgraph GoogleInfra ["Google 后端服务集群 (Google Infrastructure)"]
        MakerSuite["MakerSuite RPC 网关<br/>alkalimakersuite-pa.clients6.google.com"]
        WaaServer["WAA 反作弊网关<br/>waa-pa.clients6.google.com"]
        StaticCDN["Google 静态 CDN<br/>www.google.com/js/bg/..."]
    end

    UI -- "(1) 页面资源 & GenerateContent RPC" --> MakerSuite
    BGSvc -- "(2) 挑战初始化握手 (Waa/Ping)" --> WaaServer
    BGWasm -- "(3) 下载与执行专属 Wasm 字节码" --> StaticCDN
```

### 交互时序泳道图

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户 (User)
    participant Browser as AI Studio (Angular)
    participant WaaClient as BotGuardService (qUa)
    participant WaaServer as WAA 网关 (waa-pa)
    participant StaticCDN as Google CDN (Static)
    participant Gateway as MakerSuite RPC (alkali)

    Note over User,Gateway: 【阶段一 & 二：页面启动与 WAA 挑战握手】
    User->>Browser: 访问 aistudio.google.com/u/0/prompts/new_chat
    Browser->>Browser: Angular 引导，实例化 BotGuardService
    Browser->>WaaClient: initialize()
    WaaClient->>WaaServer: POST /Waa/Ping (携带 X-Goog-AuthUser: 0 & SAPISIDHASH)
    WaaServer-->>WaaClient: 下发会话挑战 Nonce & Wasm 资源文件名
    WaaClient->>StaticCDN: GET /js/bg/{hash}.js
    StaticCDN-->>WaaClient: 返回动态 BotGuard 虚拟机字节码 (固化 u/0 身份)

    Note over User,Gateway: 【阶段三 & 四：用户提问与动态签名】
    User->>Browser: 输入 Prompt 并点击 "Run" (或 Ctrl+Enter)
    Browser->>Browser: 计算 content_hash = SHA256(Prompt + Images)
    Browser->>WaaClient: Bp(service, content_hash)
    WaaClient->>WaaClient: Wasm 收集硬件/时钟指纹 + HMAC(内容哈希 + 身份凭据)
    WaaClient-->>Browser: 返回加密快照 Token (!dXaldhL...)
    Browser->>Browser: Wire Codec 组包 (body[4] = snapshot)
    Browser->>Gateway: POST /GenerateContent (XHR withCredentials = true)

    Note over Gateway: 【阶段五：服务端交叉审计与放行】
    Gateway->>Gateway: 解密 Body[4] 快照，比对 HMAC 内容指纹
    Gateway->>Gateway: 交叉比对快照内嵌 GAIA ID 与请求头 X-Goog-AuthUser
    Gateway-->>Browser: 审计通过，流式返回分块推理响应 (200 OK)
    Browser-->>User: 界面渲染并实时展示模型生成结果
```

---

## 2. 阶段一：页面加载与 Angular DI 服务初始化

1. **路由抵达**：
   - 用户浏览器访问 `https://aistudio.google.com/prompts/new_chat?model=gemini-3.8-flash`（或带有子账号路由的 `/u/{auth_user}/...`）。
   - HTML 页面引入 `boq-makersuite` 打包 JavaScript Bundle（位于 `www.gstatic.com/_/mss/boq-makersuite/...`）。
2. **依赖注入（DI）容器装配**：
   - Angular 引导（Bootstrap）根组件及提示词编辑页面模块。
   - Angular DI 容器解析并实例化 `BotGuardService` 单例：
     ```javascript
     class BotGuardService {
         constructor() {
             this.wb = _.q(_.jr); // 注入全局环境配置
             this.F = false;
             this.H = new Promise((resolve) => {
                 var self = this;
                 return _.z(function*() {
                     self.wb.ea && (yield self.initialize());
                     resolve();
                 });
             });
             // 监听可见性切换以执行保活
             document.addEventListener("visibilitychange", () => {
                 if (this.F && document.visibilityState === "visible") {
                     this.A?.CE();
                 }
                 this.F = document.visibilityState === "hidden";
             });
         }
     }
     ```
3. **读取身份元数据**：
   - 从页面上下文中读取当前激活账户索引 `this.wb.xo`（即 `auth_user`，如 `"0"`、`"1"`）；
   - 从 `document.cookie` 读取 `SAPISID`，并通过标准算法计算当前 UNIX 秒级时间戳对应的 `SAPISIDHASH`。

---

## 3. 阶段二：Waa (Web Attestation) 挑战握手与 Wasm 动态加载

在 `BotGuardService.initialize()` 中，前端开始与 Google 的 WAA（Web Attestation & Anti-abuse）服务器进行挑战初始化握手：

```javascript
initialize() {
    var self = this;
    return _.z(function*() {
        var transport = new vVa(); // 底层 gRPC-Web / JSON-RPC 传输层
        self.A = new qUa({
            fetcher: new tra(transport, () => self.getMetadata())
        });
        yield self.A.z5a; // 等待 Wasm 运行时握手并就绪
    });
}
```

### 3.1 挑战信令交互 (Ping / Create)
`tra.ping()` 组装挑战请求并通过 `POST` 发往 `https://waa-pa.clients6.google.com/$rpc/google.internal.waa.v1.Waa/Ping`：
- **请求头**：
  ```http
  X-Goog-Api-Key: AIzaSyBGb5fGAyC-pRcRU6MUHb__b_vKha71HRE
  Authorization: SAPISIDHASH <timestamp>_<sha1_hash> ...
  X-Goog-AuthUser: 0
  Content-Type: application/json+protobuf
  ```
- **请求负载**：
  包含客户端专有接入 ID（`client: "lmnUSbltwc5ULv48iKLX"`）与环境预备标志。
- **响应载荷**：
  WAA 服务器响应当前会话分配的独立挑战 Token 与专属 JS/Wasm 引导哈希（例如 `gBetl7I-09yp6c3Nmm4ajwTxhDHStoNbVEOK3L3hfg4`）。

### 3.2 动态加载 BotGuard 运行时
前端 `scriptLoader` 根据上述哈希，向 Google 静态资源集群发起请求：
```http
GET https://www.google.com/js/bg/gBetl7I-09yp6c3Nmm4ajwTxhDHStoNbVEOK3L3hfg4.js
```
该脚本在浏览器全局沙箱内执行，实例化核心 BotGuard 虚拟机（`this.A.H`）。**在此刻，该 Wasm 虚拟机内部已被硬编码注入了当前账号的 GAIA ID、会话 Nonce 以及握手时绑定的 `auth_user` 凭据**。

---

## 4. 阶段三：用户交互、内容哈希与 BotGuard 动态签名

当用户在界面操作并发起提问时：

1. **触发行为**：
   用户在提示词输入框内输入文本（或上传图片），点击界面右下角 `Run` 按钮（或按下快捷键 `Ctrl+Enter`）。
2. **提取并哈希会话内容**：
   UI 事件处理器搜集全部会话片段（`contents` 数组中的文字及 inline base64 图片）。为防止通信过程中内容被篡改，算法会对所有内容字符进行串联并执行 SHA-256 运算：
   ```text
   content_hash = SHA256(concat(parts.text) + concat(parts.inline_data))
   ```
   输出一个 64 位的十六进制摘要字符串（例如 `a5c2d89f...`）。
3. **执行快照签名计算**：
   Angular 逻辑调用公共签名导出方法（混淆键名为 `Bp`）：
   ```javascript
   dms.Bp = function(service, contentHash) {
       return _.z(function*() {
           yield service.H; // 确保 Waa 握手完成
           if (service.A) {
               yield oUa(service.A);
               // 调用底层的 snapshot 生成方法
               return service.A.snapshot({
                   I7b: {
                       content: contentHash
                   }
               });
           }
           return "";
       });
   };
   ```
4. **Wasm 虚拟机内部执行**：
   - 收集当前浏览器窗口及硬件指纹（屏幕宽高、像素比、语言列表、时钟精准微纳秒耗时、WebGL 渲染管线特征、历史点击时序）；
   - 使用握手时分配的会话秘钥，对 `contentHash` 与指纹进行加密和 HMAC 签名；
   - 生成以 `!` 开头、长度约为 1500~1600 字符的高熵 Base64 字符串（例如 `!dXaldhLNAAa1dSU5lXVCmOax...`）。

---

## 5. 阶段四：Wire Codec 组包与浏览器内原生 XHR 传输

签名成功后，Angular 前端将快照回填进 Google 内部专有的 Protobuf-over-JSON 数组结构（即 Wire Codec）：

```json
[
  "models/gemini-3.8-flash",
  [
    [
      [[null, "用户输入的提示词内容"]],
      "user"
    ]
  ],
  null,
  [null, null, null, 128, 0.5, 0.8, 16],
  "!dXaldhLNAAa1dSU5lXVCmOax...",
  null,
  null
]
```
- 索引 `0`：模型全称（如 `models/gemini-3.8-flash`）；
- 索引 `1`：结构化会话历史与多模态数据；
- 索引 `3`：生成控制配置（GenerationConfig：Temperature、TopP、TopK、MaxTokens 等）；
- **索引 `4`：BotGuard 快照签名 Token（核心反爬保护字段）**。

### 5.1 发起 XHR 通信
浏览器通过原生 `XMLHttpRequest` 发送异步流式请求：
```http
POST https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent
Host: alkalimakersuite-pa.clients6.google.com
Content-Type: application/json+protobuf
X-User-Agent: grpc-web-javascript/0.1
X-Goog-Api-Key: AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs
X-Goog-AuthUser: 0
Authorization: SAPISIDHASH 1789397237_0093f004...
Origin: https://aistudio.google.com
Referer: https://aistudio.google.com/
Cookie: SID=...; HSID=...; SSID=...; SAPISID=...; __Secure-1PAPISID=...
```
- `xhr.withCredentials = true`：保证浏览器底层 Cookie 存储区中的完整会话 Cookie 随请求上送；
- `X-Goog-AuthUser` 与当前 Angular 会话严格一致。

---

## 6. 阶段五：Google 服务端多维交叉审计与放行逻辑

Google RPC 网关接收到 `GenerateContent` 请求后，进行链式严格审计：

```mermaid
flowchart TD
    Req["传入请求: Header + Body[4] Snapshot + Body[1] Contents"] --> Step1

    subgraph Audit ["Google RPC 网关严格审计链"]
        Step1["步骤 1：解密 Body[4] Snapshot<br/>• 使用阶段二协商的私钥解密快照载荷"]
        Step2["步骤 2：内容完整性校验 (Anti-Tampering)<br/>• 计算 Body[1] Contents 的 SHA-256 哈希<br/>• 验证快照内嵌的 HMAC 摘要是否完全吻合"]
        Step3["步骤 3：账号身份交叉比对 (Identity Cross-Check)<br/>• 提取快照内嵌绑定的 GAIA 身份<br/>• 对比请求头 X-Goog-AuthUser 对应的 Cookie 会话"]
        Step4["步骤 4：客户端运行环境真实度评分 (Attestation Score)<br/>• 检查 WebGL 渲染管线 (拒绝 SwiftShader/虚拟显卡)<br/>• 检查时钟单调性、事件时序与 Headless 特征"]

        Step1 -->|解密成功| Step2
        Step2 -->|摘要一致| Step3
        Step3 -->|身份完全一致| Step4
    end

    Step1 -->|解密失败| Err1["抛出反作弊异常 / 拒绝连接"]
    Step2 -->|摘要冲突| Err2["抛出 400 INVALID_ARGUMENT (数据传输被篡改)"]
    Step3 -->|身份冲突| Err3["抛出 gRPC 7 / 403 PERMISSION_DENIED (越权或伪造)"]
    Step4 -->|评分达标| Pass["放行请求，开启后端模型推理，分块流式返回 200 OK"]
    Step4 -->|评分不达标| Err4["触发验证码拦截 / 降级限制"]

    style Pass fill:#d4edda,stroke:#28a745,stroke-width:2px,color:#155724
    style Err1 fill:#f8d7da,stroke:#dc3545,stroke-width:1px,color:#721c24
    style Err2 fill:#f8d7da,stroke:#dc3545,stroke-width:1px,color:#721c24
    style Err3 fill:#f8d7da,stroke:#dc3545,stroke-width:2px,color:#721c24
    style Err4 fill:#fff3cd,stroke:#ffc107,stroke-width:1px,color:#856404
```

---

> [!IMPORTANT]
> **1. 时效性与一次性原则**
> 每次生成的快照签名 `!` 字符串包含微秒时间戳与挑战 Nonce，仅允许在生成后的短时间内使用，不可跨请求无限制重复重放；提示词变更时必须传入新内容哈希重新由 Wasm 生成新签名。

> [!NOTE]
> **2. 后台挂起与保活（Visibility Change）**
> 当浏览器标签页切入后台（`document.visibilityState === "hidden"`）超过一定时长后，BotGuard 虚拟机进入挂起状态；用户重新切回页面（`document.visibilityState === "visible"`）时，`document.addEventListener("visibilitychange")` 自动触发 `service.A.CE()` 进行轻量恢复；若超时严重则触发 `service.initialize()` 重新向 `waa-pa` 拉取新挑战。

> [!WARNING]
> **3. 多账号切换约束**
> 同一物理 Session 下的多个子账号（`u/0`、`u/1`、`u/2`）拥有独立的身份上下文；切换账号时，由于旧 Wasm 虚拟机内部固化的 GAIA 身份无法热更新，必须通过 Angular 路由重载（`page.goto('/u/N/...')`）触发新一轮的依赖注入与 WAA 挑战握手。
---

## 8. 核心数据结构与报文参考

### 8.1 典型 SAPISIDHASH 鉴权格式
```text
Authorization: SAPISIDHASH <TS>_<HASH1> SAPISID1PHASH <TS>_<HASH2> SAPISID3PHASH <TS>_<HASH3>
```
计算公式：
```text
HASH = SHA1(timestamp + " " + SAPISID_VALUE + " https://aistudio.google.com")
```
### 8.2 快照签名 Token 特征
- 前缀：固定以感叹号 `!` 开头；
- 编码：高熵 URL-Safe Base64；
- 长度：稳定在 1500 ~ 1650 字节之间；
- 熵值：经加密与压缩，字符分布极其离散，任何字节微调都会导致后端解密失败。
