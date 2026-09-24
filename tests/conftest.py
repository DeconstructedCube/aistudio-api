"""Hermetic test environment fixtures isolating test runs from local config."""

from __future__ import annotations

import pytest

from aistudio_api.infrastructure.gateway.model_defaults import invalidate_config_cache

_TEST_DEFAULT_CONFIG = """
api_keys: []
model_defaults:
  drop_unsupported_params: false
  profiles:
    - name: image_models
      match:
        contains:
          - image
      is_image_model: true
      default_tools:
        - google_search_and_image_search
      generation_config_defaults:
        response_mime_type: null
        image_output_mode: image_only
        thinking_config:
          level: MINIMAL
          mode: 1
      clear_generation_config_indexes:
        - 7
        - 13
        - 17
      disable_safety_settings: true
    - name: gemma_models
      match:
        prefixes:
          - gemma-
      default_tools:
        - google_search
      safety_settings:
        Harassment: 5
        Hate: 5
        Sexually Explicit: 5
        Dangerous Content: 5
    - name: gemini_models
      match:
        prefixes:
          - gemini-
      default_tools:
        - google_search
      safety_settings:
        Harassment: 5
        Hate: 5
        Sexually Explicit: 5
        Dangerous Content: 5
  models: {}
"""


@pytest.fixture(autouse=True)
def isolate_test_config(monkeypatch, tmp_path):
    """Isolate all test runs from repository-level config.yaml modifications and runtime state files."""
    test_config_path = tmp_path / "test_isolated_config.yaml"
    test_config_path.write_text(_TEST_DEFAULT_CONFIG, encoding="utf-8")
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(test_config_path))
    monkeypatch.setenv("AISTUDIO_DATA_DIR", str(test_data_dir))
    monkeypatch.setenv("AISTUDIO_ACCOUNTS_DIR", str(test_data_dir / "accounts"))
    monkeypatch.setenv("AISTUDIO_STATS_FILE", str(test_data_dir / "stats.json"))
    monkeypatch.setenv(
        "AISTUDIO_ROTATOR_STATE_FILE", str(test_data_dir / "rotator_state.json")
    )
    invalidate_config_cache()
    yield
    invalidate_config_cache()
