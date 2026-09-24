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
  - [6.2 结构化 Schema 编码与类型字典](#62-结构化-schema-编码与类型字典)
  - [6.3 工具控制模式 (ToolConfig)](#63-工具控制模式-toolconfig)
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
| **`0`** | Field 1 | `model` | `string` | 目标模型路径，如 `"models/gemini-3.7-flash"` |
| **`1`** | Field 2 | `contents` | `list` | 结构化对话轮次数组（`AistudioContent` 列表） |
| **`2`** | Field 3 | `safety_settings` | `list \| null` | 安全拦截阈值数组 |
| **`3`** | Field 4 | `generation_config` | `list` | 生成控制参数数组（见第 4 节） |
| **`4`** | Field 5 | `snapshot` | `string` | **BotGuard WAA 快照签名 Token**（以 `!` 开头） |
| **`5`** | Field 6 | `system_instruction` | `list \| null` | 系统提示词内容（格式同 Content） |
| **`6`** | Field 7 | `tools` | `list \| null` | 工具声明列表（内置工具与自定义函数） |
| **`7`** | Field 8 | `tool_config` | `list \| null` | 工具调用控制模式 |
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

每个 Part 是一个稀疏数组，根据其数据类型的不同占用不同的下标位置：

| Part 类型 | 数组结构映射 | 说明 |
|---|---|---|
| **文本 (Text)** | `[null, "文本内容"]` | 普通文本块 |
| **思考文本 (Thinking)** | `[null, "思考链内容", ..., ..., ..., ..., ..., ..., ..., ..., ..., null, 1]` | 下标 `12` 为 `1` 时标记该文本为思维链 |
| **内联多模态 (InlineData)** | `[null, null, ["image/jpeg", "base64_data"]]` | 下标 `2` 为 `[mimeType, base64]` |
| **资源引用 (FileData)** | `[null, null, null, null, null, ["file_id"]]` | 下标 `5` 为 `[file_id]` |
| **工具调用 (FunctionCall)** | `[..., ..., ..., [name, args, call_id]]` | 请求中下标 `10`（部分响应中下标 `3`） |
| **工具结果 (FunctionResponse)** | `[..., ..., ..., ..., [name, response, call_id]]` | 请求中下标 `11`（部分响应中下标 `4`） |
| **思维签名 (ThoughtSignature)** | 位于任意 Part 的下标 `14` (`part[14] = "signature"`) | Google 官方校验思维链真实性的签名字符串 |

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

位于顶层数组下标 `6`。

### 6.1 内置工具模板

| 工具名称 | Wire 数组表示 |
|---|---|
| **代码执行 (Code Execution)** | `[[]]` |
| **网页搜索 (Google Search)** | `[null, null, null, [null, [[]]]]` |
| **图片搜索 (Google Image Search)** | `[null, null, null, [null, [null, []]]]` |
| **网页 + 图片联合搜索** | `[null, null, null, [null, [[], []]]]` |
| **谷歌地图 (Google Maps)** | `[null, null, null, null, null, null, null, null, null, null, []]` |
| **URL 上下文提取 (URL Context)** | `[null, null, null, null, null, null, null, []]` |

### 6.2 结构化 Schema 编码与类型字典

针对自定义函数声明（Function Declarations）与结构化输出（Response Schema），字段类型码映射如下：

| 数据类型 (Type) | 类型码 (Type Code) | 结构展开规则 |
|---|---|---|
| `string` | `1` | `[1]` |
| `number` / `float` | `2` | `[2]` |
| `integer` / `int` | `3` | `[3]` |
| `boolean` / `bool` | `4` | `[4]` |
| `array` | `5` | `[5, null, null, null, null, items_schema]` (下标 `5` 为元素 Schema) |
| `object` | `6` | `[6, null, null, null, null, null, properties_list, required_list, ..., property_ordering]` |

- **对象属性列表 (Index 6)**：`[[prop_name, prop_schema], ...]`
- **必填属性列表 (Index 7)**：`["field1", "field2"]`
- **属性声明顺序 (Index 22)**：`["field1", "field2"]`


### 6.3 工具控制模式 (ToolConfig)

位于顶层数组下标 `7` (Proto Field 8)。

内部为 `ToolConfig` 消息序列化数组：

```json
[
  null,
  [mode_code, ["allowed_func_1", "allowed_func_2"]],
  include_server_side_tool_invocations
]
```

| 下标 | Proto 字段 | 名称 | 说明 |
|---|---|---|---|
| `0` | Field 1 | `retrieval_config` | 知识检索配置，未配置时为 `null` |
| `1` | Field 2 | `function_calling_config` | 函数调用模式控制数组：`[mode, allowed_function_names]` |
| `2` | Field 3 | `include_server_side_tool_invocations` | `bool \| null`，服务端工具回显控制 |

#### 函数调用模式码 (Mode Code)
- `0`：`MODE_UNSPECIFIED`
- `1`：`AUTO` (自动模型决策)
- `2`：`ANY` (强制必须调用工具)
- `3`：`NONE` (禁止调用工具)
- `4`：`VALIDATED` (验证模式)

> [!CRITICAL]
> `function_calling_config` 属于 Protobuf Field 2，必须下发在下标 `1`（前置 `null` 占位符 `[null, fcc_wire]`）。若错误编码为 `[fcc_wire]`，Google 后端会按 Field 1 (`retrieval_config`) 反序列化导致类型不匹配报错。
---

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
