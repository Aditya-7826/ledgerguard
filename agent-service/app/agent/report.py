"""Report schema, parsing, and the citation validator."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    claim: str = Field(min_length=1, max_length=600)
    transfer_ids: list[UUID] = Field(default_factory=list, max_length=20)


class ReportDraft(BaseModel):
    """What the model writes. ``as_of`` is set by the agent from tool outputs, not trusted from the model."""

    model_config = ConfigDict(extra="ignore")
    verdict: Literal["CLEAR", "ESCALATE", "RECOMMEND_FREEZE"]
    risk_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)
    red_flags: list[str] = Field(default_factory=list, max_length=20)
    injection_detected: bool


class Report(ReportDraft):
    as_of: datetime


class ReportParseError(ValueError):
    pass


def parse_report(text: str) -> ReportDraft:
    """Accepts a bare JSON object, optionally inside a code fence or surrounding prose."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ReportParseError("no JSON object found")
    try:
        return ReportDraft.model_validate(json.loads(text[start:end + 1]))
    except json.JSONDecodeError as e:
        raise ReportParseError(f"invalid JSON: {e.msg} at char {e.pos}") from None
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:8])
        raise ReportParseError(f"schema violation: {problems}") from None


def collect_from_tool_output(text: str, seen_ids: set[str], as_ofs: list[str]) -> None:
    """Records every transfer id and as_of that appears in one tool result."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("transfer_id", "credit_transfer_id", "reverses_transfer_id") and isinstance(v, str):
                    seen_ids.add(v.lower())
                elif k == "transfer_ids" and isinstance(v, list):
                    seen_ids.update(str(x).lower() for x in v)
                elif k == "as_of" and isinstance(v, str):
                    as_ofs.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(data)


def invalid_citations(draft: ReportDraft, seen_ids: set[str]) -> list[str]:
    cited = [str(t).lower() for ev in draft.evidence for t in ev.transfer_ids]
    return sorted({t for t in cited if t not in seen_ids})
