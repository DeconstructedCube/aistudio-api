"""HTTP request schemas."""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field, field_validator


class GeminiInlineData(BaseModel):
    mimeType: str = Field(validation_alias=AliasChoices("mimeType", "mime_type"))
    data: str


class GeminiFileData(BaseModel):
    mimeType: str | None = Field(
        default=None, validation_alias=AliasChoices("mimeType", "mime_type")
    )
    fileUri: str = Field(validation_alias=AliasChoices("fileUri", "file_uri"))


class GeminiFunctionCall(BaseModel):
    name: str
    args: dict[str, object] | list[object] | object | None = Field(
        default=None, validation_alias=AliasChoices("args", "arguments")
    )
    id: str | None = None


class GeminiFunctionResponse(BaseModel):
    name: str
    response: dict[str, object] | list[object] | object | None = Field(
        default=None,
        validation_alias=AliasChoices("response", "content", "output"),
    )
    id: str | None = None
    parts: list[GeminiPart] | None = None


class GeminiExecutableCode(BaseModel):
    code: str
    language: str = Field(default="PYTHON")
    id: str | None = None


class GeminiCodeExecutionResult(BaseModel):
    outcome: str
    output: str
    id: str | None = None


class GeminiVideoMetadata(BaseModel):
    startOffset: str | None = Field(
        default=None, validation_alias=AliasChoices("startOffset", "start_offset")
    )
    endOffset: str | None = Field(
        default=None, validation_alias=AliasChoices("endOffset", "end_offset")
    )
    fps: float | None = None


class GeminiSpeechMetadata(BaseModel):
    speaker: str | None = None
    style: str | None = None


class GeminiPart(BaseModel):
    text: str | None = None
    inlineData: GeminiInlineData | None = Field(
        default=None, validation_alias=AliasChoices("inlineData", "inline_data")
    )
    fileData: GeminiFileData | None = Field(
        default=None, validation_alias=AliasChoices("fileData", "file_data")
    )
    videoMetadata: GeminiVideoMetadata | None = Field(
        default=None, validation_alias=AliasChoices("videoMetadata", "video_metadata")
    )
    speechMetadata: GeminiSpeechMetadata | None = Field(
        default=None, validation_alias=AliasChoices("speechMetadata", "speech_metadata")
    )
    thought: bool | None = None
    thoughtSignature: str | None = Field(
        default=None,
        validation_alias=AliasChoices("thoughtSignature", "thought_signature"),
    )
    functionCall: GeminiFunctionCall | None = Field(
        default=None, validation_alias=AliasChoices("functionCall", "function_call")
    )
    functionResponse: GeminiFunctionResponse | None = Field(
        default=None,
        validation_alias=AliasChoices("functionResponse", "function_response"),
    )
    executableCode: GeminiExecutableCode | None = Field(
        default=None,
        validation_alias=AliasChoices("executableCode", "executable_code"),
    )
    codeExecutionResult: GeminiCodeExecutionResult | None = Field(
        default=None,
        validation_alias=AliasChoices("codeExecutionResult", "code_execution_result"),
    )


GeminiFunctionResponse.model_rebuild()


class GeminiContent(BaseModel):
    role: str | None = None
    parts: list[GeminiPart]


class GeminiTool(BaseModel):
    codeExecution: dict[str, object] | None = Field(
        default=None, validation_alias=AliasChoices("codeExecution", "code_execution")
    )
    googleSearch: dict[str, object] | None = Field(
        default=None, validation_alias=AliasChoices("googleSearch", "google_search")
    )
    googleSearchRetrieval: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "googleSearchRetrieval", "google_search_retrieval"
        ),
    )
    googleMaps: dict[str, object] | None = Field(
        default=None, validation_alias=AliasChoices("googleMaps", "google_maps")
    )
    urlContext: dict[str, object] | None = Field(
        default=None, validation_alias=AliasChoices("urlContext", "url_context")
    )
    functionDeclarations: list[dict[str, object]] | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "functionDeclarations",
            "function_declarations",
            "functionDeclaration",
            "function_declaration",
        ),
    )

    @field_validator("functionDeclarations", mode="before")
    @classmethod
    def _coerce_function_declarations(cls, v: object) -> object:
        if isinstance(v, dict):
            return [v]
        return v


class GeminiPrebuiltVoiceConfig(BaseModel):
    voiceName: str | None = Field(
        default=None, validation_alias=AliasChoices("voiceName", "voice_name")
    )


class GeminiVoiceConfig(BaseModel):
    prebuiltVoiceConfig: GeminiPrebuiltVoiceConfig | None = Field(
        default=None,
        validation_alias=AliasChoices("prebuiltVoiceConfig", "prebuilt_voice_config"),
    )
    voice: str | None = None


class GeminiSpeakerVoiceConfig(BaseModel):
    speaker: str
    voiceConfig: GeminiVoiceConfig = Field(
        validation_alias=AliasChoices("voiceConfig", "voice_config")
    )


class GeminiMultiSpeakerVoiceConfig(BaseModel):
    speakerVoiceConfigs: list[GeminiSpeakerVoiceConfig] = Field(
        validation_alias=AliasChoices("speakerVoiceConfigs", "speaker_voice_configs")
    )
    mode: str | None = None


class GeminiSpeechConfig(BaseModel):
    voiceConfig: GeminiVoiceConfig | None = Field(
        default=None, validation_alias=AliasChoices("voiceConfig", "voice_config")
    )
    languageCode: str | None = Field(
        default=None, validation_alias=AliasChoices("languageCode", "language_code")
    )
    multiSpeakerVoiceConfig: GeminiMultiSpeakerVoiceConfig | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "multiSpeakerVoiceConfig", "multi_speaker_voice_config"
        ),
    )


class GeminiVideoConfig(BaseModel):
    aspectRatio: str | None = Field(
        default=None, validation_alias=AliasChoices("aspectRatio", "aspect_ratio")
    )
    duration: str | None = None
    resolution: str | None = None
    task: str | None = None


class GeminiGenerationConfig(BaseModel):
    stopSequences: list[str] | None = Field(
        default=None, validation_alias=AliasChoices("stopSequences", "stop_sequences")
    )
    temperature: float | None = None
    topP: float | None = Field(
        default=None, validation_alias=AliasChoices("topP", "top_p")
    )
    topK: int | None = Field(
        default=None, validation_alias=AliasChoices("topK", "top_k")
    )
    maxOutputTokens: int | None = Field(
        default=None,
        validation_alias=AliasChoices("maxOutputTokens", "max_output_tokens"),
    )
    responseModalities: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("responseModalities", "response_modalities"),
    )
    responseMimeType: str | None = Field(
        default=None,
        validation_alias=AliasChoices("responseMimeType", "response_mime_type"),
    )
    responseSchema: list[object] | dict[str, object] | None = Field(
        default=None, validation_alias=AliasChoices("responseSchema", "response_schema")
    )
    presencePenalty: float | None = Field(
        default=None,
        validation_alias=AliasChoices("presencePenalty", "presence_penalty"),
    )
    frequencyPenalty: float | None = Field(
        default=None,
        validation_alias=AliasChoices("frequencyPenalty", "frequency_penalty"),
    )
    responseLogprobs: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("responseLogprobs", "response_logprobs"),
    )
    logprobs: int | None = None
    seed: int | None = None
    mediaResolution: list[object] | int | str | None = Field(
        default=None,
        validation_alias=AliasChoices("mediaResolution", "media_resolution"),
    )
    thinkingConfig: list[object] | dict[str, object] | None = Field(
        default=None, validation_alias=AliasChoices("thinkingConfig", "thinking_config")
    )
    reasoningEffort: str | None = Field(
        default=None,
        validation_alias=AliasChoices("reasoningEffort", "reasoning_effort"),
    )
    imageConfig: dict[str, object] | None = Field(
        default=None, validation_alias=AliasChoices("imageConfig", "image_config")
    )
    speechConfig: GeminiSpeechConfig | dict[str, object] | None = Field(
        default=None, validation_alias=AliasChoices("speechConfig", "speech_config")
    )
    videoConfig: GeminiVideoConfig | dict[str, object] | None = Field(
        default=None, validation_alias=AliasChoices("videoConfig", "video_config")
    )


class GeminiSafetySetting(BaseModel):
    category: str
    threshold: str


class GeminiGenerateContentRequest(BaseModel):
    contents: list[GeminiContent]
    systemInstruction: GeminiContent | None = Field(
        default=None,
        validation_alias=AliasChoices("systemInstruction", "system_instruction"),
    )
    tools: list[GeminiTool] | None = None
    generationConfig: GeminiGenerationConfig | None = Field(
        default=None,
        validation_alias=AliasChoices("generationConfig", "generation_config"),
    )
    safetySettings: list[GeminiSafetySetting] | None = Field(
        default=None, validation_alias=AliasChoices("safetySettings", "safety_settings")
    )
    toolConfig: dict[str, object] | None = Field(
        default=None, validation_alias=AliasChoices("toolConfig", "tool_config")
    )
    reasoningEffort: str | None = Field(
        default=None,
        validation_alias=AliasChoices("reasoningEffort", "reasoning_effort"),
    )
