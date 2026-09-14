export interface GeminiModel {
  name: string
  version?: string
  displayName: string
  description: string
  inputTokenLimit: number
  outputTokenLimit: number
  supportedGenerationMethods: string[]
  temperature?: number
  topP?: number
  topK?: number
}

export interface GeminiModelListResponse {
  models: GeminiModel[]
}
