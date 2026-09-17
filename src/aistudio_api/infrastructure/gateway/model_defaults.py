"""Model-specific default behaviors for AI Studio wire requests."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from .wire_types import (
    AistudioImageOutputMode,
    AistudioThinkingConfig,
    ImageOutputType,
    MediaResolution,
    ThinkingLevel,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"
_SAFETY_CATEGORY_ORDER = (
    ("harassment", 7),
    ("hate", 8),
    ("sexually explicit", 9),
    ("dangerous content", 10),
)


@dataclass(frozen=True)
class ModelDefaults:
    is_image_model: bool = False
    default_tools: tuple[str, ...] = ()
    generation_config_defaults: dict[str, object] = field(default_factory=dict)
    clear_generation_config_indexes: tuple[int, ...] = ()
    safety_settings: tuple[tuple[object, ...], ...] | None = None
    disable_safety_settings: bool = False
    specified_fields: frozenset[str] = frozenset()

    def generation_config_overrides(self) -> dict[str, object]:
        return dict(self.generation_config_defaults)

    def safety_settings_overrides(self) -> list[list[object]] | None:
        if self.safety_settings is None:
            return None
        return [list(item) for item in self.safety_settings]

    def merged(self, other: ModelDefaults) -> ModelDefaults:
        merged_generation = dict(self.generation_config_defaults)
        merged_generation.update(other.generation_config_defaults)
        merged_tools = self.default_tools + tuple(
            tool for tool in other.default_tools if tool not in self.default_tools
        )
        merged_indexes = self.clear_generation_config_indexes + tuple(
            idx
            for idx in other.clear_generation_config_indexes
            if idx not in self.clear_generation_config_indexes
        )
        return ModelDefaults(
            is_image_model=self.is_image_model or other.is_image_model,
            default_tools=merged_tools,
            generation_config_defaults=merged_generation,
            clear_generation_config_indexes=merged_indexes,
            safety_settings=other.safety_settings
            if other.safety_settings is not None
            else self.safety_settings,
            disable_safety_settings=self.disable_safety_settings
            or other.disable_safety_settings,
            specified_fields=self.specified_fields | other.specified_fields,
        )

    def overridden(self, other: ModelDefaults) -> ModelDefaults:
        merged_generation = dict(self.generation_config_defaults)
        if "generation_config_defaults" in other.specified_fields:
            merged_generation.update(other.generation_config_defaults)
        return ModelDefaults(
            is_image_model=other.is_image_model
            if "is_image_model" in other.specified_fields
            else self.is_image_model,
            default_tools=other.default_tools
            if "default_tools" in other.specified_fields
            else self.default_tools,
            generation_config_defaults=merged_generation,
            clear_generation_config_indexes=(
                other.clear_generation_config_indexes
                if "clear_generation_config_indexes" in other.specified_fields
                else self.clear_generation_config_indexes
            ),
            safety_settings=other.safety_settings
            if "safety_settings" in other.specified_fields
            else self.safety_settings,
            disable_safety_settings=(
                other.disable_safety_settings
                if "disable_safety_settings" in other.specified_fields
                else self.disable_safety_settings
            ),
            specified_fields=self.specified_fields | other.specified_fields,
        )


@dataclass(frozen=True)
class ModelProfile:
    name: str
    exact: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    contains: tuple[str, ...] = ()
    defaults: ModelDefaults = field(default_factory=ModelDefaults)

    def matches(self, model: str) -> bool:
        return bool(
            (self.exact and model in self.exact)
            or any(model.startswith(prefix) for prefix in self.prefixes)
            or any(token in model for token in self.contains)
        )


def _normalize_model_name(model: str) -> str:
    return model.removeprefix("models/").lower()


def _coerce_thinking_level(value: object) -> ThinkingLevel:
    if isinstance(value, ThinkingLevel):
        return value
    if isinstance(value, int):
        return ThinkingLevel(value)
    return ThinkingLevel[str(value).strip().upper()]


def _coerce_media_resolution(value: object) -> int:
    if isinstance(value, MediaResolution):
        return int(value)
    if isinstance(value, int):
        return value
    return int(MediaResolution[str(value).strip().upper()])


def _coerce_image_output_mode(value: object) -> AistudioImageOutputMode | list[int]:
    if isinstance(value, AistudioImageOutputMode):
        return value
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        output_type = ImageOutputType(
            int(value.get("output_type", ImageOutputType.IMAGE))
        )
        include_text = bool(value.get("include_text"))
        return AistudioImageOutputMode(
            output_type=output_type, include_text=include_text
        )
    label = str(value).strip().lower()
    if label == "text_and_image":
        return AistudioImageOutputMode.text_and_image()
    if label == "image_only":
        return AistudioImageOutputMode.image_only()
    raise ValueError(f"Unsupported image_output_mode: {value!r}")


def _coerce_generation_defaults(raw: dict[str, object] | None) -> dict[str, object]:
    raw = raw or {}
    coerced: dict[str, object] = {}
    for key, value in raw.items():
        if key == "thinking_config" and isinstance(value, dict):
            level = _coerce_thinking_level(value.get("level", ThinkingLevel.HIGH))
            mode = int(value.get("mode", 1))
            coerced[key] = AistudioThinkingConfig(level=level, mode=mode).to_wire()
        elif key == "image_output_mode":
            coerced[key] = _coerce_image_output_mode(value)
        elif key == "media_resolution":
            coerced[key] = _coerce_media_resolution(value)
        else:
            coerced[key] = value
    return coerced


def _normalize_safety_category(name: object) -> str:
    return " ".join(
        str(name).strip().lower().replace("_", " ").replace("-", " ").split()
    )


def _coerce_safety_threshold(value: object) -> int:
    threshold = int(str(value))
    if threshold < 1 or threshold > 5:
        raise ValueError(f"Unsupported safety threshold: {value!r}")
    return threshold


def _coerce_safety_settings(raw: object) -> tuple[tuple[object, ...], ...] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        normalized = {
            _normalize_safety_category(name): _coerce_safety_threshold(value)
            for name, value in raw.items()
        }
        supported = {name for name, _ in _SAFETY_CATEGORY_ORDER}
        unknown = [name for name in normalized if name not in supported]
        if unknown:
            raise ValueError(f"Unsupported safety categories: {', '.join(unknown)}")
        return tuple(
            (None, None, category, normalized[name])
            for name, category in _SAFETY_CATEGORY_ORDER
            if name in normalized
        )
    if isinstance(raw, (list, tuple)):
        settings = []
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) < 4:
                raise ValueError(f"Unsupported safety setting entry: {item!r}")
            settings.append(
                (None, None, int(item[2]), _coerce_safety_threshold(item[3]))
            )
        return tuple(settings)
    raise ValueError(f"Unsupported safety_settings: {raw!r}")


def _defaults_from_mapping(raw: dict[str, object] | None) -> ModelDefaults:
    raw_dict = raw or {}
    raw_default_tools = raw_dict.get("default_tools")
    default_tools_list = (
        raw_default_tools if isinstance(raw_default_tools, (list, tuple)) else ()
    )
    raw_gen_defaults = raw_dict.get("generation_config_defaults")
    gen_defaults = raw_gen_defaults if isinstance(raw_gen_defaults, dict) else None
    raw_clear_indexes = raw_dict.get("clear_generation_config_indexes")
    clear_indexes_list = (
        raw_clear_indexes if isinstance(raw_clear_indexes, (list, tuple)) else ()
    )
    return ModelDefaults(
        is_image_model=bool(raw_dict.get("is_image_model", False)),
        default_tools=tuple(str(x) for x in default_tools_list),
        generation_config_defaults=_coerce_generation_defaults(gen_defaults),
        clear_generation_config_indexes=tuple(int(str(v)) for v in clear_indexes_list),
        safety_settings=_coerce_safety_settings(raw_dict.get("safety_settings")),
        disable_safety_settings=bool(raw_dict.get("disable_safety_settings", False)),
        specified_fields=frozenset(raw_dict.keys()),
    )


def _profile_from_mapping(raw: dict[str, object]) -> ModelProfile:
    raw_match = raw.get("match")
    match: dict[str, object] = raw_match if isinstance(raw_match, dict) else {}
    raw_exact = match.get("exact")
    exact_list = raw_exact if isinstance(raw_exact, (list, tuple)) else ()
    raw_prefixes = match.get("prefixes")
    prefixes_list = raw_prefixes if isinstance(raw_prefixes, (list, tuple)) else ()
    raw_contains = match.get("contains")
    contains_list = raw_contains if isinstance(raw_contains, (list, tuple)) else ()
    return ModelProfile(
        name=str(raw.get("name", "unnamed")),
        exact=tuple(_normalize_model_name(str(v)) for v in exact_list),
        prefixes=tuple(_normalize_model_name(str(v)) for v in prefixes_list),
        contains=tuple(str(v).strip().lower() for v in contains_list),
        defaults=_defaults_from_mapping(raw),
    )


def _default_config() -> dict[str, object]:
    return {
        "model_defaults": {
            "profiles": [
                {
                    "name": "image_models",
                    "match": {"contains": ["image"]},
                    "is_image_model": True,
                    "generation_config_defaults": {
                        "response_mime_type": None,
                        "image_output_mode": "image_only",
                        "thinking_config": {"level": "MINIMAL", "mode": 1},
                    },
                    "clear_generation_config_indexes": [7, 13, 17],
                    "disable_safety_settings": True,
                },
                {
                    "name": "gemma_models",
                    "match": {"prefixes": ["gemma-"]},
                    "default_tools": ["google_search"],
                    "safety_settings": {
                        "Harassment": 5,
                        "Hate": 5,
                        "Sexually Explicit": 5,
                        "Dangerous Content": 5,
                    },
                },
                {
                    "name": "gemini_models",
                    "match": {"prefixes": ["gemini-"]},
                    "safety_settings": {
                        "Harassment": 5,
                        "Hate": 5,
                        "Sexually Explicit": 5,
                        "Dangerous Content": 5,
                    },
                },
            ],
            "models": {},
        }
    }


def _resolve_config_path(config_path: str | os.PathLike[str] | None) -> Path:
    if config_path is not None:
        return Path(config_path)
    override = os.getenv("AISTUDIO_CONFIG_FILE")
    if override:
        return Path(override)
    return _DEFAULT_CONFIG_PATH


def _load_yaml_config(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return _default_config()
    loaded = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(loaded, dict):
        return _default_config()
    merged: dict[str, object] = _default_config()
    merged.update(loaded)
    raw_merged_defaults = merged.get("model_defaults")
    raw_loaded_defaults = loaded.get("model_defaults")
    if isinstance(raw_merged_defaults, dict) and isinstance(raw_loaded_defaults, dict):
        def_config = _default_config()
        def_md = def_config.get("model_defaults")
        merged_model_defaults = dict(def_md if isinstance(def_md, dict) else {})
        merged_model_defaults.update(raw_loaded_defaults)
        merged["model_defaults"] = merged_model_defaults
    return merged


@lru_cache(maxsize=8)
def _compiled_profiles(config_path: str) -> tuple[ModelProfile, ...]:
    config = _load_yaml_config(Path(config_path))
    raw_md = config.get("model_defaults")
    model_defaults = raw_md if isinstance(raw_md, dict) else {}
    raw_profiles = model_defaults.get("profiles")
    profiles_list = raw_profiles if isinstance(raw_profiles, list) else []
    return tuple(
        _profile_from_mapping(item) for item in profiles_list if isinstance(item, dict)
    )


@lru_cache(maxsize=8)
def _compiled_model_overrides(config_path: str) -> dict[str, ModelDefaults]:
    config = _load_yaml_config(Path(config_path))
    raw_md = config.get("model_defaults")
    model_defaults = raw_md if isinstance(raw_md, dict) else {}
    raw_models = model_defaults.get("models")
    models_dict = raw_models if isinstance(raw_models, dict) else {}
    overrides: dict[str, ModelDefaults] = {}
    for model_name, raw_defaults in models_dict.items():
        overrides[_normalize_model_name(str(model_name))] = _defaults_from_mapping(
            raw_defaults if isinstance(raw_defaults, dict) else {}
        )
    return overrides


def resolve_model_defaults(
    model: str, *, config_path: str | os.PathLike[str] | None = None
) -> ModelDefaults:
    normalized = _normalize_model_name(model)
    resolved_path = str(_resolve_config_path(config_path))
    profiles = _compiled_profiles(resolved_path)
    resolved = ModelDefaults()
    for profile in profiles:
        if profile.matches(normalized):
            resolved = resolved.merged(profile.defaults)
    override = _compiled_model_overrides(resolved_path).get(normalized)
    if override is not None:
        resolved = resolved.overridden(override)

    if resolved.is_image_model:
        allowed = {"google_search", "image_search", "google_search_and_image_search"}
        filtered = tuple(t for t in resolved.default_tools if t in allowed)
        if filtered != resolved.default_tools:
            from dataclasses import replace

            resolved = replace(resolved, default_tools=filtered)

    return resolved


def get_configured_api_key_items(
    config_path: str | os.PathLike[str] | None = None,
) -> list[dict[str, str]]:
    """获取 config.yaml 中配置的结构化 API Key 列表（含备注名与创建时间）。"""
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    raw_keys = config.get("api_keys")
    items: list[dict[str, str]] = []
    if isinstance(raw_keys, list):
        for k in raw_keys:
            if isinstance(k, dict):
                key_val = str(k.get("key") or "").strip()
                name_val = str(k.get("name") or "API Key").strip()
                created_val = str(k.get("created_at") or "").strip()
                if key_val:
                    items.append(
                        {"key": key_val, "name": name_val, "created_at": created_val}
                    )
            elif isinstance(k, str) and k.strip():
                items.append({"key": k.strip(), "name": "API Key", "created_at": ""})
    elif isinstance(raw_keys, str):
        for line in raw_keys.splitlines():
            for part in line.split(","):
                s = part.strip()
                if s:
                    items.append({"key": s, "name": "API Key", "created_at": ""})
    return items


def get_configured_api_keys(
    config_path: str | os.PathLike[str] | None = None,
) -> frozenset[str]:
    """获取 config.yaml 中配置的所有有效 API Key 集合。"""
    items = get_configured_api_key_items(config_path)
    return frozenset(item["key"] for item in items if item.get("key"))
