"""Hermetic test environment fixtures isolating test runs from local config."""

from __future__ import annotations

import pytest

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


@pytest.fixture(scope="session")
def _shared_test_env(tmp_path_factory):
    base_dir = tmp_path_factory.mktemp("test_env")
    config_file = base_dir / "test_isolated_config.yaml"
    config_file.write_text(_TEST_DEFAULT_CONFIG, encoding="utf-8")
    data_dir = base_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "accounts").mkdir(exist_ok=True)
    return config_file, data_dir


@pytest.fixture(autouse=True)
def isolate_test_config(monkeypatch, _shared_test_env):
    """Isolate all test runs from repository-level config.yaml modifications and runtime state files."""
    config_file, data_dir = _shared_test_env
    stats_file = data_dir / "stats.json"
    rotator_file = data_dir / "rotator_state.json"
    accounts_dir = data_dir / "accounts"

    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("AISTUDIO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AISTUDIO_ACCOUNTS_DIR", str(accounts_dir))
    monkeypatch.setenv("AISTUDIO_STATS_FILE", str(stats_file))
    monkeypatch.setenv("AISTUDIO_ROTATOR_STATE_FILE", str(rotator_file))
    yield
    if stats_file.exists():
        stats_file.unlink()
    if rotator_file.exists():
        rotator_file.unlink()
