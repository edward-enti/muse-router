import json

from tests.conftest import make_account, make_client


def _sse_payloads(text: str):
    out = []
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[len("data: "):]))
    return out


def test_openai_endpoints_compatibility(tmp_path, api_key_headers, admin_headers):
    client, pool, _ = make_client(tmp_path, [make_account("acc_good", "valid_token")])

    # GET /v1/models
    r = client.get("/v1/models", headers=api_key_headers)
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    assert ids == ["muse-spark", "muse-chat", "gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet-20241022"]

    # Auth failure -> 401 OpenAI error format
    r = client.get("/v1/models", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert "error" in r.json()

    # Chat completions, stream: false
    r = client.post("/v1/chat/completions", headers=api_key_headers,
                    json={"model": "muse-spark", "messages": [{"role": "user", "content": "Hello"}], "stream": False})
    assert r.status_code == 200
    body = r.json()
    assert body["id"].startswith("chatcmpl-")
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["choices"][0]["message"]["content"]
    assert body["usage"]["total_tokens"] == body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"]
    assert body["system_fingerprint"] == "fp_muse_acc_good"

    # Chat completions, stream: true (+ include_usage)
    r = client.post("/v1/chat/completions", headers=api_key_headers,
                    json={"model": "muse-spark", "messages": [{"role": "user", "content": "Hello"}], "stream": True,
                          "stream_options": {"include_usage": True}})
    assert r.status_code == 200
    assert r.text.strip().endswith("data: [DONE]")
    payloads = _sse_payloads(r.text)
    assert payloads[0]["choices"][0]["delta"]["role"] == "assistant"
    assert any(p["choices"] and p["choices"][0].get("finish_reason") == "stop" for p in payloads)
    assert any("usage" in p for p in payloads)

    # Legacy completions, both modes
    r = client.post("/v1/completions", headers=api_key_headers,
                    json={"model": "muse-spark", "prompt": "Hello", "stream": False})
    assert r.status_code == 200 and r.json()["object"] == "text_completion"
    r = client.post("/v1/completions", headers=api_key_headers,
                    json={"model": "muse-spark", "prompt": "Hello", "stream": True})
    assert r.status_code == 200 and r.text.strip().endswith("data: [DONE]")

    # Tool calling (non-streaming)
    r = client.post("/v1/chat/completions", headers=api_key_headers,
                    json={"model": "muse-spark",
                          "messages": [{"role": "user", "content": "What is the weather? Use get_weather if needed."}],
                          "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}]})
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    tool_calls = body["choices"][0]["message"]["tool_calls"]
    assert tool_calls[0]["function"]["name"] == "get_weather"
    assert json.loads(tool_calls[0]["function"]["arguments"]) == {"city": "Hanoi"}

    # Admin: add (raw cookie header), list (masked), reset
    r = client.post("/admin/accounts", headers=admin_headers,
                    json={"label": "new", "cookie_header": "hatch_sess=valid_token; hatch_gw=xyz"})
    assert r.status_code == 200
    new_id = r.json()["id"]
    r = client.get("/admin/accounts", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 2
    listed = [a for a in r.json()["accounts"] if a["id"] == new_id][0]
    assert listed["cookies"]["hatch_sess"].startswith("valid_")
    assert listed["cookies"]["hatch_sess"].endswith("oken")
    assert listed["cookies"]["hatch_sess"] != "valid_token"
    r = client.post(f"/admin/accounts/{new_id}/reset", headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "ready"

    # Health
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_admin_patch_and_delete(tmp_path, admin_headers):
    client, pool, _ = make_client(tmp_path, [make_account("acc_1", "valid_token")])
    r = client.patch("/admin/accounts/acc_1", headers=admin_headers, json={"label": "renamed", "max_concurrent": 5})
    assert r.status_code == 200 and r.json()["label"] == "renamed" and r.json()["max_concurrent"] == 5
    r = client.delete("/admin/accounts/acc_1", headers=admin_headers)
    assert r.status_code == 200
    assert pool.get_account("acc_1") is None
