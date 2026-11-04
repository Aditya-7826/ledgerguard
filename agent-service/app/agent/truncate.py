"""Shrinks tool output before it reaches the model, keeping JSON valid.

Small local models have small context windows. A long transfer list is cut item by item (longest list
first) and annotated with how much was left out, so the model knows it saw a partial view. Citation
checks use exactly what the model saw, so it can never cite a transfer it was not shown.
"""

import json
from typing import Any

NOTE_KEY = "_truncated"


def _lists(node: Any, path: tuple = ()) -> list[tuple[tuple, list]]:
    found: list[tuple[tuple, list]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            found += _lists(v, path + (k,))
    elif isinstance(node, list):
        if len(node) > 1:
            found.append((path, node))
        for i, v in enumerate(node):
            found += _lists(v, path + (i,))
    return found


def truncate_tool_output(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, (dict, list)):
        omitted: dict[str, int] = {}
        while True:
            dumped = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
            if len(dumped) <= max_chars:
                break
            lists = _lists(data)
            if not lists:
                break
            path, longest = max(lists, key=lambda pl: len(json.dumps(pl[1])))
            drop = max(1, len(longest) // 4)
            del longest[len(longest) - drop:]
            key = ".".join(map(str, path)) or "items"
            omitted[key] = omitted.get(key, 0) + drop
        if omitted:
            note = {k: f"{n} more item(s) omitted to fit the context window" for k, n in omitted.items()}
            if isinstance(data, dict):
                data[NOTE_KEY] = note
            else:
                data = {"items": data, NOTE_KEY: note}
            dumped = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        if len(dumped) <= max_chars + 400:  # the note itself may push it slightly over
            return dumped
    cut = text[:max_chars]
    return cut + f"\n...[truncated: {len(text) - max_chars} more characters omitted to fit the context window]"
