from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from idea.config import load_config
from idea.model_client import (
    ModelClientError,
    RuntimeModelConfig,
    StructuredModelClient,
    runtime_model_config,
)


def valid_parser_output():
    return {
        "title": "Cache scheduling",
        "technical_domains": ["computer storage"],
        "application_scenario": "data center",
        "technical_problem": "reduce cache misses",
        "features": [
            {
                "feature_id": "F1",
                "feature_text": "schedule jobs using cache locality",
                "source_type": "explicit",
                "source_span": {"start": 0, "end": 10, "text": "cache idea"},
                "required": True,
            }
        ],
        "claimed_effects": ["fewer cache misses"],
        "subject_types": ["method"],
        "scope_breadth": "narrow",
        "assignee_focus": [],
        "inferred_items": [],
    }


def value_output(*, chinese: bool):
    if chinese:
        rationales = ["可从系统行为观察", "存在替代实现路径", "具备一定技术价值"]
        paths = ["采用统一编码器处理", "在识别结果后增加结构化处理"]
        overall = "建议先补充可测量的实施细节后再申请"
        limitations = ["缺少独立市场数据"]
    else:
        rationales = ["observable behavior", "alternative designs exist", "moderate value"]
        paths = ["use one encoder", "post-process the OCR result"]
        overall = "add measurable implementation details before filing"
        limitations = ["no independent market dataset"]
    return {
        "detectability": {
            "rating": 4,
            "rationale": rationales[0],
            "evidence_basis": ["IDEA:F1"],
        },
        "workaround_difficulty": {
            "rating": 3,
            "rationale": rationales[1],
            "evidence_basis": ["IDEA:F1"],
        },
        "technical_market_value": {
            "rating": 3,
            "rationale": rationales[2],
            "evidence_basis": ["NOVELTY:CONCLUSION"],
        },
        "alternative_paths": paths,
        "recommendation": "ADJUST_THEN_FILE",
        "rationale": overall,
        "limitations": limitations,
    }


class StructuredModelClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = load_config().model.model_copy(update={"structured_output_retries": 1})
        self.environment = patch.dict(
            "os.environ", {self.settings.api_key_env: "test-secret"}
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()

    def test_valid_response_is_parsed_and_schema_validated(self) -> None:
        calls = []

        async def transport(payload, headers):
            calls.append((payload, headers))
            return {
                "id": "response-1",
                "choices": [{"message": {"content": json.dumps(valid_parser_output())}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }

        with runtime_model_config(RuntimeModelConfig("https://example.test/v1", "test-secret", "test-model")):
            result = asyncio.run(
                StructuredModelClient(self.settings, transport=transport).complete(
                    "patent-idea-parser", system_prompt="Parse the idea.", input_payload={"idea": "cache idea"}
                )
            )
        self.assertEqual(result.output.features[0].feature_id, "F1")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.usage["completion_tokens"], 20)
        self.assertEqual(calls[0][0]["response_format"], {"type": "json_object"})
        self.assertEqual(calls[0][1]["Authorization"], "Bearer test-secret")

    def test_invalid_json_retries_with_validation_summary(self) -> None:
        responses = ["not-json", json.dumps(valid_parser_output())]
        payloads = []

        async def transport(payload, headers):
            payloads.append(payload)
            return {"choices": [{"message": {"content": responses.pop(0)}}]}

        result = asyncio.run(
            StructuredModelClient(self.settings, transport=transport).complete(
                "patent-idea-parser", system_prompt="Parse.", input_payload={"idea": "cache"}
            )
        )
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(payloads[1]["messages"]), 4)
        self.assertIn("failed validation", payloads[1]["messages"][-1]["content"])

    def test_empty_assistant_content_is_retried_as_structured_failure(self) -> None:
        responses = ["", json.dumps(valid_parser_output())]

        async def transport(payload, headers):
            return {"choices": [{"message": {"content": responses.pop(0)}}]}

        result = asyncio.run(
            StructuredModelClient(self.settings, transport=transport).complete(
                "patent-idea-parser",
                system_prompt="Parse.",
                input_payload={"idea": "cache"},
            )
        )
        self.assertEqual(result.attempts, 2)

    def test_schema_invalid_output_exhausts_retry_without_leaking_key(self) -> None:
        async def transport(payload, headers):
            return {"choices": [{"message": {"content": "{}"}}]}

        with self.assertRaises(ModelClientError) as caught:
            asyncio.run(
                StructuredModelClient(self.settings, transport=transport).complete(
                    "patent-idea-parser", system_prompt="Parse.", input_payload={"idea": "cache"}
                )
            )
        self.assertNotIn("test-secret", str(caught.exception))

    def test_markdown_json_fence_is_tolerated_deterministically(self) -> None:
        async def transport(payload, headers):
            content = "```json\n" + json.dumps(valid_parser_output()) + "\n```"
            return {"choices": [{"message": {"content": content}}]}

        result = asyncio.run(
            StructuredModelClient(self.settings, transport=transport).complete(
                "patent-idea-parser", system_prompt="Parse.", input_payload={"idea": "cache"}
            )
        )
        self.assertEqual(result.output.title, "Cache scheduling")

    def test_english_user_facing_judgments_retry_until_chinese(self) -> None:
        responses = [value_output(chinese=False), value_output(chinese=True)]
        payloads = []

        async def transport(payload, headers):
            payloads.append(payload)
            return {
                "choices": [
                    {"message": {"content": json.dumps(responses.pop(0), ensure_ascii=False)}}
                ]
            }

        result = asyncio.run(
            StructuredModelClient(self.settings, transport=transport).complete(
                "patent-value-analyzer",
                system_prompt="Assess value.",
                input_payload={"allowed_basis_ids": ["IDEA:F1", "NOVELTY:CONCLUSION"]},
            )
        )
        self.assertEqual(result.attempts, 2)
        self.assertIn("建议先补充", result.output.rationale)
        self.assertIn("Simplified Chinese", payloads[1]["messages"][-1]["content"])

    def test_missing_credential_fails_before_transport(self) -> None:
        with patch.dict("os.environ", {self.settings.api_key_env: ""}):
            with self.assertRaisesRegex(ModelClientError, "credential"):
                StructuredModelClient(self.settings).api_key()

    def test_runtime_config_overrides_all_model_coordinates_only_inside_context(self) -> None:
        client = StructuredModelClient(self.settings)
        self.assertEqual(client.api_key(), "test-secret")
        with runtime_model_config(
            RuntimeModelConfig(
                "https://runtime.example.test/v1",
                "ephemeral-test-token",
                "runtime-model",
            )
        ):
            self.assertEqual(client.api_key(), "ephemeral-test-token")
            self.assertEqual(client.base_url(), "https://runtime.example.test/v1")
            self.assertEqual(client.model_name(), "runtime-model")
            self.assertEqual(client._payload([])["model"], "runtime-model")
        self.assertEqual(client.api_key(), "test-secret")
        self.assertEqual(client.model_name(), self.settings.default)

    def test_volcengine_runtime_disables_thinking_for_structured_output(self) -> None:
        client = StructuredModelClient(self.settings)
        with runtime_model_config(
            RuntimeModelConfig(
                "https://ark.cn-beijing.volces.com/api/coding/v3",
                "ephemeral-test-token",
                "kimi-k2.6",
            )
        ):
            payload = client._payload([])
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_deepseek_runtime_disables_thinking_for_structured_output(self) -> None:
        client = StructuredModelClient(self.settings)
        with runtime_model_config(
            RuntimeModelConfig(
                "https://api.deepseek.com",
                "ephemeral-test-token",
                "deepseek-v4-flash",
            )
        ):
            payload = client._payload([])
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_non_volcengine_runtime_does_not_receive_vendor_thinking_field(self) -> None:
        client = StructuredModelClient(self.settings)
        with runtime_model_config(
            RuntimeModelConfig(
                "https://api.example.test/v1",
                "ephemeral-test-token",
                "compatible-model",
            )
        ):
            payload = client._payload([])
        self.assertNotIn("thinking", payload)

    def test_default_transport_uses_langchain_chat_model_and_preserves_usage(self) -> None:
        client = StructuredModelClient(self.settings)
        message = AIMessage(
            content=json.dumps(valid_parser_output()),
            id="langchain-response",
            usage_metadata={
                "input_tokens": 12,
                "output_tokens": 34,
                "total_tokens": 46,
            },
        )

        async def scenario():
            with runtime_model_config(
                RuntimeModelConfig(
                    "https://ark.cn-beijing.volces.com/api/coding/v3",
                    "ephemeral-test-token",
                    "kimi-k2.6",
                )
            ):
                return await client._langchain_post(client._payload([]), trust_env=False)

        with patch("idea.model_client.ChatOpenAI") as chat:
            chat.return_value.ainvoke = AsyncMock(return_value=message)
            response = asyncio.run(scenario())

        self.assertEqual(response["id"], "langchain-response")
        self.assertEqual(response["usage"]["prompt_tokens"], 12)
        self.assertEqual(response["usage"]["completion_tokens"], 34)
        arguments = chat.call_args.kwargs
        self.assertEqual(arguments["model"], "kimi-k2.6")
        self.assertEqual(arguments["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(arguments["max_retries"], 0)
        self.assertEqual(arguments["http_socket_options"], ())


if __name__ == "__main__":
    unittest.main()
