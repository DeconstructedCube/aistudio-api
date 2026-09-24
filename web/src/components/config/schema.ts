/**
 * aistudio-api 配置项规范与描述体系 (Configuration Specification & Schema)
 * 集中管理所有配置字段的元数据、类型、说明、推荐值与业务原理。
 */

export interface FieldOption {
  value: string | number | boolean | null
  label: string
  description?: string
  badge?: string
  recommended?: boolean
}

export interface FieldDefinition {
  key: string
  title: string
  category: 'profiles' | 'match' | 'tools' | 'generation' | 'safety' | 'overrides' | 'system'
  type: 'string' | 'number' | 'boolean' | 'select' | 'tags' | 'safety_matrix' | 'indexes'
  description: string
  explanation: string
  defaultValue?: unknown
  recommendedValue?: unknown
  options?: FieldOption[]
  suggestions?: { label: string; value: string | number; description?: string }[]
  tags?: string[]
}
export const TOOL_SUGGESTIONS = [
  { label: '网页+图片搜索', value: 'google_search_and_image_search', description: '适用于生图模型 (支持搜索参考图)' },
  { label: 'Google 网页搜索', value: 'google_search', description: '常规文本问答联网搜索' },
  { label: '仅图片搜索', value: 'image_search', description: '仅用于图像检索' },
  { label: '代码执行 (Python)', value: 'code_execution', description: '文本模型 Python 计算与绘图' },
  { label: 'Google 地图', value: 'google_maps', description: '地理与地图位置检索' },
  { label: '网页抓取与上下文', value: 'url_context', description: '提取 URL 内容上下文' },
]

export const IMAGE_MODE_OPTIONS = [
  { value: 'image_only', label: 'image_only (仅输出图片)', description: '推荐：适合第三方画图客户端' },
  { value: 'text_and_image', label: 'text_and_image (文字与图片混排)', description: '包含提示词分析与图片实体' },
  { value: null, label: '不显式指定 (Null)', description: '遵循上游默认' },
]

export const THINKING_LEVEL_OPTIONS = [
  { value: 'MINIMAL', label: 'MINIMAL (极速 / 最小思考)', description: '生图或短回复推荐' },
  { value: 'LOW', label: 'LOW (轻度思考)', description: '兼顾速度与简单推理' },
  { value: 'MEDIUM', label: 'MEDIUM (标准中度思考)', description: '平衡模式' },
  { value: 'HIGH', label: 'HIGH (深度慢思考)', description: '推理模型默认' },
  { value: null, label: '不显式指定 (Null)', description: '遵循模型默认' },
]

export const MEDIA_RESOLUTION_OPTIONS = [
  { value: 'HIGH', label: 'HIGH (高分辨率 / 1024+)', description: '原图细节，多模态推荐' },
  { value: 'MEDIUM', label: 'MEDIUM (标准清晰度)', description: '标准分辨率' },
  { value: 'LOW', label: 'LOW (低画质缩略)', description: '节省 Token' },
  { value: null, label: '默认 (Null)', description: '遵循上游默认' },
]


export const CONFIG_SCHEMA: Record<string, FieldDefinition> = {
  'profile.name': {
    key: 'profile.name',
    title: '规则分组标识 (Profile Name)',
    category: 'profiles',
    type: 'string',
    description: '该模型分组规则的唯一英文标识名称。',
    explanation: '用于在配置中区分不同的模型分类（如 image_models, gemini_models, gemma_models）。不可重名。',
    defaultValue: 'custom_profile',
    tags: ['基础项'],
  },
  'profile.is_image_model': {
    key: 'profile.is_image_model',
    title: '生图模型标识 (is_image_model)',
    category: 'profiles',
    type: 'boolean',
    description: '是否将命中此规则的模型视为生图（Image Generation）模型。',
    explanation: '开启后系统会自动切换至生图专用请求编解码管线，包含多模态 Base64 图片解析提取，并强制过滤不兼容的文本工具。',
    defaultValue: false,
    recommendedValue: true,
    tags: ['生图模型', '核心开关'],
  },
  'match.contains': {
    key: 'match.contains',
    title: '包含关键词匹配 (contains)',
    category: 'match',
    type: 'tags',
    description: '模型名称中只要包含指定子串，即命中该规则组。',
    explanation: '例如配置 ["image"] 时，凡是包含 image 的模型（如 gemini-3.1-flash-image-preview）都会自动应用该组配置。',
    defaultValue: [],
    suggestions: [
      { label: 'image', value: 'image', description: '匹配所有带 image 的生图模型' },
      { label: 'preview', value: 'preview', description: '匹配所有预览版模型' },
      { label: 'thinking', value: 'thinking', description: '匹配思考模型' },
    ],
    tags: ['匹配规则'],
  },
  'match.prefixes': {
    key: 'match.prefixes',
    title: '模型前缀匹配 (prefixes)',
    category: 'match',
    type: 'tags',
    description: '模型名称以指定前缀开头时命中此规则组。',
    explanation: '例如配置 ["gemini-", "gemma-"]，系统会自动匹配该系列的所有模型，方便大类统一设置。',
    defaultValue: [],
    suggestions: [
      { label: 'gemini-', value: 'gemini-', description: 'Gemini 系列通用模型' },
      { label: 'gemma-', value: 'gemma-', description: 'Gemma 开源系列模型' },
      { label: 'imagen-', value: 'imagen-', description: 'Imagen 图像生成系列' },
    ],
    tags: ['匹配规则'],
  },
  'match.exact': {
    key: 'match.exact',
    title: '精确模型名匹配 (exact)',
    category: 'match',
    type: 'tags',
    description: '精确匹配特定模型全称（忽略 models/ 前缀与大小写）。',
    explanation: '适合对特定版本模型进行精确指定（如 gemini-3.7-flash）。',
    defaultValue: [],
    tags: ['匹配规则'],
  },
  'match.rules': {
    key: 'match.rules',
    title: '模型名称匹配规则 (Match Rules)',
    category: 'match',
    type: 'tags',
    description: '系统按 contains、prefixes、exact 顺序依次检测模型名，命中后自动应用该组配置。',
    explanation: '支持前缀匹配（如 gemini-）、关键词包含（如 image）与全称精确匹配。',
    defaultValue: {},
    tags: ['匹配规则'],
  },
  'profile.default_tools': {
    key: 'profile.default_tools',
    title: '默认内置工具 (default_tools)',
    category: 'tools',
    type: 'tags',
    description: '客户端调用未显式传 tools 参数时，系统默认自动挂载的 Google 内置工具。',
    explanation: 'Google AI Studio 原生支持搜索、代码执行等内置工具。注意生图模型仅支持 google_search_and_image_search / google_search / image_search。',
    defaultValue: ['google_search'],
    suggestions: [
      { label: '网页+图片搜索', value: 'google_search_and_image_search', description: '生图模型推荐 (支持搜索参考图)' },
      { label: 'Google 网页搜索', value: 'google_search', description: '常规文本问答联网搜索' },
      { label: '仅图片搜索', value: 'image_search', description: '仅用于图像检索' },
      { label: '代码执行 (Python)', value: 'code_execution', description: '文本模型 Python 计算与绘图' },
      { label: 'Google 地图', value: 'google_maps', description: '地理位置与地图检索' },
      { label: '网页抓取', value: 'url_context', description: '提取 URL 内容上下文' },
    ],
    tags: ['工具能力', '联网搜索'],
  },
  'generation.image_output_mode': {
    key: 'generation.image_output_mode',
    title: '生图输出模式 (image_output_mode)',
    category: 'generation',
    type: 'select',
    description: '控制生图模型生成内容的返回结构。',
    explanation: 'image_only 会过滤模型废话只输出图片；text_and_image 则同时保留模型生成图片的解说文本与图片实体。',
    defaultValue: 'image_only',
    recommendedValue: 'image_only',
    options: [
      { value: 'image_only', label: 'image_only (仅输出图片)', description: '推荐：适合第三方画图客户端', recommended: true },
      { value: 'text_and_image', label: 'text_and_image (文字与图片混排)', description: '包含提示词分析与图片' },
      { value: null, label: '上游默认 (Null)', description: '不干预上游默认行为' },
    ],
    tags: ['生图模型', '响应格式'],
  },
  'generation.thinking_level': {
    key: 'generation.thinking_level',
    title: '思考强度等级 (thinking_level)',
    category: 'generation',
    type: 'select',
    description: '控制 Gemini 2.0 / 3.0 系列模型的思考过程强度。',
    explanation: '生图模型推荐 MINIMAL 避免生成冗长慢思考；深度数学或代码推理推荐 HIGH。',
    defaultValue: 'HIGH',
    recommendedValue: 'MINIMAL',
    options: [
      { value: 'MINIMAL', label: 'MINIMAL (极速 / 最小思考)', description: '生图与即时问答推荐', recommended: true },
      { value: 'LOW', label: 'LOW (轻度思考)', description: '兼顾速度与简单推理' },
      { value: 'MEDIUM', label: 'MEDIUM (标准中度思考)', description: '平衡模式' },
      { value: 'HIGH', label: 'HIGH (深度慢思考)', description: '复杂逻辑/算法推导' },
    ],
    tags: ['思考模型', '推理速度'],
  },
  'generation.media_resolution': {
    key: 'generation.media_resolution',
    title: '多模态媒体分辨率 (media_resolution)',
    category: 'generation',
    type: 'select',
    description: '控制客户端上传图片/视频进行多模态分析时的图片压缩率。',
    explanation: 'HIGH 能看清高清图片细节与文字 OCR，但消耗 Token 略多。',
    defaultValue: null,
    options: [
      { value: 'HIGH', label: 'HIGH (高分辨率 / 原图细节)', recommended: true },
      { value: 'MEDIUM', label: 'MEDIUM (标准分辨率)' },
      { value: 'LOW', label: 'LOW (低画质缩略 / 节省 Token)' },
      { value: null, label: '默认 (Null)' },
    ],
    tags: ['多模态', '视觉分析'],
  },
  'generation.clear_indexes': {
    key: 'generation.clear_indexes',
    title: '清空 generation_config 特殊下标 (clear_indexes)',
    category: 'generation',
    type: 'indexes',
    description: '请求发送给 Google 前，从内部 wire 数组中强制清空的字段下标。',
    explanation: 'Google 部分生图模型若携带下标 7, 13, 17 会报错 400 Bad Request，清空这些下标可确保生图请求 100% 成功。',
    defaultValue: [7, 13, 17],
    recommendedValue: [7, 13, 17],
    tags: ['高级项', '生图兼容'],
  },
  'generation.defaults': {
    key: 'generation.defaults',
    title: '生成参数默认值 (Generation Config Defaults)',
    category: 'generation',
    type: 'select',
    description: '映射至 Google Wire 请求中的 generation_config 结构，控制思考深度、生图模式与多模态分辨率。',
    explanation: '可为整组或单个模型定义专用的生成与多模态配置，下发请求时自动注入。',
    defaultValue: {},
    tags: ['生成配置'],
  },
  'safety.disable_safety_settings': {
    key: 'safety.disable_safety_settings',
    title: '完全禁用安全规则下发 (disable_safety_settings)',
    category: 'safety',
    type: 'boolean',
    description: '请求中完全不下发 safety_settings 数组。',
    explanation: '生图模型下发 safety_settings 容易触发 Google 严格审查并报错，开启此项可避免误拦截。',
    defaultValue: false,
    recommendedValue: true,
    tags: ['安全策略', '生图推荐'],
  },
  'safety.settings': {
    key: 'safety.settings',
    title: '安全过滤拦截阈值 (Safety Settings)',
    category: 'safety',
    type: 'safety_matrix',
    description: '针对 4 类敏感内容（骚扰、仇恨、色情、危险）配置拦截等级（1: 最严 ~ 5: 关闭拦截）。',
    explanation: '反向代理推荐设置为 5 (BLOCK_NONE)，即完全关闭拦截，将内容审核权交由下游或用户自身决定，避免正常技术文本被误拦。',
    defaultValue: { Harassment: 5, Hate: 5, 'Sexually Explicit': 5, 'Dangerous Content': 5 },
    recommendedValue: { Harassment: 5, Hate: 5, 'Sexually Explicit': 5, 'Dangerous Content': 5 },
    tags: ['安全策略', '防拦截'],
  },
  'profile.drop_unsupported_params': {
    key: 'profile.drop_unsupported_params',
    title: '自动丢弃不支持参数 (drop_unsupported_params)',
    category: 'safety',
    type: 'boolean',
    description: '是否自动丢弃下游客户端传入的未知安全类别（如 HARM_CATEGORY_CIVIC_INTEGRITY）或该模型不支持的内置工具与高级参数。',
    explanation: '部分第三方 SDK 或客户端（如 Open-WebUI、Cherry Studio）默认携带官方完整安全类别或不兼容的工具，开启此项后系统会自动过滤多余/未知参数，防止 Google 上游返回 400 INVALID_ARGUMENT 阻断请求。',
    defaultValue: false,
    recommendedValue: true,
    tags: ['兼容性', '容错', '防拦截'],
  },
  'system.drop_unsupported_params': {
    key: 'system.drop_unsupported_params',
    title: '全局丢弃不支持参数 (drop_unsupported_params)',
    category: 'system',
    type: 'boolean',
    description: '在全局维度自动丢弃下游客户端传入的未知安全类别（如 CIVIC_INTEGRITY）或模型不兼容工具。',
    explanation: '当特定模型或 Profile 未单独指定 drop_unsupported_params 时，默认以此全局设置为准，避免上游 400 报错。',
    defaultValue: false,
    recommendedValue: true,
    tags: ['全局配置', '防拦截'],
  },
  'model.override': {
    key: 'model.override',
    title: '单模型精确覆盖规则 (Model Override)',
    category: 'overrides',
    type: 'string',
    description: '针对特定具体模型（如 gemini-2.0-flash）精确覆盖规则，优先级高于 Profiles 分组规则。',
    explanation: '支持单独配置生图模式、思考强度、多模态清晰度、专用工具集与独立安全规则。',
    defaultValue: {},
    tags: ['精确覆盖'],
  },
}

export const SCHEMA_CATEGORIES = [
  { key: 'profiles', label: '模型分组规则 (Profiles)' },
  { key: 'match', label: '模型名称匹配 (Match Rules)' },
  { key: 'tools', label: '内置工具配置 (Default Tools)' },
  { key: 'generation', label: '生成与思考参数 (Generation & Thinking)' },
  { key: 'safety', label: '安全拦截等级 (Safety Filters)' },
]
