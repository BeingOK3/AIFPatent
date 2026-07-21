from __future__ import annotations

from typing import Any

from .context import AssembledModelContext


class LangChainAdapterUnavailable(RuntimeError):
    pass


def to_message_dicts(context: AssembledModelContext) -> tuple[dict[str, str], ...]:
    """Dependency-free representation useful for adapters and audit tests."""
    return tuple({"role": message.role, "content": message.content} for message in context.messages)


def to_langchain_messages(context: AssembledModelContext) -> list[Any]:
    """Convert frozen domain messages to LangChain messages without changing them."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except ModuleNotFoundError as exc:
        raise LangChainAdapterUnavailable(
            "langchain-core is optional; install the runtime adapter dependency to use this bridge"
        ) from exc
    result: list[Any] = []
    for message in context.messages:
        if message.role == "system":
            result.append(SystemMessage(content=message.content))
        elif message.role == "user":
            result.append(HumanMessage(content=message.content))
        else:  # pragma: no cover - ModelMessage already constrains roles.
            raise ValueError(f"unsupported message role: {message.role}")
    return result


__all__ = ["LangChainAdapterUnavailable", "to_langchain_messages", "to_message_dicts"]
