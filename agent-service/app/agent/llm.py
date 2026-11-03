"""LLM factory plus two deterministic fakes (tests / keyless demo)."""

import asyncio
import json
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, PrivateAttr

from app.config import Settings

# The case prompt names the case id first, then "ACCOUNT_ID: <uuid>"; the fake must use the latter.
ACCOUNT_RE = re.compile(r"ACCOUNT_ID:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def make_llm(settings: Settings) -> BaseChatModel:
    provider = settings.llm_provider.lower()
    if provider == "fake":
        return HeuristicFakeModel()
    if not settings.llm_model:
        raise ValueError("LLM_MODEL must be set (the model is never hardcoded)")
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # reads ANTHROPIC_API_KEY from the environment

        # Retries/timeouts are handled by the agent so every attempt is traced; disable the SDK's own.
        return ChatAnthropic(model=settings.llm_model, temperature=0, max_retries=0,
                             default_request_timeout=settings.llm_timeout_s, max_tokens=4096)
    raise ValueError(f"unsupported LLM_PROVIDER {settings.llm_provider!r} (supported: anthropic, fake)")


def _usage(i: int, o: int) -> dict[str, int]:
    return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}


class ScriptedChatModel(BaseChatModel):
    """Returns scripted responses in order. An item may be an AIMessage, an Exception (raised), a
    ``("sleep", seconds)`` tuple (await, then continue with the next item), or a callable taking the
    message list. The last item repeats once the script runs out."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    script: list[Any]
    _i: int = PrivateAttr(default=0)
    _seen: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    @property
    def calls(self) -> list[list[BaseMessage]]:
        return self._seen

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _next(self) -> Any:
        item = self.script[min(self._i, len(self.script) - 1)]
        self._i += 1
        return item

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # pragma: no cover
        raise NotImplementedError("use ainvoke")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self._seen.append(list(messages))
        item = self._next()
        while isinstance(item, tuple) and item[0] == "sleep":
            await asyncio.sleep(item[1])
            item = self._next()
        if isinstance(item, Exception):
            raise item
        if callable(item) and not isinstance(item, AIMessage):
            item = item(messages)
        msg = item.model_copy()
        if not msg.usage_metadata:
            msg.usage_metadata = _usage(100, 20)
        return ChatResult(generations=[ChatGeneration(message=msg)])


class HeuristicFakeModel(BaseChatModel):
    """Keyless stand-in (LLM_PROVIDER=fake): looks at the account, then files an ESCALATE report citing
    what it saw. Deterministic; never clears and never claims fraud. Not used for eval numbers."""

    @property
    def _llm_type(self) -> str:
        return "heuristic-fake"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "HeuristicFakeModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._respond(messages))])

    def _respond(self, messages: list[BaseMessage]) -> AIMessage:
        last = messages[-1]
        tool_outputs = [m.content for m in messages if isinstance(m, ToolMessage)]
        if isinstance(last, HumanMessage) and "WRITE_REPORT" in str(last.content):
            seen: list[str] = []
            injected = False
            for out in tool_outputs:
                text = out if isinstance(out, str) else json.dumps(out)
                injected |= bool(re.search(r"(?i)ignore (all )?previous|SYSTEM:|ASSISTANT:|verdict=", text))
                for m in re.finditer(r'"transfer_id":\s*"([0-9a-f-]{36})"', text):
                    if m.group(1) not in seen:
                        seen.append(m.group(1))
            report = {
                "verdict": "ESCALATE", "risk_score": 0.5, "confidence": 0.3,
                "summary": "Heuristic fake model: rule hits need human review.",
                "evidence": [{"claim": "Recent transfers on the account", "transfer_ids": seen[:3]}] if seen else [],
                "red_flags": ["memo contains instructions to the reviewer"] if injected else [],
                "injection_detected": injected,
            }
            return AIMessage(content=json.dumps(report), usage_metadata=_usage(500, 120))
        if not tool_outputs:
            first = next((m for m in messages if isinstance(m, HumanMessage)), None)
            match = ACCOUNT_RE.search(str(first.content)) if first else None
            if match:
                acct = match.group(1)
                return AIMessage(content="", usage_metadata=_usage(400, 40), tool_calls=[
                    {"name": "get_account", "args": {"account_id": acct}, "id": "call_get_account"},
                    {"name": "list_transfers", "args": {"account_id": acct, "limit": 20}, "id": "call_list"},
                ])
        return AIMessage(content="Investigation complete.", usage_metadata=_usage(300, 10))
