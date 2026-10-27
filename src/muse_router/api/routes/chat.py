import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ...core.prompt import build_prompt, extract_text, parse_tool_calls
from ...utils.id_gen import chat_completion_id, completion_id
from ...utils.tokenizer import count_tokens
from ..deps import get_gateway, require_api_key
from ..schemas import ChatCompletionRequest, CompletionRequest
from ...core.activity import activity_from_app
from ...core.gateway import Gateway

router = APIRouter()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chat_chunk(cid: str, model: str, fingerprint: str, delta: dict, finish: str | None, usage: dict | None = None) -> dict:
    chunk: dict[str, Any] = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "system_fingerprint": fingerprint,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        chunk["usage"] = usage
    return chunk


async def _run_gateway(gateway: Gateway, prompt: str, model: str, session_key: str | None):
    return await gateway.execute_with_failover(prompt=prompt, model=model, session_key=session_key)


@router.post("/v1/chat/completions", dependencies=[Depends(require_api_key)])
async def chat_completions(request: Request, payload: ChatCompletionRequest, gateway: Gateway = Depends(get_gateway)):
    activity = activity_from_app(request.app)
    started = time.monotonic()
    prompt = build_prompt(payload.messages, tools=payload.tools, tool_choice=payload.tool_choice)
    try:
        account, stream = await _run_gateway(gateway, prompt, payload.model, payload.user)
    except Exception as exc:
        # Gateway raises before the first chunk: failover already exhausted.
        activity.record(kind="chat", model=payload.model, account_id=None, status="failed",
                        duration_ms=(time.monotonic() - started) * 1000, ttft_ms=None,
                        prompt_tokens=count_tokens(prompt), completion_tokens=0, error=str(exc))
        raise
    # execute_with_failover only returns after the first chunk is prefetched,
    # so the time to here is the time-to-first-token from the client's view.
    ttft_ms = (time.monotonic() - started) * 1000
    fingerprint = f"fp_muse_{account.id}"
    cid = chat_completion_id()
    created = int(time.time())

    if not payload.stream:
        try:
            full = ""
            async for piece in stream:
                full += piece
        except Exception as exc:
            activity.record(kind="chat", model=payload.model, account_id=account.id, status="failed",
                            duration_ms=(time.monotonic() - started) * 1000, ttft_ms=ttft_ms,
                            prompt_tokens=count_tokens(prompt), completion_tokens=0, error=str(exc))
            raise
        cleaned, tool_calls = parse_tool_calls(full) if payload.tools else (full, [])
        if tool_calls:
            message: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": tool_calls}
            finish = "tool_calls"
            completion_text = json.dumps(tool_calls, ensure_ascii=False)
        else:
            message = {"role": "assistant", "content": cleaned}
            finish = "stop"
            completion_text = cleaned
        usage = {
            "prompt_tokens": count_tokens(prompt),
            "completion_tokens": count_tokens(completion_text),
            "total_tokens": count_tokens(prompt) + count_tokens(completion_text),
        }
        activity.record(kind="chat", model=payload.model, account_id=account.id, status="success",
                        duration_ms=(time.monotonic() - started) * 1000, ttft_ms=ttft_ms,
                        prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"])
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": payload.model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": usage,
            "system_fingerprint": fingerprint,
        }

    include_usage = bool(payload.stream_options and payload.stream_options.get("include_usage"))

    stream_state: dict[str, Any] = {"text": "", "error": None}

    async def event_stream_impl():
        if payload.tools:
            # Collect first: a <tool_call> block may be split across chunks.
            full = ""
            async for piece in stream:
                full += piece
            stream_state["text"] = full
            cleaned, tool_calls = parse_tool_calls(full)
            yield _sse(_chat_chunk(cid, payload.model, fingerprint, {"role": "assistant", "content": ""}, None))
            if tool_calls:
                for tc in tool_calls:
                    yield _sse(_chat_chunk(cid, payload.model, fingerprint, {"tool_calls": [tc]}, None))
                yield _sse(_chat_chunk(cid, payload.model, fingerprint, {}, "tool_calls"))
                usage = {
                    "prompt_tokens": count_tokens(prompt),
                    "completion_tokens": count_tokens(full),
                    "total_tokens": count_tokens(prompt) + count_tokens(full),
                } if include_usage else None
                if include_usage:
                    yield _sse({"id": cid, "object": "chat.completion.chunk", "created": created,
                                "model": payload.model, "system_fingerprint": fingerprint,
                                "choices": [], "usage": usage})
                yield "data: [DONE]\n\n"
                return
            if cleaned:
                yield _sse(_chat_chunk(cid, payload.model, fingerprint, {"content": cleaned}, None))
            yield _sse(_chat_chunk(cid, payload.model, fingerprint, {}, "stop"))
            if include_usage:
                usage = {
                    "prompt_tokens": count_tokens(prompt),
                    "completion_tokens": count_tokens(cleaned),
                    "total_tokens": count_tokens(prompt) + count_tokens(cleaned),
                }
                yield _sse({"id": cid, "object": "chat.completion.chunk", "created": created,
                            "model": payload.model, "system_fingerprint": fingerprint,
                            "choices": [], "usage": usage})
            yield "data: [DONE]\n\n"
            return

        yield _sse(_chat_chunk(cid, payload.model, fingerprint, {"role": "assistant", "content": ""}, None))
        collected = ""
        async for piece in stream:
            collected += piece
            yield _sse(_chat_chunk(cid, payload.model, fingerprint, {"content": piece}, None))
        stream_state["text"] = collected
        yield _sse(_chat_chunk(cid, payload.model, fingerprint, {}, "stop"))
        if include_usage:
            usage = {
                "prompt_tokens": count_tokens(prompt),
                "completion_tokens": count_tokens(collected),
                "total_tokens": count_tokens(prompt) + count_tokens(collected),
            }
            yield _sse({"id": cid, "object": "chat.completion.chunk", "created": created,
                        "model": payload.model, "system_fingerprint": fingerprint,
                        "choices": [], "usage": usage})
        yield "data: [DONE]\n\n"

    async def event_stream():
        # Wrapper only: it yields impl chunks unchanged (no extra buffering
        # of the SSE payload) and records the activity event in finally,
        # after the stream has been fully delivered or has failed.
        try:
            async for chunk in event_stream_impl():
                yield chunk
        except Exception as exc:
            stream_state["error"] = str(exc)
            raise
        finally:
            error = stream_state["error"]
            activity.record(kind="chat", model=payload.model, account_id=account.id,
                            status="failed" if error else "success",
                            duration_ms=(time.monotonic() - started) * 1000, ttft_ms=ttft_ms,
                            prompt_tokens=count_tokens(prompt),
                            completion_tokens=count_tokens(stream_state["text"]) if stream_state["text"] else 0,
                            error=error)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/v1/completions", dependencies=[Depends(require_api_key)])
async def completions(request: Request, payload: CompletionRequest, gateway: Gateway = Depends(get_gateway)):
    activity = activity_from_app(request.app)
    started = time.monotonic()
    if isinstance(payload.prompt, list):
        prompt = "\n".join(str(p) for p in payload.prompt)
    else:
        prompt = str(payload.prompt)
    try:
        account, stream = await _run_gateway(gateway, prompt, payload.model, payload.user)
    except Exception as exc:
        activity.record(kind="completion", model=payload.model, account_id=None, status="failed",
                        duration_ms=(time.monotonic() - started) * 1000, ttft_ms=None,
                        prompt_tokens=count_tokens(prompt), completion_tokens=0, error=str(exc))
        raise
    ttft_ms = (time.monotonic() - started) * 1000
    fingerprint = f"fp_muse_{account.id}"
    cid = completion_id()
    created = int(time.time())

    if not payload.stream:
        try:
            full = ""
            async for piece in stream:
                full += piece
        except Exception as exc:
            activity.record(kind="completion", model=payload.model, account_id=account.id, status="failed",
                            duration_ms=(time.monotonic() - started) * 1000, ttft_ms=ttft_ms,
                            prompt_tokens=count_tokens(prompt), completion_tokens=0, error=str(exc))
            raise
        usage = {
            "prompt_tokens": count_tokens(prompt),
            "completion_tokens": count_tokens(full),
            "total_tokens": count_tokens(prompt) + count_tokens(full),
        }
        activity.record(kind="completion", model=payload.model, account_id=account.id, status="success",
                        duration_ms=(time.monotonic() - started) * 1000, ttft_ms=ttft_ms,
                        prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"])
        return {
            "id": cid,
            "object": "text_completion",
            "created": created,
            "model": payload.model,
            "choices": [{"text": full, "index": 0, "logprobs": None, "finish_reason": "stop"}],
            "usage": usage,
            "system_fingerprint": fingerprint,
        }

    include_usage = bool(payload.stream_options and payload.stream_options.get("include_usage"))

    stream_state: dict[str, Any] = {"text": "", "error": None}

    async def event_stream_impl():
        collected = ""
        async for piece in stream:
            collected += piece
            yield _sse({"id": cid, "object": "text_completion", "created": created, "model": payload.model,
                        "system_fingerprint": fingerprint,
                        "choices": [{"text": piece, "index": 0, "finish_reason": None}]})
        stream_state["text"] = collected
        yield _sse({"id": cid, "object": "text_completion", "created": created, "model": payload.model,
                    "system_fingerprint": fingerprint,
                    "choices": [{"text": "", "index": 0, "finish_reason": "stop"}]})
        if include_usage:
            yield _sse({"id": cid, "object": "text_completion", "created": created, "model": payload.model,
                        "system_fingerprint": fingerprint, "choices": [],
                        "usage": {"prompt_tokens": count_tokens(prompt),
                                  "completion_tokens": count_tokens(collected),
                                  "total_tokens": count_tokens(prompt) + count_tokens(collected)}})
        yield "data: [DONE]\n\n"

    async def event_stream():
        try:
            async for chunk in event_stream_impl():
                yield chunk
        except Exception as exc:
            stream_state["error"] = str(exc)
            raise
        finally:
            error = stream_state["error"]
            activity.record(kind="completion", model=payload.model, account_id=account.id,
                            status="failed" if error else "success",
                            duration_ms=(time.monotonic() - started) * 1000, ttft_ms=ttft_ms,
                            prompt_tokens=count_tokens(prompt),
                            completion_tokens=count_tokens(stream_state["text"]) if stream_state["text"] else 0,
                            error=error)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)
