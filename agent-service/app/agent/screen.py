"""Deterministic prompt-injection screen for customer-written text (memos, owner names).

The model's own `injection_detected` flag can't be trusted to gate auto-clear: a model that has been
swayed by an injection is exactly the one that won't report it. This screen is plain code. The agent
runs it on every case, and any hit blocks auto-clear, whatever the model concluded.

It looks for generic signs of text written *at an AI or a reviewer*, not for specific known
payloads: chat-role markers and template tokens, attempts to override instructions, messages
addressed to the AI/reviewer, dictated verdicts or scores, and requests to skip review. A false
positive only sends a case to a human, so the screen leans towards flagging. It is a heuristic: a
determined attacker can phrase around it, so it narrows the hole rather than closing it.
"""

import json
import re
from typing import Any

_F = re.IGNORECASE

# Each signal on its own is enough: ordinary payment memos don't contain any of these.
SIGNALS: dict[str, re.Pattern[str]] = {
    # "SYSTEM:", "assistant:", "developer:" turn markers, chat-template tokens, fake closing tags.
    "role_marker": re.compile(
        r"(^|[\s\]\[>)(\"'])(system|assistant|developer|user|human|ai)\s*:"
        r"|<\|[a-z_]+\|>|\[/?inst\]|#{2,}\s*(instruction|system|response)"
        r"|</?\s*(memo|system|data|untrusted[_a-z]*|tool[_a-z]*|instructions?|context|user|assistant)\s*>",
        _F),
    # "ignore all previous instructions", "disregard the rules above", "forget your guidelines".
    "instruction_override": re.compile(
        r"\b(ignore|disregard|forget|override|bypass|skip)\b[^.\n]{0,40}"
        r"\b(instructions?|rules|prompts?|guidelines|guidance|directions|directives|policy|policies|checks?)\b",
        _F),
    # "Note to AI analyst", "Dear reviewer", "attention model", "as an AI", "you are the reviewer".
    # Not "to agent" alone: "Commission to agent" is an ordinary memo.
    "addressed_to_ai": re.compile(
        r"\b(note|message|instructions?|attention|dear|hey|hello|hi)\b[\s:,-]{1,5}((to|for)\s+)?"
        r"(the\s+|any\s+|all\s+)?"
        r"(ai|a\.i\.|llm|language model|model|assistant|chatbot|bot|gpt|reviewer|analyst|investigator|agent)s?\b"
        r"|\bas an? (ai|language model|llm)\b|\byou are (now )?(an? |the )?(ai|assistant|reviewer|analyst|model)\b",
        _F),
    # "verdict=CLEAR", "risk_score: 0", "confidence 1.0", "injection_detected false".
    "dictated_field": re.compile(
        r"\b(verdict|risk[_ ]?score|confidence|injection[_ ]detected|red[_ ]flags)\b\s*[=:]?\s*"
        r"(clear|escalate|recommend_freeze|freeze|true|false|[01](\.\d+)?)\b",
        _F),
    # "output CLEAR", "mark this as safe", "classify as low risk", "recommend FREEZE immediately".
    # Output verbs take a verdict word directly ("output CLEAR"); everyday verbs need "as"
    # ("mark this as safe"), so "Return of advance - cleared" or "Mark - safe locker" don't trip it.
    "dictated_verdict": re.compile(
        r"\b(output|respond|answer|reply|recommend|classify|verdict)\b[^.\n]{0,30}"
        r"\b(clear|safe|low[- ]risk|legit(imate)?|not suspicious|no risk|freeze|fraud(ulent)?|high[- ]risk"
        r"|recommend_freeze|escalate)\b"
        r"|\b(mark|set|treat|flag|label|rate|report|consider)\b[^.\n]{0,25}\bas\s+(clear(ed)?|safe|low[- ]risk"
        r"|legit(imate)?|not suspicious|genuine|verified|fraud(ulent)?|high[- ]risk|suspicious)\b",
        _F),
    # "auto-clear any alerts", "do not escalate", "no need to review", "admin override", "whitelisted".
    # Not "pre-approved": "Pre-approved loan EMI" is an everyday bank memo.
    "review_bypass": re.compile(
        r"\bauto[- ]?(clear|approve)|\b(do not|don'?t|never|no need to)\s+(escalate|review|flag|investigate)"
        r"|\bskip\s+(the\s+)?(review|investigation)|\b(admin|compliance|system)\s+override\b"
        r"|\bwhite[- ]?listed\b",
        _F),
}


def screen_text(text: str | None) -> list[str]:
    """Names of the signals `text` trips, in SIGNALS order. Empty: nothing found."""
    if not text:
        return []
    return [name for name, pattern in SIGNALS.items() if pattern.search(text)]


def untrusted_texts(tool_output: str) -> list[str]:
    """Every customer-written string in a tool result: the MCP server wraps them as
    {"untrusted_text": ...}. Non-JSON output (errors, plain text) is screened whole."""
    try:
        data = json.loads(tool_output)
    except (ValueError, TypeError):
        return [tool_output] if tool_output else []
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("untrusted_text"), str):
                found.append(node["untrusted_text"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


def screen_tool_output(tool_output: str) -> list[dict[str, Any]]:
    """Hits in one tool result: [{"text": <first 200 chars>, "signals": [...]}, ...]."""
    hits = []
    for text in untrusted_texts(tool_output):
        signals = screen_text(text)
        if signals:
            hits.append({"text": text[:200], "signals": signals})
    return hits
