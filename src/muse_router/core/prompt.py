import json
import re
from typing import Any

TOOL_INSTRUCTION = (
    "If you need to call a function, output ONLY a block in exactly this format "
    "and nothing else for that call:\n"
    "<tool_call>\n"
    '{"name": "function_name", "arguments": {"arg1": "val1"}}\n'
    "</tool_call>"
)

_TOOL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def _role(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("role", ""))
    return str(getattr(msg, "role", "") or "")


def _content(msg: Any) -> str:
    raw = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    return extract_text(raw)


def _tool_call_id(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("tool_call_id", "") or "")
    return str(getattr(msg, "tool_call_id", "") or "")


def extract_text(content: Any) -> str:
    """Extract plain text from an OpenAI message content (str or multimodal list)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content)


def build_prompt(messages: list[Any], tools: list[Any] | None = None, tool_choice: Any = None) -> str:
    """Stateless context serialization (SPEC §3.4)."""
    if not messages:
        return ""
    roles = [_role(m) for m in messages]
    # Single-user fast path: verbatim, only when no system/developer and no tools.
    if len(messages) == 1 and roles[0] == "user" and not tools:
        return _content(messages[0])

    system_parts: list[str] = []
    for m in messages:
        if _role(m) in ("system", "developer"):
            system_parts.append(_content(m))
    if tools:
        system_parts.append(TOOL_INSTRUCTION)

    # Last user message is the current message; everything before it is history.
    last_user_idx = -1
    for i, m in enumerate(messages):
        if _role(m) == "user":
            last_user_idx = i
    history_lines: list[str] = []
    current = ""
    if last_user_idx >= 0:
        current = _content(messages[last_user_idx])
        for m in messages[:last_user_idx]:
            r = _role(m)
            if r in ("system", "developer"):
                continue
            if r == "user":
                history_lines.append(f"User: {_content(m)}")
            elif r == "assistant":
                history_lines.append(f"Assistant: {_content(m)}")
            elif r == "tool":
                history_lines.append(f"Tool ({_tool_call_id(m)}): {_content(m)}")
            else:
                history_lines.append(f"{r}: {_content(m)}")
    else:
        for m in messages:
            if _role(m) in ("system", "developer"):
                continue
            history_lines.append(f"{_role(m)}: {_content(m)}")

    system_block = "\n".join(system_parts)
    history_block = "\n".join(history_lines)
    return (
        "<|system_instructions|>\n"
        f"{system_block}\n"
        "</|system_instructions|>\n\n"
        "<|conversation_history|>\n"
        f"{history_block}\n"
        "</|conversation_history|>\n\n"
        "<|current_user_message|>\n"
        f"{current}\n"
        "</|current_user_message|>\n"
        "Please respond directly to the current_user_message as the Assistant, without repeating the XML tags."
    )


def parse_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Return (cleaned_text, tool_calls). tool_calls are OpenAI-shaped dicts."""
    tool_calls: list[dict] = []
    cleaned = text
    for match in _TOOL_RE.finditer(text):
        raw = match.group(1).strip()
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        name = parsed.get("name") if isinstance(parsed, dict) else None
        arguments = parsed.get("arguments", {}) if isinstance(parsed, dict) else {}
        if not name:
            continue
        if isinstance(arguments, dict):
            args_str = json.dumps(arguments, ensure_ascii=False)
        else:
            args_str = str(arguments)
        from ..utils.id_gen import tool_call_id

        tool_calls.append(
            {
                "id": tool_call_id(),
                "type": "function",
                "function": {"name": name, "arguments": args_str},
            }
        )
    if tool_calls:
        cleaned = _TOOL_RE.sub("", text).strip()
    return cleaned, tool_calls


# Backwards-friendly alias
extract_tool_calls = parse_tool_calls
