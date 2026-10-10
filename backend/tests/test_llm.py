import unittest
from os import environ
import sys
import types
from unittest.mock import patch

from app.config import Settings
from app.llm import complete_json, provider_status, resolve_provider


class OllamaConfigurationTests(unittest.TestCase):
    def test_provider_status_reports_selected_ollama_model(self) -> None:
        with patch("app.llm.settings.llm_provider", "ollama"), patch(
            "app.llm.settings.ollama_model", "qwen3:4b"
        ):
            status = provider_status()

        self.assertEqual(status["active"], "ollama")
        self.assertEqual(status["ollama_model"], "qwen3:4b")

    def test_auto_provider_uses_ollama_when_cloud_keys_are_missing(self) -> None:
        with patch("app.llm.settings.llm_provider", "auto"), patch(
            "app.llm.settings.openai_api_key", ""
        ), patch("app.llm.settings.xai_api_key", ""), patch(
            "app.llm.settings.ollama_base_url", "http://127.0.0.1:11434/v1"
        ), patch("app.llm.settings.ollama_model", "qwen3:4b"):
            self.assertEqual(resolve_provider(), "ollama")

    def test_auto_provider_prefers_configured_anthropic(self) -> None:
        with patch("app.llm.settings.llm_provider", "auto"), patch(
            "app.llm.settings.anthropic_api_key", "test-key"
        ), patch("app.llm.settings.openai_api_key", "openai-key"):
            self.assertEqual(resolve_provider(), "anthropic")

    def test_databricks_alternate_environment_names_are_supported(self) -> None:
        values = {
            "DATABRICKS_HOST": "workspace.example.com",
            "DATABRICKS_PAT": "test-token",
            "WAREHOUSE_ID": "warehouse-123",
        }
        with patch.dict(environ, values, clear=False):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.databricks_server_hostname, "workspace.example.com")
        self.assertEqual(settings.databricks_token, "test-token")
        self.assertEqual(settings.databricks_warehouse_id, "warehouse-123")

    def test_anthropic_json_response_is_parsed(self) -> None:
        calls: list[dict[str, object]] = []

        class Messages:
            def create(self, **kwargs: object):
                calls.append(kwargs)
                return types.SimpleNamespace(
                    content=[
                        types.SimpleNamespace(
                            type="text",
                            text='{"sql":"SELECT 1"}',
                        )
                    ]
                )

        class FakeAnthropic:
            def __init__(self, **_kwargs: object) -> None:
                self.messages = Messages()

        anthropic = types.ModuleType("anthropic")
        anthropic.Anthropic = FakeAnthropic
        with patch("app.llm.settings.anthropic_api_key", "test-key"), patch(
            "app.llm.settings.anthropic_model", "claude-test"
        ), patch.dict(sys.modules, {"anthropic": anthropic}):
            result = complete_json("Generate SQL", "Count rows", "anthropic")

        self.assertEqual(result, {"sql": "SELECT 1"})
        self.assertEqual(calls[0]["model"], "claude-test")
        self.assertIn("valid JSON", calls[0]["system"])


if __name__ == "__main__":
    unittest.main()
