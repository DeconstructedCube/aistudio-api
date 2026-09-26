# Google AI Studio Wire 协议逆向技术规范 (WIRE_SPECIFICATION.md)

本文档记录 Google AI Studio (MakerSuite / boq-makersuite) 底层 Protobuf-over-JSON Wire 数组协议的完整逆向映射结构、字段索引定义、类型系统及编解码实现细节。

---

## 目录

- [1. 协议概览与序列化机制](#1-协议概览与序列化机制)
- [2. 请求顶层 Wire 数组映射 (Request Array)](#2-请求顶层-wire-数组映射-request-array)
- [3. 对话内容与多模态数据结构 (Contents & Parts)](#3-对话内容与多模态数据结构-contents--parts)
  - [3.1 Content 结构](#31-content-结构)
  - [3.2 Part 字段定义与多态表示](#32-part-字段定义与多态表示)
- [4. 生成控制参数 (GenerationConfig)](#4-生成控制参数-generationconfig)
- [5. 安全策略 (SafetySettings)](#5-安全策略-safetysettings)
- [6. 工具与函数声明 (Tools & Function Calling)](#6-工具与函数声明-tools--function-calling)
  - [6.1 内置工具模板](#61-内置工具模板)
  - [6.2 自定义函数声明 (Function Declarations)](#62-自定义函数声明-function-declarations)
  - [6.3 结构化 Schema 编码与类型字典](#63-结构化-schema-编码与类型字典)
  - [6.4 工具控制模式 (ToolConfig)](#64-工具控制模式-toolconfig)
- [7. 响应报文解析与 Token 使用量 (Response & Usage)](#7-响应报文解析与-token-使用量-response--usage)
  - [7.1 响应 Chunk 容器](#71-响应-chunk-容器)
  - [7.2 Candidate 与 Part 字段解析](#72-candidate-与-part-字段解析)
  - [7.3 Token 使用量 (UsageMetadata)](#73-token-使用量-usagemetadata)
  - [7.4 FinishReason 完整状态码映射表](#74-finishreason-完整状态码映射表)
- [8. 前端逆向新特性与高价值发现](#8-前端逆向新特性与高价值发现)
---

## 1. 协议概览与序列化机制

Google AI Studio 在 Web 端（`alkalimakersuite-pa.clients6.google.com`）与后端通信时，采用 Google 内部的 **Protobuf-over-JSON 紧凑数组** 格式（即 JSPB / Protobuf 数组序列化规范）。

- **字段定位规则**：Protobuf 字段序号为 `N` 的属性，在 JSON 数组中对应的索引为 `N - 1`（0-indexed）；
- **稀疏表示**：空字段或默认未赋值字段序列化为 `null`；
- **结尾截断**：尾部连续的 `null` 元素在 JSON 编码时可省略截断。

---

## 2. 请求顶层 Wire 数组映射 (Request Array)

发往 `MakerSuiteService/GenerateContent` 的 JSON 顶层数组索引映射如下：

| 数组索引 | Proto 字段号 | 字段名称 | 类型 | 说明 |
|---|---|---|---|---|
| **`0`** | Field 1 | `model` | `string` | 目标模型路径，如 `"models/gemini-3.8-flash"`、`"models/gemini-3.7-flash"` |
| **`1`** | Field 2 | `contents` | `list` | 结构化对话轮次数组（`AistudioContent` 列表） |
| **`2`** | Field 3 | `safety_settings` | `list \| null` | 安全拦截阈值数组 |
| **`3`** | Field 4 | `generation_config` | `list` | 生成控制参数数组（见第 4 节） |
| **`4`** | Field 5 | `snapshot` | `string` | **BotGuard WAA 快照签名 Token**（以 `!` 开头） |
| **`5`** | Field 6 | `system_instruction` | `list \| null` | 系统提示词内容（格式同 Content） |
| **`6`** | Field 7 | `tools` | `list \| null` | 工具声明列表（内置工具与自定义函数） |
| **`7`** | Field 8 | `evergreen_model_uri` | `string \| null` | 动态常青模型 URI（如 `"models/gemini-..."`） |
| **`10`** | Field 11 | `request_flag` | `int` | 请求行为标记，固定值 `1` |
| **`11`** | Field 12 | `cached_content` | `string \| null` | 缓存上下文名称 |
| **`13`** | Field 14 | `location` | `list \| null` | 用户位置与时区声明，如 `[[null, null, "Asia/Tokyo"], null, 1]` |

---

## 3. 对话内容与多模态数据结构 (Contents & Parts)

### 3.1 Content 结构

单个对话轮次表示为二元数组 `[parts, role]`：

```json
[
  [
    [null, "用户输入的提示词内容"]
  ],
  "user"
]
```

- `role` 可选值：`"user"`、`"model"`。

### 3.2 Part 字段定义与多态表示

每个 Part 是一个稀疏数组，根据其数据类型的不同占用不同的下标位置（对应 Protobuf 字段号 `N` 位于下标 `N - 1`）：

| Part 类型 | 数组结构映射 | 说明 |
|---|---|---|
| **文本 (Text)** | `[null, "文本内容"]` | 下标 `1` (Field 2) 为普通文本块 |
| **思考文本 (Thinking)** | `[null, "思考链内容", ..., ..., ..., ..., ..., ..., ..., ..., ..., null, 1]` | 下标 `1` 为思维文本，下标 `12` (Field 13) 为 `1` 标记思维链 |
| **内联多模态 (InlineData)** | `[null, null, ["image/jpeg", "base64_data"]]` | 下标 `2` (Field 3) 为 `[mimeType, base64]` |
| **资源引用 (FileData)** | `[null, null, null, null, null, ["file_id"]]` | 下标 `5` (Field 6) 为 `[file_id]` |
| **工具调用 (FunctionCall)** | `[..., [name, args_struct, call_id]]` | 请求中位于下标 `10` (Field 11)；响应中位于候选 Part 下标 `3` (Field 4) |
| **工具结果 (FunctionResponse)** | `[..., [name, response_struct, call_id]]` | 请求中位于下标 `11` (Field 12) |
| **思维签名 (ThoughtSignature)** | 位于任意 Part 的下标 `14` (`part[14] = "signature"`) | Google 官方校验思维链真实性的签名字符串 |

#### 参数与返回值的 Protobuf 结构体编码 (Struct & Value)
在 `FunctionCall.args` 与 `FunctionResponse.response` 中，键值数据遵循 `google.protobuf.Struct` 紧凑数组格式：
- **Struct 容器**：`[[key, value_wire], ...]`
- **Value 节点**（按值类型映射）：
  - `null`：`[0]`
  - `number` (int/float)：`[null, num]`
  - `string`：`[null, null, str]`
  - `boolean`：`[null, null, null, bool]`
  - `object` (嵌套 Struct)：`[null, null, null, null, [struct_fields]]`
  - `array` (ListValue)：`[null, null, null, null, null, [[item_values]]]`
---

## 4. 生成控制参数 (GenerationConfig)

`generation_config` 位于顶层数组下标 `3`，其内部数组下标定义如下：

| 数组索引 | 字段名称 | 类型 | 说明 |
|---|---|---|---|
| **`1`** | `stop_sequences` | `list[str] \| null` | 停止词列表 |
| **`3`** | `max_tokens` | `int \| null` | 最大生成 Token 数（如 `65536`） |
| **`4`** | `temperature` | `float \| null` | 采样温度（如 `1.0`） |
| **`5`** | `top_p` | `float \| null` | 核采样概率（如 `0.95`） |
| **`6`** | `top_k` | `int \| null` | Top-K 采样候选数（如 `64`） |
| **`7`** | `response_mime_type` | `string \| null` | 输出 MIME 类型（如 `"application/json"` 或 `"text/plain"`） |
| **`8`** | `response_schema` | `list \| null` | 结构化输出 JSON Schema（编码见第 6 节） |
| **`9`** | `presence_penalty` | `float \| null` | 存在惩罚系数 |
| **`10`** | `frequency_penalty` | `float \| null` | 频率惩罚系数 |
| **`11`** | `response_logprobs` | `bool \| null` | 是否返回 Logprobs |
| **`12`** | `logprobs` | `int \| null` | Logprobs 候选数 |
| **`14`** | `image_output_mode` | `list[int] \| null` | 生图输出模式：`[2]` (仅图片) 或 `[2, 1]` (图文混合) |
| **`16`** | `thinking_config` | `list \| null` | 思考模式：`[mode, null, null, level]`（`1`=LOW, `2`=MEDIUM, `3`=HIGH, `4`=MINIMAL） |
| **`17`** | `media_resolution` | `int \| null` | 多模态输入分辨率：`1`=LOW, `2`=MEDIUM, `3`=HIGH |
| **`26`** | `output_resolution` | `list[str] \| null` | 生图分辨率配置，如 `["1:1", "1K"]` |

---

## 5. 安全策略 (SafetySettings)

位于顶层数组下标 `2`，格式为二元条目列表 `[[null, null, category_code, threshold_code], ...]`：

### 类别码 (Category Code)
- `7`：`HARM_CATEGORY_HARASSMENT` (骚扰内容)
- `8`：`HARM_CATEGORY_HATE_SPEECH` (仇恨言论)
- `9`：`HARM_CATEGORY_SEXUALLY_EXPLICIT` (色情露骨内容)
- `10`：`HARM_CATEGORY_DANGEROUS_CONTENT` (危险内容)

### 阈值码 (Threshold Code)
- `1`：`BLOCK_LOW_AND_ABOVE` (低阈值及以上拦截，最严格)
- `2`：`BLOCK_MEDIUM_AND_ABOVE` (中阈值及以上拦截)
- `3`：`BLOCK_ONLY_HIGH` (仅高风险拦截)
- `4`：`BLOCK_NONE` (不拦截)
- `5`：`OFF` (完全关闭安全过滤器)

---

## 6. 工具与函数声明 (Tools & Function Calling)

位于顶层数组下标 `6` (Proto Field 7)。`tools` 是一个工具容器列表，支持内置工具与自定义函数声明。

### 6.1 内置工具模板

| 工具名称 | Wire 数组表示 |
|---|---|
| **代码执行 (Code Execution)** | `[[]]` |
| **网页搜索 (Google Search)** | `[null, null, null, [null, [[]]]]` |
| **图片搜索 (Google Image Search)** | `[null, null, null, [null, [null, []]]]` |
| **网页 + 图片联合搜索** | `[null, null, null, [null, [[], []]]]` |
| **谷歌地图 (Google Maps)** | `[null, null, null, null, null, null, null, null, null, null, []]` |
| **URL 上下文提取 (URL Context)** | `[null, null, null, null, null, null, null, []]` |

### 6.2 自定义函数声明 (Function Declarations)

在 `tools` 列表中，自定义函数集合序列化为 Tool 消息的 Field 2（前置 `null` 占位）：

```json
[
  null,
  [
    [func_name, func_description, parameters_schema],
    ...
  ]
]
```

单个函数声明三元数组结构如下：
- **下标 `0` (Field 1)**：`name` (`string`)，函数唯一名称；
- **下标 `1` (Field 2)**：`description` (`string | null`)，函数功能说明；
- **下标 `2` (Field 3)**：`parameters` (`list | null`)，参数 Schema 定义（遵循下文 6.3 节 Schema 编码规范）。

### 6.3 结构化 Schema 完整逆向编码表

针对自定义函数声明（Function Declarations）与结构化输出（Response Schema），根据 AI Studio 前端 JS 核心反编译（`_.nm` 与 `dwa`）提取的完整 Protobuf-over-JSON 字段索引如下：

| 数组索引 | Proto 字段号 | 字段名 (Property) | 类型 | 说明与编码规范 |
|---|---|---|---|---|
| **`0`** | Field 1 | `type` | `int` | `1`=string, `2`=number, `3`=integer, `4`=boolean, `5`=array, `6`=object, `7`=null |
| **`1`** | Field 2 | `format` | `string` | 格式修饰符（如 `"date-time"`, `"int64"`） |
| **`2`** | Field 3 | `description` | `string` | **字段语义描述**（向模型传达业务逻辑的关键元数据） |
| **`3`** | Field 4 | `nullable` | `bool` | 是否允许为 null |
| **`4`** | Field 5 | `enum` | `list[str]` | **枚举合法取值数组**（防止模型生成非法入参的核心约束） |
| **`5`** | Field 6 | `items` | `list` | 数组元素 Schema 递归定义（针对 array 类型） |
| **`6`** | Field 7 | `properties` | `list` | 对象属性键值对列表：`[[prop_name, prop_schema], ...]` |
| **`7`** | Field 8 | `required` | `list[str]` | 必填属性名称数组 |
| **`8`** | Field 9 | `minProperties` | `int` | 最小属性数量约束 |
| **`9`** | Field 10 | `maxProperties` | `int` | 最大属性数量约束 |
| **`10`** | Field 11 | `minimum` | `float/int` | 数值下界 |
| **`11`** | Field 12 | `maximum` | `float/int` | 数值上界 |
| **`12`** | Field 13 | `minLength` | `int` | 字符串最小长度 |
| **`13`** | Field 14 | `maxLength` | `int` | 字符串最大长度 |
| **`14`** | Field 15 | `pattern` | `string` | 正则表达式匹配规则 |
| **`15`** | Field 16 | `example` | `list` | 示例值（google.protobuf.Value 编码） |
| **`16`** | Field 17 | `oneOf` | `list` | 独占联合类型定义数组 |
| **`17`** | Field 18 | `anyOf` | `list` | 多态联合类型定义数组 |
| **`18`** | Field 19 | `allOf` | `list` | 交叉继承类型定义数组 |
| **`19`** | Field 20 | `not` | `list` | 取反类型定义 |
| **`20`** | Field 21 | `maxItems` | `int` | 数组最大元素数 |
| **`21`** | Field 22 | `minItems` | `int` | 数组最小元素数 |
| **`22`** | Field 23 | `propertyOrdering` | `list[str]` | 属性在 UI / Prompt 中呈现的确定性声明顺序 |
| **`23`** | Field 24 | `title` | `string` | Schema 标题 / 类名标识 |
| **`24`** | Field 25 | `default` | `list` | 字段默认值（google.protobuf.Value 编码） |
### 6.4 工具控制模式 (ToolConfig)

MakerSuite 协议无独立顶层工具控制字段，Gemini API 的 `toolConfig` 在网关层按语义映射：

| 模式 (Mode) | 语义说明 | 网关处理策略 |
|---|---|---|
| **`AUTO`** | 模型自主决定文本或工具调用 | 保持 `tools` 原样下发 |
| **`NONE`** | 禁用工具调用，强制纯文本输出 | 置空 `tools` 为 `null` |
| **`ANY`** | 强制执行工具调用 | 若配置 `allowedFunctionNames`，在网关层过滤候选工具列表 |
| **`VALIDATED`** | 受限解码验证 | 保持 `tools` 原样下发 |

## 7. 响应报文解析与 Token 使用量 (Response & Usage)

### 7.1 响应 Chunk 容器

Google AI Studio 响应为 Protobuf JSON 数组，每个流式分块或单次响应顶层结构为：

```json
[
  [
    [
      [
        [
          [null, "模型生成的文本内容"]
        ],
        "model"
      ],
      1,
      null,
      "STOP",
      []
    ]
  ],
  null,
  [12, 45, 57, 0, null, null, null, null, null, 18],
  null,
  null,
  null,
  null,
  "response_id_string"
]
```

### 7.2 Candidate 与 Part 字段解析

- `chunk[0][0]`：候选回答对象（Candidate）；
- `candidate[0]`：结构化内容 `[parts_list, role]`；
- `candidate[1]`：FinishReason 代码（`1` 对应 STOP）；
- `candidate[3]`：FinishMessage 说明；
- `candidate[4]`：安全评估结果数组（SafetyRatings）；
- `chunk[7]`：响应唯一标识（Response ID）。

### 7.2.1 函数调用参数 JSPB 映射规范

当候选 Part 为函数调用 (`FunctionCall`) 时，位于 Part 下标 `3` (Field 4) 或下标 `10` (Field 11)。其内部三元组格式为 `[name, args_struct, call_id]`。

`args_struct` 遵循 `google.protobuf.Struct` 映射：
1. **外层容器**：键值对二维列表 `[[key, value_wire], ...]`。当被上游多层容器包裹时（例如 `[[ [...pairs] ]]`），需解包外层单元素容器，但**绝不可破坏键值对本身**；
2. **多态值节点 (`google.protobuf.Value`)**：
   - **NullValue (Field 1, 下标 0)**：`[0]` 解码为 `None`；
   - **NumberValue (Field 2, 下标 1)**：`[null, number]` 解码为浮点数或整数；
   - **StringValue (Field 3, 下标 2)**：`[null, null, "text"]` 解码为字符串；
   - **BoolValue (Field 4, 下标 3)**：`[null, null, null, bool]`，JSPB 常压缩为 `0` (`False`) 与 `1` (`True`)，必须解码为标准布尔值；
   - **StructValue (Field 5, 下标 4)**：`[null, null, null, null, [struct_fields]]` 递归解码为字典；
   - **ListValue (Field 6, 下标 5)**：`[null, null, null, null, null, [items]]` 必须解码为列表。空列表 `[]` 必须严格保持为 `[]`，不可误转为字典；单元素列表必须完整保留单个元素，不可解包其内部字段导致被 null 填充。

### 7.3 Token 使用量 (UsageMetadata)

位于 `chunk[2]` 数组：

| 数组索引 | 统计指标 | 说明 |
|---|---|---|
| **`0`** | `prompt_tokens` | 提示词 Token 消耗量 |
| **`1`** | `visible_completion_tokens` | 可见模型输出 Token 消耗量 |
| **`2`** | `total_tokens` | 请求总 Token 数 |
| **`3`** | `cached_tokens` | 命中的上下文缓存 Token 数 |
| **`4`** | `prompt_tokens_details` | 提示词结构明细 |
| **`9`** | `reasoning_tokens` | **思维链（Thinking）思考 Token 消耗量** |

> [!NOTE]
> 最终对齐 Gemini API 标准时：
> `completion_tokens = visible_completion_tokens + (reasoning_tokens or 0)`

### 7.4 FinishReason 完整状态码映射表

上游 Protobuf 在 Candidate 的 Field 2 (下标 `1`) 返回整数代码，对应 Google API 标准字符串枚举：

| 代码 | 标准枚举值 | 说明 |
|---|---|---|
| `0` | `FINISH_REASON_UNSPECIFIED` | 未指定 |
| `1` | `STOP` | 自然停止或遇到停止序列 |
| `2` | `MAX_TOKENS` | 达到最大输出 Token 上限 |
| `3` | `SAFETY` | 触发安全过滤策略拦截 |
| `4` | `RECITATION` | 触发版权/原文背诵拦截 |
| `5` | `LANGUAGE` | 不支持的语言策略拦截 |
| `6` | `OTHER` | 其他终止原因 |
| `7` | `BLOCKLIST` | 触发禁用词黑名单 |
| `8` | `PROHIBITED_CONTENT` | 触发违规有害内容拦截 |
| `9` | `SPII` | 敏感个人身份信息拦截 |
| `10` | `MALFORMED_FUNCTION_CALL` | 模型生成了非法的函数调用参数 |
| `11` | `IMAGE_SAFETY` | 生成图片触发安全策略 |
| `12` | `UNEXPECTED_TOOL_CALL` | 意外的工具调用 |
| `13` | `TOO_MANY_TOOL_CALLS` | 工具链连续调用超限退出 |
| `14` | `IMAGE_PROHIBITED_CONTENT` | 图片包含违规内容 |
| `15` | `NO_IMAGE` | 预期生图但未生成 |
| `16` | `IMAGE_RECITATION` | 生图版权来源相似度拦截 |
| `17` | `IMAGE_OTHER` | 生图其他未知原因终止 |

AI Studio Web 核心过滤数组：
`hCb = [0, 3, 4, 5, 7, 8, 9, 11, 14, 15, 17]`
前端逻辑中 `1` (`STOP`) 与 `2` (`MAX_TOKENS`) 视为常规完成，其余在 `hCb` 中的代码均触发错误或警告弹窗处理。

---

## 8. 前端逆向新特性与高价值发现

通过对 AI Studio 最新前端代码包（`m=_b.js`）的 AST 深度逆向，发现如下具有高挖掘价值的协议特性与未公开接口：

1. **原生 MCP (Model Context Protocol) 支持**：
   前端 `toolType` 解析中内置 `mcp_server_tool_call`（枚举代码 `6`），表明 Google 正在或已在 AI Studio 底层协议中预留了连接本地/远程 MCP Server 的标准工具通道。
2. **原生文件检索工具 (`file_search`)**：
   工具分支代码中存在 `case 8: return "file_search"` 与 `file_search_call`，区别于传统的代码执行与普通检索，属于针对多文档的大规模知识库检索能力。
3. **语音自定义词汇表 (`customVocabulary`)**：
   GenerationConfig 字段 32（`_.cu` / Field 32）下支持向语音端点下发专属专业术语、专有名词与人名词典，显著提升高精度音频转录/生成的准确度。
4. **音频高级控制标记**：
   - `wordTimestamps`：字级别（Word-level）输出时间戳；
   - `speakerDiarization`：多说话人角色分离与标签标记；
   - `smartTranscription`：去除口语赘字（Filler words）、自动语法重构与口误纠正；
   - `fillerWords`：允许自然停顿与语气词（"hmm", "ahh"）。
5. **视频帧流式抽取服务 (`StreamExtractVideoFrames`)**：
   RPC 服务 `/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/StreamExtractVideoFrames`，用于在后端按时间戳或指定采样率无损切片视频流并回传图片帧。

### 8.6 前端 JS (JSPB) 工具调用参数压缩机制与解包原则

根据对 AI Studio 前端生产包动态逆向分析，Google 前端在处理 `FunctionCall.args` 与 JSON Schema 时存在特定的 JSPB 优化机制：
1. **布尔压缩 (Boolean 0/1 Compression)**：
   在 `dwa` 反序列化器中，`BoolValue` 字段（Field 4）常以整数 `0` 和 `1` 传递以减少传输体积，Python 网关端必须通过 `value[3] in (0, 1)` 强制还原为 `bool`。
2. **Repeated 容器单层扁平化边界**：
   在 `_decode_wire_list` 中，Repeated 字段只允许剥离一层外层数组包装（`len == 1 and not _is_wire_value`），一旦内层为 `_is_wire_value`（以 `None` 或 `0` 开头的 JSPB 数组），必须停止拆包，否则单元素列表（如包含一个问题对象的 `ask` 工具）会被错误展开成 5 元素数组，导致首部填充 4 个 `None`。
3. **Schema 元数据无损注入**：
   下发给 MakerSuite 的 `tools` 必须完整保留字段的 `description` (下标 2) 与 `enum` (下标 4)，否则模型在生成调用参数时失去参数语义与合法枚举选项，极易造成格式错误或参数幻觉。
