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
- [7. 响应报文解析与 Token 使用量 (Response & Usage)](#7-响应报文解析与-token-使用量-response--usage)
  - [7.1 响应 Chunk 容器](#71-响应-chunk-容器)
  - [7.2 Candidate 与 Part 字段解析](#72-candidate-与-part-字段解析)
  - [7.3 Token 使用量 (UsageMetadata)](#73-token-使用量-usagemetadata)

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
