import secrets


def chat_completion_id() -> str:
    return f"chatcmpl-{secrets.token_hex(12)}"


def completion_id() -> str:
    return f"cmpl-{secrets.token_hex(12)}"


def tool_call_id() -> str:
    return f"call_{secrets.token_hex(12)}"
