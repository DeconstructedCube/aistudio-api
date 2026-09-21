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
    """Isolate all test runs from repository-level config.yaml modifications."""
    test_config_path = tmp_path / "test_isolated_config.yaml"
    test_config_path.write_text(_TEST_DEFAULT_CONFIG, encoding="utf-8")
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(test_config_path))
    invalidate_config_cache()
    yield
    invalidate_config_cache()
