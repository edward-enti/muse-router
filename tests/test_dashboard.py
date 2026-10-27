"""Dashboard tests.

Uses the same make_client fixture as the existing suites, so every
client gets a fresh app (and a fresh ActivityLog) per test. Only the
fixture keys from conftest are used; no real key or cookie appears here.
"""

from tests.conftest import make_account, make_client


def test_dashboard_pages(tmp_path):
    client, _, _ = make_client(tmp_path, [make_account("acc_good", "valid_token")])
    for path in ("/", "/dashboard"):
        r = client.get(path)
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "muse-router" in r.text
        # The static page must never embed a default key value.
        assert "sk-muse-default-key" not in r.text
        assert "sk-muse-admin-key" not in r.text


def test_dashboard_admin_endpoints_auth(tmp_path, admin_headers):
    client, _, settings = make_client(tmp_path, [make_account("acc_good", "valid_token")])
    for path in ("/admin/activity", "/admin/usage", "/admin/config"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        r = client.get(path, headers=admin_headers)
        assert r.status_code == 200, path
    config = client.get("/admin/config", headers=admin_headers).json()
    assert "api_key" not in config and "admin_key" not in config
    assert settings.api_key not in str(config.values())
    assert settings.admin_key not in str(config.values())
    assert config["port"] == 18610
    assert config["driver"] == "mock"
    assert config["pool_strategy"] == "round_robin"
    assert config["max_retries"] == 3
    assert isinstance(config["accounts_file"], str)


def test_chat_request_creates_activity_and_clear(tmp_path, api_key_headers, admin_headers):
    client, _, _ = make_client(tmp_path, [make_account("acc_good", "valid_token")])

    before = client.get("/admin/activity", headers=admin_headers).json()
    assert before["events"] == []

    r = client.post("/v1/chat/completions", headers=api_key_headers,
                    json={"model": "muse-spark",
                          "messages": [{"role": "user", "content": "Hello"}],
                          "stream": False})
    assert r.status_code == 200

    events = client.get("/admin/activity", headers=admin_headers).json()["events"]
    assert len(events) == 1
    event = events[0]
    assert event["kind"] == "chat"
    assert event["model"] == "muse-spark"
    assert event["account_id"] == "acc_good"
    assert event["status"] == "success"
    assert event["prompt_tokens"] > 0
    assert event["completion_tokens"] > 0
    assert event["ttft_ms"] is not None and event["ttft_ms"] >= 0
    assert event["duration_ms"] >= 0

    usage = client.get("/admin/usage", headers=admin_headers).json()
    assert usage["total_requests"] == 1
    assert usage["success"] == 1
    assert usage["failed"] == 0
    assert usage["by_model"]["muse-spark"]["requests"] == 1
    assert usage["by_account"]["acc_good"]["requests"] == 1
    assert usage["pool"]["total"] == 1

    # Clear requires the admin key too.
    assert client.post("/admin/activity/clear").status_code == 401
    r = client.post("/admin/activity/clear", headers=admin_headers)
    assert r.status_code == 200 and r.json() == {"cleared": True}
    assert client.get("/admin/activity", headers=admin_headers).json()["events"] == []
