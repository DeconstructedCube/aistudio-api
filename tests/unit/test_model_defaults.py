from pathlib import Path

from aistudio_api.infrastructure.gateway.model_defaults import (
    invalidate_config_cache,
    resolve_model_defaults,
    resolve_tier_quota_limits,
    validate_config_sections,
)
from aistudio_api.infrastructure.gateway.model_discovery import ModelDiscoveryService
from aistudio_api.infrastructure.gateway.wire_types import AistudioImageOutputMode


def test_resolve_model_defaults_uses_repo_config_for_gemma():
    defaults = resolve_model_defaults("models/gemma-4-31b-it")

    assert defaults.default_tools == ()
    assert defaults.is_image_model is False
    assert defaults.safety_settings == (
        (None, None, 7, 5),
        (None, None, 8, 5),
        (None, None, 9, 5),
        (None, None, 10, 5),
    )


def test_resolve_model_defaults_merges_yaml_profiles(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model_defaults:
  profiles:
    - name: gemini_models
      match:
        prefixes: [gemini-]
      generation_config_defaults:
        response_mime_type: application/json
    - name: image_models
      match:
        contains: [image]
      is_image_model: true
      generation_config_defaults:
        image_output_mode: text_and_image
        thinking_config:
          level: HIGH
          mode: 9
        media_resolution: HIGH
      clear_generation_config_indexes: [7, 17]
      disable_safety_settings: true
  models:
    gemini-3.1-flash-image-preview:
      generation_config_defaults:
        response_mime_type: null
"""
    )

    defaults = resolve_model_defaults(
        "models/gemini-3.1-flash-image-preview", config_path=config_path
    )

    assert defaults.is_image_model is True
    assert defaults.disable_safety_settings is True
    assert defaults.clear_generation_config_indexes == (7, 17)
    assert defaults.generation_config_defaults["response_mime_type"] is None
    assert (
        defaults.generation_config_defaults["image_output_mode"]
        == AistudioImageOutputMode.text_and_image()
    )
    assert defaults.generation_config_defaults["thinking_config"] == [9, None, None, 3]
    assert defaults.generation_config_defaults["media_resolution"] == 3


def test_resolve_model_defaults_coerces_safety_settings(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model_defaults:
  profiles:
    - name: gemini_models
      match:
        prefixes: [gemini-]
      safety_settings:
        Harassment: 1
        Hate: 2
        Sexually Explicit: 3
        Dangerous Content: 5
"""
    )

    defaults = resolve_model_defaults(
        "models/gemini-2.5-flash", config_path=config_path
    )

    assert defaults.safety_settings == (
        (None, None, 7, 1),
        (None, None, 8, 2),
        (None, None, 9, 3),
        (None, None, 10, 5),
    )


def test_resolve_model_defaults_supports_image_default_tool_names(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model_defaults:
  profiles:
    - name: image_models
      match:
        contains: [image]
      is_image_model: true
      default_tools:
        - google_search_and_image_search
"""
    )

    defaults = resolve_model_defaults(
        "models/gemini-3.1-flash-image-preview", config_path=config_path
    )

    assert defaults.is_image_model is True
    assert defaults.default_tools == ("google_search_and_image_search",)


def test_resolve_model_defaults_keeps_gemini_four_tool_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model_defaults:
  profiles:
    - name: gemini_models
      match:
        prefixes: [gemini-]
      default_tools:
        - google_search
        - code_execution
        - google_maps
        - url_context
"""
    )

    defaults = resolve_model_defaults(
        "models/gemini-3.5-flash", config_path=config_path
    )

    assert defaults.default_tools == (
        "google_search",
        "code_execution",
        "google_maps",
        "url_context",
    )


def test_resolve_model_defaults_model_override_replaces_profile_tools_and_flags(
    tmp_path,
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model_defaults:
  profiles:
    - name: gemini_models
      match:
        prefixes: [gemini-]
      default_tools:
        - google_search
      disable_safety_settings: true
  models:
    gemini-3.5-flash:
      default_tools:
        - code_execution
      disable_safety_settings: false
"""
    )

    defaults = resolve_model_defaults(
        "models/gemini-3.5-flash", config_path=config_path
    )

    assert defaults.default_tools == ("code_execution",)
    assert defaults.disable_safety_settings is False


def test_resolve_model_defaults_drop_unsupported_params_inheritance(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model_defaults:
  drop_unsupported_params: true
  profiles:
    - name: strict_profile
      match:
        prefixes: [strict-]
      drop_unsupported_params: false
    - name: loose_profile
      match:
        prefixes: [loose-]
  models:
    strict-override:
      drop_unsupported_params: true
"""
    )

    assert (
        resolve_model_defaults(
            "loose-model", config_path=config_path
        ).drop_unsupported_params
        is True
    )
    assert (
        resolve_model_defaults(
            "strict-model", config_path=config_path
        ).drop_unsupported_params
        is False
    )
    assert (
        resolve_model_defaults(
            "strict-override", config_path=config_path
        ).drop_unsupported_params
        is True
    )


def test_resolve_tier_quota_limits_supports_token_limits_and_overrides(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
tier_quotas:
  Free:
    hydrogen_models:
      match:
        contains: [hydrogen]
      daily_limit: 50
      daily_token_limit: 10000000
    flash_models:
      match:
        contains: [flash]
      daily_limit: 50
      daily_token_limit: 5000000
    default_limit: 20
  Pro:
    argon_models:
      match:
        contains: [argon]
      daily_limit: 50
      daily_token_limit: 15000000
    pro_models:
      match:
        contains: [pro]
      daily_limit: 50
      daily_token_limit: 13000000
    default_limit: 50
  overrides:
    gemini-3.8-flash:
      daily_limit: 120
      daily_token_limit: 8000000
    gemini-legacy: 33
"""
    )

    h_free = resolve_tier_quota_limits("Free", "gemini-4-hydrogen", config_path=config_path)
    assert h_free.daily_limit == 50
    assert h_free.daily_token_limit == 10_000_000

    f_free = resolve_tier_quota_limits("Free", "gemini-3.6-flash", config_path=config_path)
    assert f_free.daily_limit == 50
    assert f_free.daily_token_limit == 5_000_000

    a_pro = resolve_tier_quota_limits("Pro", "gemini-4-argon", config_path=config_path)
    assert a_pro.daily_limit == 50
    assert a_pro.daily_token_limit == 15_000_000

    p_pro = resolve_tier_quota_limits("Pro", "gemini-3.1-pro-preview", config_path=config_path)
    assert p_pro.daily_limit == 50
    assert p_pro.daily_token_limit == 13_000_000

    ov_obj = resolve_tier_quota_limits("Free", "models/gemini-3.8-flash", config_path=config_path)
    assert ov_obj.daily_limit == 120
    assert ov_obj.daily_token_limit == 8_000_000

    ov_scalar = resolve_tier_quota_limits("Free", "gemini-legacy", config_path=config_path)
    assert ov_scalar.daily_limit == 33
    assert ov_scalar.daily_token_limit is None


def test_repo_config_matches_playground_quotas_and_paid_tiers(monkeypatch):
    repo_config = Path(__file__).resolve().parents[2] / "config.yaml"
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(repo_config))

    invalidate_config_cache()

    assert resolve_tier_quota_limits("Free", "gemini-4-hydrogen", config_path=repo_config) == (
        resolve_tier_quota_limits("Free", "models/gemini-4-hydrogen", config_path=repo_config)
    )
    assert resolve_tier_quota_limits("Free", "gemini-4-hydrogen", config_path=repo_config).daily_limit == 50
    assert resolve_tier_quota_limits("Free", "gemini-4-hydrogen", config_path=repo_config).daily_token_limit == 10_000_000
    assert resolve_tier_quota_limits("Pro", "gemini-4-hydrogen", config_path=repo_config).daily_limit == 100
    assert resolve_tier_quota_limits("Pro", "gemini-4-hydrogen", config_path=repo_config).daily_token_limit == 25_000_000
    assert resolve_tier_quota_limits("Ultra", "gemini-4-carbon", config_path=repo_config).daily_limit == 200
    assert resolve_tier_quota_limits("Ultra_20X", "gemini-4-carbon", config_path=repo_config).daily_limit == 200
    assert resolve_tier_quota_limits("Ultra", "gemini-3.1-pro-preview", config_path=repo_config).daily_limit == 100
    assert resolve_tier_quota_limits("Ultra_20X", "gemini-3.1-pro-preview", config_path=repo_config).daily_limit == 100

    svc = ModelDiscoveryService()
    assert svc.is_paid_model("gemini-4-hydrogen") is False
    assert svc.is_model_allowed_for_tier("gemini-4-hydrogen", "Free") is True
    assert svc.is_paid_model("gemini-4-argon") is True
    assert svc.is_model_allowed_for_tier("gemini-4-argon", "Free") is False
    assert svc.is_model_allowed_for_tier("gemini-4-argon", "Pro") is True
    assert svc.is_paid_model("gemini-4-carbon") is True
    assert svc.is_model_allowed_for_tier("gemini-4-carbon", "Pro") is False
    assert svc.is_model_allowed_for_tier("gemini-4-carbon", "Ultra") is True
    assert svc.is_paid_model("gemini-3.1-pro-preview") is True
    assert svc.is_model_allowed_for_tier("gemini-3.1-pro-preview", "Free") is False
    assert svc.is_model_allowed_for_tier("gemini-3.1-pro-preview", "Pro") is True


def test_validate_config_sections_rejects_invalid_quota_and_rotator():
    import pytest

    validate_config_sections(
        {
            "tier_quotas": {
                "Free": {
                    "flash_models": {"match": {"contains": ["flash"]}, "daily_limit": 50, "daily_token_limit": 5000000},
                    "default_limit": 20,
                },
                "overrides": {"gemini-3.8-flash": {"daily_limit": 60, "daily_token_limit": None}},
            },
            "paid_model_rules": {
                "ultra_required": {"contains": ["carbon"], "required_tiers": ["Ultra", "Ultra_20X"]}
            },
            "rotator": {"strategy": "sticky", "stickiness_threshold": 0.8, "tier_weights": {"Free": 0.0}},
        }
    )

    with pytest.raises(ValueError, match="daily_limit"):
        validate_config_sections({"tier_quotas": {"Free": {"flash": {"daily_limit": 0}}}})

    with pytest.raises(ValueError, match="stickiness_threshold"):
        validate_config_sections({"rotator": {"stickiness_threshold": 1.5}})
