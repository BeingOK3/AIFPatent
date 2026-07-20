from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import openai
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from .agent_schemas import AgentModel, agent_json_schema, validate_agent_output
from .config import ModelSettings


ModelTransport = Callable[[dict[str, Any], dict[str, str]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RuntimeModelConfig:
    base_url: str
    api_key: str
    model: str


_RUN_MODEL_CONFIG: ContextVar[RuntimeModelConfig | None] = ContextVar(
    "idea_run_model_config", default=None
)


class ModelClientError(RuntimeError):
    pass


_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")


def _is_primarily_chinese(value: str) -> bool:
    cjk_count = len(_CJK_PATTERN.findall(value))
    latin_count = len(_LATIN_PATTERN.findall(value))
    return cjk_count > 0 and cjk_count * 4 >= latin_count


def _validate_user_facing_language(agent_name: str, output: AgentModel) -> None:
    """Reject English-only judgment text so the structured retry can correct it."""
    data = output.model_dump(mode="json")
    texts: list[tuple[str, str]] = []

    def add(path: str, value: Any) -> None:
        if isinstance(value, str) and value.strip():
            texts.append((path, value))

    def add_list(path: str, values: Any) -> None:
        if isinstance(values, list):
            for index, value in enumerate(values):
                add(f"{path}[{index}]", value)

    if agent_name == "patent-document-analyzer":
        for field in (
            "technical_problem",
            "technical_solution",
            "technical_effect",
            "application_scenario",
            "independent_claim_summary",
            "relevance_rationale",
        ):
            add(field, data.get(field))
        add_list("limitations", data.get("limitations"))
        for index, mapping in enumerate(data.get("feature_mappings", [])):
            add(f"feature_mappings[{index}].rationale", mapping.get("rationale"))
    elif agent_name == "patent-inventive-step-analyzer":
        add("objective_technical_problem", data.get("objective_technical_problem"))
        add("overall_rationale", data.get("overall_rationale"))
        add_list("limitations", data.get("limitations"))
        for index, feature in enumerate(data.get("distinguishing_features", [])):
            add(f"distinguishing_features[{index}].rationale", feature.get("rationale"))
    elif agent_name == "patent-value-analyzer":
        for field in ("detectability", "workaround_difficulty", "technical_market_value"):
            add(f"{field}.rationale", data.get(field, {}).get("rationale"))
        add("rationale", data.get("rationale"))
        add_list("alternative_paths", data.get("alternative_paths"))
        add_list("limitations", data.get("limitations"))
    elif agent_name == "patent-evidence-auditor":
        for index, issue in enumerate(data.get("issues", [])):
            add(f"issues[{index}].message", issue.get("message"))
    elif agent_name == "patent-report-composer":
        for field in (
            "executive_summary",
            "novelty_statement",
            "inventive_step_statement",
            "value_statement",
            "simulated_office_action",
        ):
            add(field, data.get(field))
        add_list("action_recommendations", data.get("action_recommendations"))

    invalid = [path for path, text in texts if not _is_primarily_chinese(text)]
    if invalid:
        raise ValueError(
            "user-facing text must be primarily Simplified Chinese: "
            + ", ".join(invalid[:12])
        )


@contextmanager
def runtime_model_config(config: RuntimeModelConfig) -> Iterator[None]:
    """Make one page-supplied model configuration available to one async Run."""
    value = RuntimeModelConfig(
        base_url=config.base_url.strip().rstrip("/"),
        api_key=config.api_key.strip(),
        model=config.model.strip(),
    )
    if not value.base_url or not value.api_key or not value.model:
        raise ModelClientError("runtime base URL, API credential, and model are required")
    token = _RUN_MODEL_CONFIG.set(value)
    try:
        yield
    finally:
        _RUN_MODEL_CONFIG.reset(token)


@dataclass(frozen=True)
class AgentCallResult:
    agent_name: str
    model: str
    output: AgentModel
    attempts: int
    duration_ms: int
    usage: dict[str, int]
    response_id: str | None


class StructuredModelClient:
    def __init__(
        self,
        settings: ModelSettings,
        *,
        transport: ModelTransport | None = None,
    ):
        self.settings = settings
        self.transport = transport or self._langchain_transport

    async def complete(
        self,
        agent_name: str,
        *,
        system_prompt: str,
        input_payload: dict[str, Any],
    ) -> AgentCallResult:
        schema = agent_json_schema(agent_name)
        messages = [
            {
                "role": "system",
                "content": (
                    system_prompt.strip()
                    + "\n\nAll human-readable explanations, rationales, summaries, recommendations, "
                    "limitations, and issue messages must use Simplified Chinese. Keep schema "
                    "enums, IDs, publication numbers, and search query text unchanged."
                    + "\n\nReturn only one JSON object that validates against the supplied JSON Schema. "
                    "Do not use markdown fences. Do not invent tool calls or evidence IDs."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"input": input_payload, "output_schema": schema},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
        started = time.monotonic()
        errors = []
        total_attempts = self.settings.structured_output_retries + 1
        for attempt in range(1, total_attempts + 1):
            response = await self.transport(self._payload(messages), self._headers())
            try:
                content = self._content(response)
                parsed = json.loads(self._strip_fence(content))
                output = validate_agent_output(agent_name, parsed)
                _validate_user_facing_language(agent_name, output)
                usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
                return AgentCallResult(
                    agent_name=agent_name,
                    model=self.model_name(),
                    output=output,
                    attempts=attempt,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    usage={
                        key: int(value)
                        for key, value in usage.items()
                        if isinstance(value, (int, float))
                    },
                    response_id=response.get("id"),
                )
            except (
                json.JSONDecodeError,
                ValidationError,
                ValueError,
                TypeError,
                ModelClientError,
            ) as exc:
                errors.append(f"{type(exc).__name__}: {str(exc)[:400]}")
                if attempt >= total_attempts:
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": self._safe_previous_content(response),
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The previous JSON failed validation. Correct its structure and ensure every "
                            "user-facing explanation is written in Simplified Chinese, then return only JSON. "
                            f"Validation summary: {errors[-1]}"
                        ),
                    }
                )
        raise ModelClientError(
            f"structured output failed after {total_attempts} attempts: {' | '.join(errors)}"
        )

    def api_key(self) -> str:
        runtime = _RUN_MODEL_CONFIG.get()
        if runtime:
            return runtime.api_key
        key = os.environ.get(self.settings.api_key_env, "").strip()
        if key:
            return key
        raise ModelClientError("runtime model credential is not configured")

    def model_name(self) -> str:
        runtime = _RUN_MODEL_CONFIG.get()
        return runtime.model if runtime else self.settings.default

    def base_url(self) -> str:
        runtime = _RUN_MODEL_CONFIG.get()
        return runtime.base_url if runtime else str(self.settings.base_url).rstrip("/")

    def _payload(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name(),
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_output_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        if self._uses_volcengine_gateway():
            # Ark Coding models such as Kimi expose their chain of thought through
            # reasoning_content and count it against max_tokens.  For strict JSON
            # extraction this can consume the entire budget, leave content empty,
            # or exceed the request timeout.  Ark explicitly supports disabling
            # thinking, which makes the response behave like a normal structured
            # completion.  Keep the adapter host-scoped so other OpenAI-compatible
            # providers never receive a vendor-specific field.
            payload["thinking"] = {"type": "disabled"}
        return payload

    def _uses_volcengine_gateway(self) -> bool:
        hostname = (urlsplit(self.base_url()).hostname or "").lower()
        return hostname == "volces.com" or hostname.endswith(".volces.com")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key()}",
            "Content-Type": "application/json",
        }

    async def _langchain_transport(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        try:
            return await self._langchain_post(payload, trust_env=True)
        except (ImportError, httpx.ProxyError, httpx.ConnectError, openai.APIConnectionError):
            return await self._langchain_post(payload, trust_env=False)

    async def _langchain_post(
        self, payload: dict[str, Any], *, trust_env: bool
    ) -> dict[str, Any]:
        extra_body = None
        if "thinking" in payload:
            extra_body = {"thinking": payload["thinking"]}
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            trust_env=trust_env,
            follow_redirects=True,
        ) as async_client:
            with httpx.Client(
                timeout=self.settings.timeout_seconds,
                trust_env=trust_env,
                follow_redirects=True,
            ) as sync_client:
                model = ChatOpenAI(
                    model=self.model_name(),
                    base_url=self.base_url(),
                    api_key=self.api_key(),
                    temperature=self.settings.temperature,
                    max_completion_tokens=self.settings.max_output_tokens,
                    timeout=self.settings.timeout_seconds,
                    max_retries=0,
                    extra_body=extra_body,
                    http_client=sync_client,
                    http_async_client=async_client,
                    http_socket_options=(),
                    stream_usage=False,
                )
                message = await model.ainvoke(
                    payload["messages"],
                    response_format=payload["response_format"],
                )

        content = self._langchain_content(message.content)
        metadata = message.response_metadata if isinstance(message.response_metadata, dict) else {}
        token_usage = metadata.get("token_usage")
        usage = dict(token_usage) if isinstance(token_usage, dict) else {}
        usage_metadata = message.usage_metadata
        if isinstance(usage_metadata, dict):
            mapping = {
                "input_tokens": "prompt_tokens",
                "output_tokens": "completion_tokens",
                "total_tokens": "total_tokens",
            }
            for source, target in mapping.items():
                value = usage_metadata.get(source)
                if isinstance(value, (int, float)):
                    usage[target] = int(value)
        return {
            "id": message.id or metadata.get("id"),
            "choices": [{"message": {"content": content}}],
            "usage": usage,
        }

    @staticmethod
    def _langchain_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, str):
                    texts.append(block)
                elif isinstance(block, dict) and isinstance(block.get("text"), str):
                    texts.append(block["text"])
            return "".join(texts)
        return ""

    @staticmethod
    def _content(response: dict[str, Any]) -> str:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelClientError("model response has no assistant content") from exc
        if not isinstance(content, str) or not content.strip():
            raise ModelClientError("model assistant content is empty")
        return content

    @staticmethod
    def _safe_previous_content(response: dict[str, Any]) -> str:
        try:
            content = StructuredModelClient._content(response)
        except ModelClientError:
            return "{}"
        return content[:12000]

    @staticmethod
    def _strip_fence(content: str) -> str:
        stripped = content.strip()
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
        return match.group(1).strip() if match else stripped
