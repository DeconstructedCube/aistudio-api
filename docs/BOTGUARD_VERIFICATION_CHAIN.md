# Google AI Studio BotGuard 验证机制技术规范

本文档记录 Google AI Studio 官方 Web 客户端与 Google WAA (Web Attestation & Anti-abuse / BotGuard) 服务端的交互协议、数据链路与验证逻辑。

---

## 目录

- [1. 系统架构与交互拓扑](#1-系统架构与交互拓扑)
- [2. 阶段一：页面加载与服务初始化](#2-阶段一页面加载与服务初始化)
- [3. 阶段二：WAA 挑战握手与虚拟机加载](#3-阶段二waa-挑战握手与虚拟机加载)
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
        BGVM["BotGuard Bitstream VM<br/>(动态加载的 JS 字节码虚拟机)"]
        UI <--> BGSvc
        BGSvc <--> BGVM
    end

    subgraph GoogleInfra ["Google 后端服务集群 (Google Infrastructure)"]
        MakerSuite["MakerSuite RPC 网关<br/>alkalimakersuite-pa.clients6.google.com"]
        WaaServer["WAA 反作弊网关<br/>waa-pa.clients6.google.com"]
        StaticCDN["Google 静态 CDN<br/>www.google.com/js/bg/..."]
    end

    UI -- "(1) 页面资源 & GenerateContent RPC" --> MakerSuite
    BGSvc -- "(2) 挑战初始化握手 (/Waa/Create)" --> WaaServer
    BGVM -- "(3) 下载并执行多态 JS 解释器" --> StaticCDN
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
    WaaClient->>WaaServer: POST /Waa/Create (携带 X-Goog-AuthUser 与 SAPISIDHASH)
    WaaServer-->>WaaClient: 下发挑战字节码 (Program) 与解释器脚本哈希 (interpreterHash)
    WaaClient->>StaticCDN: GET /js/bg/{interpreterHash}.js
    StaticCDN-->>WaaClient: 返回多态 JS 字节码解释器

    Note over User,Gateway: 【阶段三 & 四：用户提问与动态签名】
    User->>Browser: 输入 Prompt 并点击 Run
    Browser->>Browser: 计算 content_hash = SHA256(Prompt + Images)
    Browser->>WaaClient: snapshot(content_hash)
    WaaClient->>WaaClient: 采集 DOM/环境特征 + Lookup3(挑战盐值 + "0" + content_hash)
    WaaClient-->>Browser: 返回加密快照 Token (!dXaldhL...)
    Browser->>Browser: 组装 Wire 数据包 (body[4] = snapshot)
    Browser->>Gateway: POST /GenerateContent (Fetch credentials='include')
    Note over Gateway: 【阶段五：服务端校验与响应】
    Gateway->>Gateway: 解密 Body[4] 快照，校验 Tag 22 内容哈希签名与 Tag 59 会话令牌
    Gateway->>Gateway: 校验外层 User-Agent / X-Goog-AuthUser 与快照内部声明是否一致
    Gateway-->>Browser: 校验通过，分块流式返回推理响应 (200 OK)
    Browser-->>User: 实时渲染并展示模型生成结果
```

---

## 2. 阶段一：页面加载与服务初始化

1. **页面与路由加载**：
   - 用户访问 `https://aistudio.google.com/prompts/new_chat?model=gemini-3.8-flash`（或子账号路由 `/u/{auth_user}/...`）。
   - HTML 页面引入 `boq-makersuite` JavaScript Bundle。
2. **依赖注入容器初始化**：
   - Angular 根组件启动时，DI 容器解析并实例化 `BotGuardService`（符号随版本变化，如 `_.QC`、`_.Vv`，注入令牌挂载于 root 作用域）：
     ```javascript
     // 结构参考 (类混淆名以实际运行环境为准):
     _.QC = class {
         constructor() {
             this.qb = _.n(_.Aq);
             this.F = !1;
             this.H = new Promise(a => {
                 var b = this;
                 return _.y(function*() {
                     b.qb.la && (yield b.initialize());
                     a();
                 });
             });
         }
     };
     ```
   - 从 Cookie 中读取 `SAPISID`，并根据当前 UNIX 时间戳计算 `SAPISIDHASH`。

---

## 3. 阶段二：WAA 挑战握手与虚拟机加载

在 `BotGuardService.initialize()` 中，前端与 Google WAA 服务完成握手：

```javascript
initialize() {
    var a = this;
    return _.z(function*() {
        var b = new QVa; // waa-pa 远程 RPC 通信客户端
        a.A = new LUa({
            fetcher: new ura(b, () => a.getMetadata())
        });
        yield a.A.u6a; // 等待 BotGuard 虚拟机握手并初始化完成
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
### 3.1 挑战握手交互 (`/Waa/Create`)

请求发往 `https://waa-pa.clients6.google.com/$rpc/google.internal.waa.v1.Waa/Create`（保活探测为 `/Waa/Ping`）：

- **请求头**：
  ```http
  X-Goog-Api-Key: AIzaSyBGb5fGAyC-pRcRU6MUHb__b_vKha71HRE
  Authorization: SAPISIDHASH <timestamp>_<sha1_hash> ...
  X-Goog-AuthUser: 0
  Content-Type: application/json+protobuf
  ```
- **请求负载**：`["lmnUSbltwc5ULv48iKLX"]`。
- **响应载荷**：外层为二元 JSON 数组，其中 `outer[1]` Base64 解码后每个字节加 `97` (`(b + 97) & 0xFF`) 得到内层 JSON 数组 `[messageId, null, null, interpreterHash, programBytecode, globalName]`。

### 3.2 加载多态 JS 虚拟机与字节码

前端根据 `interpreterHash` 从 Google 静态 CDN 拉取对应解释器脚本：

```http
GET https://www.google.com/js/bg/<interpreterHash>.js
```

脚本在隐藏 `<iframe>` 中解码并执行 `programBytecode`（约 25.8 KB 的加密比特流程序）。字节码内嵌了服务端签发的 64 字节会话上下文 Token（`Tag 59`）与 217 字节认证 Blob（`Tag 1`），与当前 `auth_user` 及挑战盐值绑定。

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

2. **生成 BotGuard 快照 (`_.Pn` / `_.jp` / `_.Dp` / `snapKey`)**：
   前端调用被检测到的专属签名函数（在 `default_MakerSuite` 中动态匹配注册）：
   ```javascript
   // 实测最新版本 (2026-10-27 live AST 验证 - live_m=_b.js):
   _.Pn = function(a, b) {
       return _.y(function*() {
           yield a.H; // 等待 BotGuardService 初始化 Promise
           return a.A
               ? (yield Fxb(a.A), a.A.snapshot({ BFc: { content: b } }))
               : "";
       });
   };
   _.gD = function(a) {
       return _.y(function*() {
           var b = _.Ck(a, _.au, 2).flatMap(c => c.Rg()).map(_.jn);
           return _.VC(b.join(" "));
       });
   };
   // 历史版本混淆符号参考:
   // 2026-09-28: _.jp = (a, b) => a.A.snapshot({ Bcc: { content: b } })
   // 更早版本:   _.Dp = (a, b) => a.A.snapshot({ e9b: { content: b } })
   ```
   > **关键事实说明（严格对齐网页源码 AST 分析）**：
   > 1. `_.Ck(a, _.au, 2)` 仅访问请求 Protobuf 的字段 2（即 `contents` 消息体），**绝不包含**字段 6（`system_instruction`）。
   > 2. `_.jn` 仅对文本 (tag 2)、内联数据 (tag 3)、文件 (tag 6) 提取字符串，其余非文本 Part（函数调用、执行结果等）**统一返回空字符串 `""`**。
   > 3. 数组所有元素以单空格 `" "` 进行 `join`，**不作过滤**，随后计算 SHA-256 Hex 摘要。
   >
   > **符号混淆与动态匹配**：Google 构建版本更新时会重置混淆名（如类名 `_.QC`/`_.nC`、签名函数 `_.Pn`/`_.jp`、参数键 `BFc`/`Bcc` 等），远程 WAA 签名服务（`aistudio-pg-bridge`）会在握手阶段动态适配对应方法。
   >
   > **生命周期与状态**：
   > - 快照 Token 与单次请求内容及时间戳绑定，不跨请求复用。
   > - 会话凭据与 Nonce 由服务端动态下发，账号切换时重新初始化。
3. **虚拟机内部处理**：
   - 执行五阶段 ARX 流密码自解密，采集浏览器 DOM、屏幕、时区、WebIDL 原型链与交互事件特征写入 `ENV_OUT`；
   - 使用挑战专属 6 字符盐值计算 `Lookup3_IV(salt + "0" + contentHash)[2]`，以 4 字节大端整数写入 `ENV_OUT` 的 `Tag 22`；
   - 对 `ENV_OUT` 与 `TRACE2` 执行 64-bit CFB 加密，封装进 `MAIN_OUT` 并生成以 `!` 开头的 Base64URL 快照 Token（长约 1500~1650 字符）。

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
| **`11`** | `Field 12` | `session_token` | 会话续接令牌（如 `"v1_..."`） |
| **`13`** | `Field 14` | `request_context` | 请求上下文与时区声明（如 `[[null, null, "Asia/Tokyo"]]`） |
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
        Step1["1. 解密 Body[4] 快照载荷 (Tag 126 / Tag 153)"]
        Step2["2. 校验 Tag 22: Lookup3(salt + '0' + content_hash)"]
        Step3["3. 校验 Tag 59 会话 Token 与 X-Goog-AuthUser 一致性"]
        Step4["4. 校验外层 User-Agent 与 ENV_OUT 环境指纹"]

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

### 6.1 服务端报错特征与网关自愈机制

当 BotGuard 校验失败（快照缺失、快照过期、内容哈希不一致或身份冲突）时，服务端返回 **HTTP 403**，响应体为 JSON 数组 `[,[7,"The caller does not have permission"]]`（对应 gRPC 状态码 7 `PERMISSION_DENIED`）。

> [!NOTE]
> **上游偶发 403 / 404 说明**：
> 在上游风控抽查或连接抖动时，即使 Cookie、SAPISIDHASH 与 `!proof` 签名均合法，上游仍可能偶发返回 403 `The caller does not have permission` 或 404 `Ambiguous request for service`。此类响应并非账号封禁，通常通过重放、在位重签或切换账号即可恢复。

网关针对该报错建立两级自愈机制：
1. **多账号故障转移（Failover）**：网关自动对触发 403 的故障账号增加调度惩罚分（`auth_errors * 200.0`），优先调度下一个健康账号，同时不加硬冷却锁，允许同账号重新请求签名自愈。
2. **单账号在位重签（In-Place Recovery）**：当首次遭遇 403 或无备用账号可用时，网关自动重新加载账号凭据并请求 `WaaSigner` 生成全新证明令牌进行重试，恢复服务可用性。

### 6.2 外层 HTTP 请求头与签名特征环境一致性约束 (403 权限异常根源)

BotGuard 在前端或签名服务沙箱执行时，会调用 `navigator.userAgent` 提取当前宿主现场特征，并将此特征作为签名载荷的一部分加密到 Proof Token 中。

**不同底层签名方案的特征环境差异**：
1. **SpiderMonkey 152 原生方案 (`waa-signer`)**：底层基于 SpiderMonkey 152 纯 C++ 引擎与 Firefox 152 宿主环境，Token 内部加密绑定的是 Firefox 152 的 User-Agent 与特征指纹；
2. **NodeVM 沙箱方案 (`spinsnow` / `aistudio-pg-bridge`)**：底层基于 Node.js VM 模拟环境，Token 内部加密绑定的是特定版本的 Chromium / Chrome User-Agent。

**一致性校验约束**：
当客户端向上游发送 `GenerateContent` 时，Google 上游会解密 Token 并严格比对外层 HTTP 头的 `User-Agent` 与 Token 内部声明的客户端特征。若外层为 Chrome UA 而 Token 内绑定为 Firefox（或反之），上游将判定为环境篡改并直接返回 **HTTP 403 `The caller does not have permission`**。
因此网关层实现了**签名服务特征动态绑定与纯手动切换机制**，严格保证外层请求头与签名服务底层环境事实一致。
---

## 7. 生命周期与会话约束

1. **时效性与内容绑定**：快照 Token 内包含时间戳与挑战 Nonce，且与特定请求的内容哈希强绑定。内容修改后需重新计算哈希并生成新快照。
2. **后台休眠与保活**：当标签页处于后台时，虚拟机可能进入休眠。页面重新切回前台时，`visibilitychange` 事件会触发恢复（`service.A.CE()`）；若已失效则重新执行握手。
3. **多账号身份隔离**：每个子账号（`u/0`, `u/1`...）对应独立的身份上下文与 VM 实例，切换账号需通过 `/u/{auth_user}/` 路由加载对应账号上下文。

## 8. 核心报文结构参考

### 8.1 SAPISIDHASH 鉴权格式

```text
Authorization: SAPISIDHASH <TS>_<HASH1> SAPISID1PHASH <TS>_<HASH2> SAPISID3PHASH <TS>_<HASH3>
```

计算逻辑：
```text
HASH = SHA1(timestamp + " " + SAPISID_VALUE + " https://aistudio.google.com")
```
必须严格生成完整的三段（`SAPISIDHASH`、`SAPISID1PHASH` 与 `SAPISID3PHASH`），即使 `__Secure-3PAPISID` 与 `SAPISID` 值相同也必须包含第三段，缺失任意段会导致偶发 403 鉴权失败。

### 8.2 快照签名 Token 特征

| 属性 | 特征 |
|---|---|
| **前缀** | 固定以感叹号 `!` 开头 |
| **编码** | URL-Safe Base64 |
| **长度** | 约 1500 ~ 1650 字符 |
| **内容** | 包含硬件与环境指纹、内容哈希 Lookup3 签名（`Tag 22`）与会话 Nonce |

> 关于 WAA 字节码虚拟机的 32+16 条多态指令集、五阶段 ARX 流密钥派生管线、完整 TLV 标签字典与静态反汇编工具链，详见 [WAA 虚拟机内部架构与签名协议规范 (`docs/WAA_VM_ARCHITECTURE.md`)](./WAA_VM_ARCHITECTURE.md)。
