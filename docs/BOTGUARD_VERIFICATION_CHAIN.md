# Google AI Studio BotGuard 验证机制技术规范

本文档记录 Google AI Studio 官方 Web 客户端与 Google WAA (Web Attestation & Anti-abuse / BotGuard) 服务端的交互协议、数据链路与验证逻辑。

---

## 目录

- [1. 系统架构与交互拓扑](#1-系统架构与交互拓扑)
- [2. 阶段一：页面加载与服务初始化](#2-阶段一页面加载与服务初始化)
- [3. 阶段二：WAA 挑战握手与 Wasm 运行时加载](#3-阶段二waa-挑战握手与-wasm-运行时加载)
- [4. 阶段三：请求内容哈希与快照签名](#4-阶段三请求内容哈希与快照签名)
- [5. 阶段四：Wire 协议组包与浏览器 Fetch/XHR 重放](#5-阶段四wire-协议组包与浏览器-fetchxhr-重放)
- [6. 阶段五：服务端校验与统一报错响应](#6-阶段五服务端校验与统一报错响应)
- [7. 生命周期与会话约束](#7-生命周期与会话约束)
- [8. 核心报文结构参考](#8-核心报文结构参考)

---

## 1. 系统架构与交互拓扑

浏览器与 Google 后端服务集群的三方交互拓扑关系：

```mermaid
flowchart TD
    subgraph Browser ["用户浏览器环境 (Client Browser)"]
        UI["Google AI Studio UI<br/>(Angular SPA)"]
        BGSvc["BotGuardService<br/>(WAA 前端容器)"]
        BGWasm["BotGuard Wasm Runtime<br/>(动态加载的沙箱虚拟机)"]
        UI <--> BGSvc
        BGSvc <--> BGWasm
    end

    subgraph GoogleInfra ["Google 后端服务集群 (Google Infrastructure)"]
        MakerSuite["MakerSuite RPC 网关<br/>alkalimakersuite-pa.clients6.google.com"]
        WaaServer["WAA 反作弊网关<br/>waa-pa.clients6.google.com"]
        StaticCDN["Google 静态 CDN<br/>www.google.com/js/bg/..."]
    end

    UI -- "(1) 页面资源 & GenerateContent RPC" --> MakerSuite
    BGSvc -- "(2) 挑战初始化握手 (/Waa/Ping)" --> WaaServer
    BGWasm -- "(3) 下载与执行专属 Wasm 字节码" --> StaticCDN
```

### 完整交互时序

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户 (User)
    participant Browser as AI Studio (Angular)
    participant WaaClient as BotGuardService
    participant WaaServer as WAA 网关 (waa-pa)
    participant StaticCDN as Google CDN (Static)
    participant Gateway as MakerSuite RPC (alkali)

    Note over User,Gateway: 【阶段一 & 二：页面启动与 WAA 挑战握手】
    User->>Browser: 访问 aistudio.google.com/u/0/prompts/new_chat
    Browser->>Browser: Angular 初始化并实例化 BotGuardService
    Browser->>WaaClient: initialize()
    WaaClient->>WaaServer: POST /Waa/Ping (携带 X-Goog-AuthUser 与 SAPISIDHASH)
    WaaServer-->>WaaClient: 下发会话挑战 Nonce 与 Wasm 资源文件名
    WaaClient->>StaticCDN: GET /js/bg/{hash}.js
    StaticCDN-->>WaaClient: 返回动态 BotGuard 虚拟机代码 (绑定 u/0 身份)

    Note over User,Gateway: 【阶段三 & 四：用户提问与动态签名】
    User->>Browser: 输入 Prompt 并点击 Run
    Browser->>Browser: 计算 content_hash = SHA256(Prompt + Images)
    Browser->>WaaClient: snapshot(content_hash)
    WaaClient->>WaaClient: 收集环境/时钟指纹 + HMAC(内容哈希 + 凭据)
    WaaClient-->>Browser: 返回加密快照 Token (!dXaldhL...)
    Browser->>Browser: 组装 Wire 数据包 (body[4] = snapshot)
    Browser->>Gateway: POST /GenerateContent (Fetch credentials='include')
    Note over Gateway: 【阶段五：服务端校验与响应】
    Gateway->>Gateway: 解密 Body[4] 快照，比对 HMAC 内容指纹
    Gateway->>Gateway: 校验快照内嵌 GAIA ID 与请求头 X-Goog-AuthUser
    Gateway-->>Browser: 校验通过，分块流式返回推理响应 (200 OK)
    Browser-->>User: 实时渲染并展示模型生成结果
```

---

## 2. 阶段一：页面加载与服务初始化

1. **页面与路由加载**：
   - 用户访问 `https://aistudio.google.com/prompts/new_chat?model=gemini-3.8-flash`（或子账号路由 `/u/{auth_user}/...`）。
   - HTML 页面引入 `boq-makersuite` JavaScript Bundle。
2. **依赖注入容器初始化**：
   - Angular 根组件启动时，DI 容器解析并实例化 `BotGuardService`（即混淆类 `_.Vv`，注入令牌 `_.Vv.sa`）单例：
     ```javascript
     _.Vv = class {
         constructor() {
             this.wb = _.q(_.mr); // 注入全局环境配置
             this.F = false;
             this.H = new Promise(a => {
                 var b = this;
                 return _.z(function*() {
                     b.wb.la && (yield b.initialize());
                     a();
                 });
             });
             // 监听标签页可见性变化
             document.addEventListener("visibilitychange", () => {
                 if (this.F && document.visibilityState === "visible") {
                     let a;
                     (a = this.A) == null || a.KE();
                 }
                 this.F = document.visibilityState === "hidden";
             });
         }
     };
     _.Vv.J = function(a) { return new (a || _.Vv); };
     _.Vv.sa = _.bd({ token: _.Vv, factory: _.Vv.J, Aa: "root" });
     ```
   - 从 Cookie 中读取 `SAPISID`，并根据当前 UNIX 时间戳计算 `SAPISIDHASH`。

---

## 3. 阶段二：WAA 挑战握手与 Wasm 运行时加载

在 `BotGuardService.initialize()` 中，前端与 Google WAA 服务完成握手：

```javascript
initialize() {
    var a = this;
    return _.z(function*() {
        var b = new QVa; // waa-pa 远程 RPC 通信客户端
        a.A = new LUa({
            fetcher: new ura(b, () => a.getMetadata())
        });
        yield a.A.u6a; // 等待 Wasm 运行时握手完成并就绪
    });
}
getMetadata() {
    var a;
    return {
        "X-Goog-Api-Key": "AIzaSyBGb5fGAyC-pRcRU6MUHb__b_vKha71HRE",
        Authorization: (a = _.bq([])) != null ? a : "",
        "X-Goog-AuthUser": this.wb.Bo.toString()
    };
}
```
### 3.1 握手信令交互 (`/Waa/Ping`)

请求发往 `https://waa-pa.clients6.google.com/$rpc/google.internal.waa.v1.Waa/Ping`：

- **请求头示例**：
  ```http
  X-Goog-Api-Key: AIzaSyBGb5fGAyC-pRcRU6MUHb__b_vKha71HRE
  Authorization: SAPISIDHASH <timestamp>_<sha1_hash> ...
  X-Goog-AuthUser: 0
  Content-Type: application/json+protobuf
  ```
- **请求负载**：包含客户端接入 ID（如 `client: "lmnUSbltwc5ULv48iKLX"`）与环境参数。
- **响应载荷**：WAA 服务返回会话挑战 Nonce 与专属 JS/Wasm 资源哈希。

### 3.2 动态加载 Wasm 虚拟机

前端向 Google CDN 请求对应哈希的脚本：

```http
GET https://www.google.com/js/bg/gBetl7I-09yp6c3Nmm4ajwTxhDHStoNbVEOK3L3hfg4.js
```

脚本执行并实例化核心虚拟机。此时虚拟机内部已绑定当前会话的 GAIA ID、Nonce 与 `auth_user` 凭据。

---

## 4. 阶段三：请求内容哈希与快照签名

当用户提交提示词触发请求时，AI Studio 底层执行管线：

```javascript
// 请求组装与签名管线
t = yield _.Nv(requestProto);        // 1. 递归提取 contents 并计算 SHA-256 内容哈希
t = yield _.Dp(this.zc, t);          // 2. 调用快照函数生成 Token
_.l(requestProto, 5, t);             // 3. 将 Token 注入 Protobuf 字段 5 (Wire 数组 body[4])
```

1. **计算内容哈希 (`_.Nv`)**：
   提取请求 `contents` 数组中的所有文本与内联图片数据，通过 `_.rj` 映射为字符串，按顺序以单个空格 `" "` 拼接后计算 SHA-256 Hex 摘要：
   ```javascript
   _.Nv = function(requestProto) {
       return _.z(function*() {
           var b = _.Mv(requestProto).flatMap(c => c.eg()).map(_.rj);
           return _.dv(b.join(" "));
       });
   };

   _.rj = function(part) {
       switch(_.oj(part)) {
           case 2: // text 文本块
               return part.getText();
           case 3: // inline_data 图片数据 (Base64 字符串)
               let b, c;
               return (b = _.pj(part)) == null ? void 0 : (c = b.getData()) == null ? void 0 : c.Xe();
           case 6: // file_data 引用资源
               let d;
               return (d = _.qj(part)) == null ? void 0 : d.getId();
           default: // functionCall / functionResponse 等非文本
               return "";
       }
   };

   _.dv = function(str) {
       var a = (new TextEncoder).encode(str);
       var b = new _.pqa; // SHA-256 算法实现
       b.update(a);
       return maa(b.digest()); // 转换为 16 进制 hex string
   };
   ```

2. **生成 BotGuard 快照 (`_.Dp` / `snapKey`)**：
   前端调用被检测到的专属签名函数（在 `default_MakerSuite` 中动态匹配注册）：
   ```javascript
   _.Dp = function(service, contentHash) {
       return _.z(function*() {
           yield service.H; // 等待 BotGuardService 初始化 Promise
           return service.A
               ? (yield JUa(service.A), service.A.snapshot({ e9b: { content: contentHash } }))
               : "";
       });
   };
   ```

3. **虚拟机内部处理**：
   - 采集浏览器指纹（屏幕分辨率、语言列表、时钟精度、WebGL 上下文特征等）；
   - 使用握手会话秘钥对 `contentHash` 与环境特征进行加密并计算 HMAC 签名；
   - 生成以 `!` 开头、长度约为 1500~1650 字符的 Base64 快照 Token（例如 `!dXaldhLNAAa1dSU5lXVCmOax...`）。

## 5. 阶段四：Wire 协议组包与浏览器 Fetch/XHR 重放

```json
[
  "models/gemini-3.8-flash",
  [
    [
      [[null, "用户输入的提示词内容"]],
      "user"
    ]
  ],
  [[null, null, 7, 5], [null, null, 8, 5], [null, null, 9, 5], [null, null, 10, 5]],
  [null, null, null, 128, 0.5, 0.8, 16],
  "!dXaldhLNAAa1dSU5lXVCmOax...",
  null,
  null,
  null,
  null,
  null,
  1,
  null,
  null,
  [[null, null, "Asia/Tokyo"]]
]
```

数组索引与 Google Protobuf 字段序号的精确映射（`Index = FieldNumber - 1`）：

| 数组索引 | Proto 字段号 | 字段名称 | 类型与说明 |
|---|---|---|---|
| **`0`** | `Field 1` | `model` | 目标模型名称（如 `"models/gemini-3.8-flash"`、`"models/gemini-3.7-flash"`） |
| **`1`** | `Field 2` | `contents` | 结构化对话轮次与多模态数据数组 |
| **`2`** | `Field 3` | `safety_settings` | 安全审核阈值配置数组 |
| **`3`** | `Field 4` | `generation_config` | 生成控制参数（temperature, topP, topK, maxTokens 等） |
| **`4`** | `Field 5` | `snapshot` | **BotGuard 快照签名 Token（以 `!` 开头）** |
| **`5`** | `Field 6` | `system_instruction` | 系统指令角色与提示词块 |
| **`6`** | `Field 7` | `tools` | 工具声明列表（Google 搜索、代码执行、Google Maps 等） |
| **`7`** | `Field 8` | `evergreen_model_uri` | 动态常青模型 URI（如 `"models/gemini-..."`） |
| **`10`** | `Field 11` | `request_flag` | 请求行为标记（固定值 `1`） |
| **`11`** | `Field 12` | `cached_content` | 上下文缓存资源名称 |
| **`13`** | `Field 14` | `location` | 用户位置与时区声明（如 `[[null, null, "Asia/Tokyo"]]`） |
### 浏览器内 Fetch/XHR 发送

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

- 设置 `credentials: 'include'`（或 `xhr.withCredentials = true`），自动附加浏览器内存储的完整会话 Cookie。

---

## 6. 阶段五：服务端校验与统一报错响应

服务端网关接收到请求后的审计与校验链路：

```mermaid
flowchart TD
    Req["GenerateContent 请求<br/>(Header + Body)"] --> Step1

    subgraph GatewayValidation ["服务端校验流程"]
        Step1["1. 解密 Body[4] 快照载荷"]
        Step2["2. 校验 HMAC(Body[1] Contents 哈希)"]
        Step3["3. 比对快照 GAIA ID 与 X-Goog-AuthUser 会话"]
        Step4["4. 校验环境与时钟指纹"]

        Step1 -->|解密成功| Step2
        Step2 -->|哈希匹配| Step3
        Step3 -->|身份一致| Step4
    end

    Step1 -->|解密失败| Reject["统一拒绝：HTTP 403<br/>[,[7,'The caller does not have permission']]"]
    Step2 -->|哈希不匹配| Reject
    Step3 -->|身份冲突| Reject
    Step4 -->|指纹异常| Reject
    Step4 -->|校验通过| Accept["HTTP 200 OK<br/>分块流式返回推理数据"]
```

> [!IMPORTANT]
> **服务端报错特征与网关自愈恢复**：
> 当 BotGuard 校验失败（快照缺失、快照过期、内容哈希不一致或身份冲突）时，服务端统一返回 **HTTP 403**，响应体为 JSON 数组 `[,[7,"The caller does not have permission"]]`（对应 gRPC 状态码 7 `PERMISSION_DENIED`）。
> 
> 网关针对该报错建立了两级快速自愈机制：
> 1. **多账号故障转移（Failover）**：网关自动对触发 403 的故障账号施加 300 秒短时隔离（`auth_cooldown`），立即调度下一个健康账号，规避在失效账号上无效重试导致的请求阻塞；
> 2. **单账号在位重建（In-Place Recovery）**：当无备用账号可用时，网关立即清空快照与捕获模板缓存，重载 Cookie 并通过 `ensure_botguard_service(force_refresh=True)` 重新执行页面导航与 WAA 挑战握手，快速恢复服务可用性。

---

## 7. 生命周期与会话约束

> [!NOTE]
> 1. **时效性与内容绑定**：快照 Token 内包含时间戳与挑战 Nonce，且与特定请求的内容哈希强绑定。提示词修改后必须重新计算哈希并生成新快照。
> 2. **后台休眠与保活**：当标签页处于后台时，虚拟机可能进入休眠。页面重新切回前台时，`visibilitychange` 事件会触发轻量恢复（`service.A.CE()`）；若已失效则重新执行握手。
> 3. **多账号身份隔离**：每个子账号（`u/0`, `u/1`...）对应独立的身份上下文与 Wasm 实例，切换账号必须通过严格的 `/u/{auth_user}/` 专属路由切换并加载对应账号的上下文，杜绝 SPA 拦截造成的身份混淆。
---

## 8. 核心报文结构参考

### 8.1 SAPISIDHASH 鉴权格式

```text
Authorization: SAPISIDHASH <TS>_<HASH1> SAPISID1PHASH <TS>_<HASH2> SAPISID3PHASH <TS>_<HASH3>
```

计算逻辑：
```text
HASH = SHA1(timestamp + " " + SAPISID_VALUE + " https://aistudio.google.com")
```

### 8.2 快照签名 Token 特征

| 属性 | 特征 |
|---|---|
| **前缀** | 固定以感叹号 `!` 开头 |
| **编码** | URL-Safe Base64 |
| **长度** | 约 1500 ~ 1650 字符 |
| **内容** | 包含硬件与环境指纹、内容哈希 HMAC 与会话 Nonce |
