from muse_router.core.prompt import build_prompt, extract_text, parse_tool_calls


def test_single_user_verbatim():
    assert build_prompt([{"role": "user", "content": "Hello there"}]) == "Hello there"


def test_multi_turn_and_system_format():
    prompt = build_prompt([
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "How are you?"},
    ])
    assert "<|system_instructions|>" in prompt
    assert "You are helpful." in prompt
    assert "<|conversation_history|>" in prompt
    assert "User: Hi" in prompt
    assert "Assistant: Hello!" in prompt
    assert "<|current_user_message|>" in prompt
    assert "How are you?" in prompt


def test_multimodal_content_extraction():
    content = [{"type": "text", "text": "Hello "}, {"type": "image_url", "image_url": {"url": "x"}}, {"type": "text", "text": "world"}]
    assert extract_text(content) == "Hello world"
    assert build_prompt([{"role": "user", "content": content}]) == "Hello world"


def test_tool_instruction_and_parser():
    prompt = build_prompt([{"role": "user", "content": "Weather?"}], tools=[{"type": "function", "function": {"name": "get_weather"}}])
    assert "<tool_call>" in prompt
    cleaned, calls = parse_tool_calls('<tool_call>{"name": "get_weather", "arguments": {"city": "Hanoi"}}</tool_call>')
    assert cleaned == ""
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "get_weather"
    assert calls[0]["function"]["arguments"] == '{"city": "Hanoi"}'
    assert calls[0]["id"].startswith("call_")
    cleaned2, calls2 = parse_tool_calls("Just text, no tool.")
    assert cleaned2 == "Just text, no tool." and calls2 == []
