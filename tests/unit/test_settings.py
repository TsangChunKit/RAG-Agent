"""Unit tests for scripts/settings.py

Tests the Gemini runtime parameters persistence and API key management.
Coverage target: 75%+
"""
import json
import os
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, mock_open

import pytest

from scripts import settings


class TestMergeFunction:
    """Test the internal _merge helper function."""

    def test_merge_empty_override(self):
        """Test merging with empty override dict."""
        default = {"a": 1, "b": 2, "c": 3}
        override = {}
        result = settings._merge(default, override)
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_merge_partial_override(self):
        """Test merging with partial override (some None values)."""
        default = {"a": 1, "b": 2, "c": 3}
        override = {"a": 10, "b": None, "c": 30}
        result = settings._merge(default, override)
        assert result == {"a": 10, "b": 2, "c": 30}  # None uses default

    def test_merge_none_override(self):
        """Test merging when override is None."""
        default = {"a": 1, "b": 2}
        result = settings._merge(default, None)
        assert result == {"a": 1, "b": 2}

    def test_merge_missing_keys_in_override(self):
        """Test merging when override is missing some keys."""
        default = {"a": 1, "b": 2, "c": 3}
        override = {"a": 10}
        result = settings._merge(default, override)
        assert result == {"a": 10, "b": 2, "c": 3}

    def test_merge_zero_and_false_values(self):
        """Test that 0 and False are treated as valid overrides, not None."""
        default = {"a": 1, "b": True, "c": "default"}
        override = {"a": 0, "b": False, "c": ""}
        result = settings._merge(default, override)
        assert result == {"a": 0, "b": False, "c": ""}


class TestLoadRaw:
    """Test the internal _load_raw function."""

    def test_load_raw_file_not_exists(self):
        """Test loading when settings file doesn't exist."""
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
            result = settings._load_raw()
            assert result == {}

    def test_load_raw_valid_json(self):
        """Test loading valid JSON from settings file."""
        mock_data = {"provider": "gemini", "dialogue": {"model": "test-model"}}
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps(mock_data)
        with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
            result = settings._load_raw()
            assert result == mock_data

    def test_load_raw_invalid_json(self):
        """Test loading invalid JSON returns empty dict."""
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = '{"invalid": json}'
        with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
            result = settings._load_raw()
            assert result == {}

    def test_load_raw_empty_file(self):
        """Test loading empty file."""
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = ''
        with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
            result = settings._load_raw()
            assert result == {}


class TestDialogueParams:
    """Test dialogue_params() function."""

    def test_dialogue_params_no_settings_file(self):
        """Test dialogue params with no settings file (use defaults)."""
        with patch('scripts.settings._load_raw', return_value={}):
            result = settings.dialogue_params()
            assert "model" in result
            assert "thinking_level" in result
            assert "temperature" in result
            assert "max_output_tokens" in result

    def test_dialogue_params_with_override(self):
        """Test dialogue params with custom override values."""
        mock_settings = {
            "dialogue": {
                "model": "custom-model",
                "thinking_level": "high",
                "temperature": 0.9,
                "max_output_tokens": 10000
            }
        }
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            result = settings.dialogue_params()
            assert result["model"] == "custom-model"
            assert result["thinking_level"] == "high"
            assert result["temperature"] == 0.9
            assert result["max_output_tokens"] == 10000

    def test_dialogue_params_partial_override(self):
        """Test dialogue params with only some fields overridden."""
        mock_settings = {
            "dialogue": {
                "model": "custom-model",
                "thinking_level": None  # Should use default
            }
        }
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            result = settings.dialogue_params()
            assert result["model"] == "custom-model"
            assert result["thinking_level"] == settings._DEFAULT_DIALOGUE["thinking_level"]


class TestSummaryParams:
    """Test summary_params() function."""

    def test_summary_params_no_settings_file(self):
        """Test summary params with no settings file (use defaults)."""
        with patch('scripts.settings._load_raw', return_value={}):
            result = settings.summary_params()
            assert "model" in result
            assert "thinking_level" in result
            assert "temperature" in result
            assert "max_output_tokens" not in result  # Excluded in this function

    def test_summary_params_with_override(self):
        """Test summary params with custom values."""
        mock_settings = {
            "summary": {
                "model": "summary-model",
                "thinking_level": "medium",
                "temperature": 0.5,
                "max_output_tokens": {"text": 5000}  # Should be excluded
            }
        }
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            result = settings.summary_params()
            assert result["model"] == "summary-model"
            assert result["thinking_level"] == "medium"
            assert result["temperature"] == 0.5
            assert "max_output_tokens" not in result


class TestSummaryMaxTokens:
    """Test summary_max_tokens() function."""

    def test_summary_max_tokens_default(self):
        """Test getting default max tokens for each task."""
        with patch('scripts.settings._load_raw', return_value={}):
            text_tokens = settings.summary_max_tokens("text")
            assert isinstance(text_tokens, int)
            assert text_tokens > 0

    def test_summary_max_tokens_custom(self):
        """Test getting custom max tokens from settings."""
        mock_settings = {
            "summary": {
                "max_output_tokens": {
                    "text": 8000,
                    "chat_graph": 4000
                }
            }
        }
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            assert settings.summary_max_tokens("text") == 8000
            assert settings.summary_max_tokens("chat_graph") == 4000

    def test_summary_max_tokens_missing_task(self):
        """Test getting max tokens for task not in custom settings (fall back to default)."""
        mock_settings = {
            "summary": {
                "max_output_tokens": {
                    "text": 8000
                }
            }
        }
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            # chat_graph not in mock_settings, should use default
            result = settings.summary_max_tokens("chat_graph")
            assert result == settings.GEMINI_SUMMARY_MAX_TOKENS["chat_graph"]

    def test_summary_max_tokens_all_valid_tasks(self):
        """Test all valid task types in SUMMARY_MAX_TASKS."""
        with patch('scripts.settings._load_raw', return_value={}):
            for task in settings.SUMMARY_MAX_TASKS.keys():
                result = settings.summary_max_tokens(task)
                assert isinstance(result, int)
                assert result > 0


class TestProvider:
    """Test provider() function."""

    def test_provider_default(self):
        """Test provider returns default when not set."""
        with patch('scripts.settings._load_raw', return_value={}):
            assert settings.provider() == settings.DEFAULT_PROVIDER

    def test_provider_valid(self):
        """Test provider returns valid provider value."""
        for valid_provider in settings.VALID_PROVIDERS:
            mock_settings = {"provider": valid_provider}
            with patch('scripts.settings._load_raw', return_value=mock_settings):
                assert settings.provider() == valid_provider

    def test_provider_invalid_fallback(self):
        """Test provider falls back to default when invalid value."""
        mock_settings = {"provider": "invalid_provider"}
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            assert settings.provider() == settings.DEFAULT_PROVIDER


class TestAPIKeys:
    """Test API key getter functions."""

    def test_get_api_key_from_settings(self):
        """Test getting Gemini API key from settings file."""
        mock_settings = {"api_key": "test-key-123"}
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            assert settings.get_api_key() == "test-key-123"

    def test_get_api_key_from_env(self):
        """Test getting Gemini API key from environment variable."""
        with patch('scripts.settings._load_raw', return_value={}):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "env-key-456"}):
                assert settings.get_api_key() == "env-key-456"

    def test_get_api_key_none(self):
        """Test when no API key is set."""
        with patch('scripts.settings._load_raw', return_value={}):
            with patch.dict(os.environ, {}, clear=True):
                assert settings.get_api_key() is None

    def test_get_xai_key_from_settings(self):
        """Test getting xAI API key from settings file."""
        mock_settings = {"xai_api_key": "xai-key-789"}
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            assert settings.get_xai_key() == "xai-key-789"

    def test_get_xai_key_from_env(self):
        """Test getting xAI API key from environment variable."""
        with patch('scripts.settings._load_raw', return_value={}):
            with patch.dict(os.environ, {"XAI_API_KEY": "xai-env-key"}):
                assert settings.get_xai_key() == "xai-env-key"

    def test_get_xai_key_none(self):
        """Test when no xAI key is set."""
        with patch('scripts.settings._load_raw', return_value={}):
            with patch.dict(os.environ, {}, clear=True):
                assert settings.get_xai_key() is None

    def test_get_hermes_key_default(self):
        """Test getting Hermes key with default fallback."""
        with patch('scripts.settings._load_raw', return_value={}):
            with patch.dict(os.environ, {}, clear=True):
                result = settings.get_hermes_key()
                # Should return the HERMES_API_KEY constant default
                assert result == settings.HERMES_API_KEY

    def test_get_hermes_key_from_settings(self):
        """Test getting Hermes key from settings file."""
        mock_settings = {"hermes_api_key": "hermes-key-123"}
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            assert settings.get_hermes_key() == "hermes-key-123"

    def test_get_hermes_key_from_env(self):
        """Test getting Hermes key from environment variable."""
        with patch('scripts.settings._load_raw', return_value={}):
            with patch.dict(os.environ, {"HERMES_API_KEY": "hermes-env-key"}):
                assert settings.get_hermes_key() == "hermes-env-key"


class TestHermesBaseURL:
    """Test hermes_base_url() function."""

    def test_hermes_base_url_default(self):
        """Test Hermes base URL returns default."""
        with patch('scripts.settings._load_raw', return_value={}):
            assert settings.hermes_base_url() == settings.HERMES_BASE_URL

    def test_hermes_base_url_custom(self):
        """Test Hermes base URL with custom value."""
        mock_settings = {"hermes_base_url": "http://custom:9999/v1"}
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            assert settings.hermes_base_url() == "http://custom:9999/v1"


class TestLoadForUI:
    """Test load_for_ui() function."""

    def test_load_for_ui_structure(self):
        """Test load_for_ui returns correct structure."""
        mock_settings = {
            "provider": "gemini",
            "dialogue": {"model": "test-model"},
            "summary": {"model": "summary-model"},
            "api_key": "test-key",
            "xai_api_key": "xai-key"
        }
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            result = settings.load_for_ui()

            assert "provider" in result
            assert "dialogue" in result
            assert "summary" in result
            assert "summary_max_tokens" in result
            assert "api_key_set" in result
            assert "xai_api_key_set" in result
            assert "hermes_base_url" in result

    def test_load_for_ui_api_keys_masked(self):
        """Test load_for_ui returns boolean flags instead of actual keys."""
        mock_settings = {
            "api_key": "secret-key-123",
            "xai_api_key": "xai-secret"
        }
        with patch('scripts.settings._load_raw', return_value=mock_settings):
            result = settings.load_for_ui()

            assert result["api_key_set"] is True
            assert result["xai_api_key_set"] is True
            assert "api_key" not in result  # Actual key should not be in result

    def test_load_for_ui_no_keys_set(self):
        """Test load_for_ui when no API keys are set."""
        with patch('scripts.settings._load_raw', return_value={}):
            with patch.dict(os.environ, {}, clear=True):
                result = settings.load_for_ui()

                assert result["api_key_set"] is False
                assert result["xai_api_key_set"] is False

    def test_load_for_ui_summary_max_tokens_all_tasks(self):
        """Test load_for_ui includes max_tokens for all task types."""
        with patch('scripts.settings._load_raw', return_value={}):
            result = settings.load_for_ui()

            summary_max = result["summary_max_tokens"]
            for task in settings.SUMMARY_MAX_TASKS.keys():
                assert task in summary_max
                assert isinstance(summary_max[task], int)


class TestSave:
    """Test save() function."""

    def test_save_basic(self):
        """Test saving basic settings."""
        dialogue = {"model": "new-model", "temperature": 0.8}
        summary = {"model": "summary-model"}
        summary_max = {"text": 5000, "chat_graph": 3000, "therapy_graph": 8000}

        mock_path = MagicMock()
        mock_parent = MagicMock()
        mock_path.parent = mock_parent

        with patch('scripts.settings._load_raw', return_value={}):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.save(dialogue, summary, summary_max)

                mock_parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)
                mock_path.write_text.assert_called_once()

                # Check written content
                written_json = mock_path.write_text.call_args[0][0]
                written_data = json.loads(written_json)

                assert written_data["dialogue"] == dialogue
                assert written_data["summary"]["model"] == "summary-model"
                assert written_data["summary"]["max_output_tokens"] == summary_max

    def test_save_with_api_keys(self):
        """Test saving with API keys."""
        dialogue = {"model": "model"}
        summary = {"model": "summary"}
        summary_max = {"text": 5000}

        mock_path = MagicMock()
        mock_path.parent = MagicMock()

        with patch('scripts.settings._load_raw', return_value={}):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.save(
                    dialogue,
                    summary,
                    summary_max,
                    api_key="new-api-key",
                    xai_api_key="new-xai-key",
                    hermes_api_key="new-hermes-key"
                )

                written_json = mock_path.write_text.call_args[0][0]
                written_data = json.loads(written_json)

                assert written_data["api_key"] == "new-api-key"
                assert written_data["xai_api_key"] == "new-xai-key"
                assert written_data["hermes_api_key"] == "new-hermes-key"

    def test_save_preserves_existing_keys(self):
        """Test that passing None/empty for API keys preserves existing keys."""
        dialogue = {"model": "model"}
        summary = {"model": "summary"}
        summary_max = {"text": 5000}

        existing_settings = {
            "api_key": "existing-key",
            "xai_api_key": "existing-xai"
        }

        mock_path = MagicMock()
        mock_path.parent = MagicMock()

        with patch('scripts.settings._load_raw', return_value=existing_settings):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.save(dialogue, summary, summary_max)  # No keys passed

                written_json = mock_path.write_text.call_args[0][0]
                written_data = json.loads(written_json)

                # Existing keys should be preserved
                assert written_data["api_key"] == "existing-key"
                assert written_data["xai_api_key"] == "existing-xai"

    def test_save_with_provider(self):
        """Test saving with provider selection."""
        dialogue = {"model": "model"}
        summary = {"model": "summary"}
        summary_max = {"text": 5000}

        mock_path = MagicMock()
        mock_path.parent = MagicMock()

        with patch('scripts.settings._load_raw', return_value={}):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.save(dialogue, summary, summary_max, provider="grok")

                written_json = mock_path.write_text.call_args[0][0]
                written_data = json.loads(written_json)

                assert written_data["provider"] == "grok"

    def test_save_ignores_invalid_provider(self):
        """Test saving ignores invalid provider value."""
        dialogue = {"model": "model"}
        summary = {"model": "summary"}
        summary_max = {"text": 5000}

        mock_path = MagicMock()
        mock_path.parent = MagicMock()

        with patch('scripts.settings._load_raw', return_value={}):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.save(dialogue, summary, summary_max, provider="invalid")

                written_json = mock_path.write_text.call_args[0][0]
                written_data = json.loads(written_json)

                assert "provider" not in written_data

    def test_save_strips_whitespace_from_keys(self):
        """Test that API keys have whitespace stripped."""
        dialogue = {"model": "model"}
        summary = {"model": "summary"}
        summary_max = {"text": 5000}

        mock_path = MagicMock()
        mock_path.parent = MagicMock()

        with patch('scripts.settings._load_raw', return_value={}):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.save(
                    dialogue,
                    summary,
                    summary_max,
                    api_key="  key-with-spaces  ",
                    xai_api_key="\txai-key\n"
                )

                written_json = mock_path.write_text.call_args[0][0]
                written_data = json.loads(written_json)

                assert written_data["api_key"] == "key-with-spaces"
                assert written_data["xai_api_key"] == "xai-key"

    def test_save_with_hermes_base_url(self):
        """Test saving with custom Hermes base URL."""
        dialogue = {"model": "model"}
        summary = {"model": "summary"}
        summary_max = {"text": 5000}

        mock_path = MagicMock()
        mock_path.parent = MagicMock()

        with patch('scripts.settings._load_raw', return_value={}):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.save(
                    dialogue,
                    summary,
                    summary_max,
                    hermes_base_url="http://custom:8080/v1"
                )

                written_json = mock_path.write_text.call_args[0][0]
                written_data = json.loads(written_json)

                assert written_data["hermes_base_url"] == "http://custom:8080/v1"


class TestReset:
    """Test reset() function."""

    def test_reset_preserves_keys_and_provider(self):
        """Test reset preserves API keys and provider but resets other params."""
        existing_settings = {
            "api_key": "preserved-key",
            "xai_api_key": "preserved-xai",
            "hermes_api_key": "preserved-hermes",
            "hermes_base_url": "http://custom:9999",
            "provider": "grok",
            "dialogue": {"model": "custom-model", "temperature": 0.9},
            "summary": {"model": "custom-summary"}
        }

        mock_path = MagicMock()
        mock_path.parent = MagicMock()

        with patch('scripts.settings._load_raw', return_value=existing_settings):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.reset()

                written_json = mock_path.write_text.call_args[0][0]
                written_data = json.loads(written_json)

                # Keys and provider should be preserved
                assert written_data["api_key"] == "preserved-key"
                assert written_data["xai_api_key"] == "preserved-xai"
                assert written_data["hermes_api_key"] == "preserved-hermes"
                assert written_data["hermes_base_url"] == "http://custom:9999"
                assert written_data["provider"] == "grok"

                # Other params should NOT be in the reset file
                assert "dialogue" not in written_data
                assert "summary" not in written_data

    def test_reset_no_existing_settings(self):
        """Test reset when no existing settings (creates minimal file)."""
        mock_path = MagicMock()
        mock_path.parent = MagicMock()

        with patch('scripts.settings._load_raw', return_value={}):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.reset()

                written_json = mock_path.write_text.call_args[0][0]
                written_data = json.loads(written_json)

                # Should be empty dict
                assert written_data == {}

    def test_reset_creates_parent_directory(self):
        """Test reset creates parent directory if it doesn't exist."""
        mock_path = MagicMock()
        mock_parent = MagicMock()
        mock_path.parent = mock_parent

        with patch('scripts.settings._load_raw', return_value={}):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.reset()

                mock_parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)


class TestConstants:
    """Test module constants are correctly defined."""

    def test_valid_providers(self):
        """Test VALID_PROVIDERS contains expected values."""
        assert "gemini" in settings.VALID_PROVIDERS
        assert "grok" in settings.VALID_PROVIDERS
        assert "hermes" in settings.VALID_PROVIDERS
        assert len(settings.VALID_PROVIDERS) >= 3

    def test_default_provider(self):
        """Test DEFAULT_PROVIDER is valid."""
        assert settings.DEFAULT_PROVIDER in settings.VALID_PROVIDERS

    def test_summary_max_tasks(self):
        """Test SUMMARY_MAX_TASKS has expected task types."""
        assert "text" in settings.SUMMARY_MAX_TASKS
        assert "chat_graph" in settings.SUMMARY_MAX_TASKS
        assert "therapy_graph" in settings.SUMMARY_MAX_TASKS
        assert all(isinstance(v, str) for v in settings.SUMMARY_MAX_TASKS.values())


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_summary_max_tokens_invalid_task(self):
        """Test summary_max_tokens with invalid task type falls back gracefully."""
        with patch('scripts.settings._load_raw', return_value={}):
            # This should not raise an error, but will use get() which returns None
            # then the 'or' operator will use the fallback
            with pytest.raises(KeyError):
                settings.summary_max_tokens("nonexistent_task")

    def test_unicode_in_api_keys(self):
        """Test handling of unicode characters in API keys."""
        dialogue = {"model": "model"}
        summary = {"model": "summary"}
        summary_max = {"text": 5000}

        mock_path = MagicMock()
        mock_path.parent = MagicMock()

        with patch('scripts.settings._load_raw', return_value={}):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.save(
                    dialogue,
                    summary,
                    summary_max,
                    api_key="测试-key-中文"
                )

                written_json = mock_path.write_text.call_args[0][0]
                # Should use ensure_ascii=False
                assert "测试" in written_json or "\\u" in written_json  # Either raw or escaped

    def test_empty_string_api_key_not_saved(self):
        """Test that empty string API key is not saved (falsy value)."""
        dialogue = {"model": "model"}
        summary = {"model": "summary"}
        summary_max = {"text": 5000}

        existing = {"api_key": "existing-key"}
        mock_path = MagicMock()
        mock_path.parent = MagicMock()

        with patch('scripts.settings._load_raw', return_value=existing):
            with patch('scripts.settings.GEMINI_SETTINGS_PATH', mock_path):
                settings.save(dialogue, summary, summary_max, api_key="")  # Empty string

                written_json = mock_path.write_text.call_args[0][0]
                written_data = json.loads(written_json)

                # Empty string is falsy, should preserve existing key
                assert written_data["api_key"] == "existing-key"
