from tests.conftest import make_client, make_account


def test_first_chunk_failover_streaming(tmp_path):
    client, pool, _ = make_client(tmp_path, [
        make_account("acc_bad", "expired_token"),
        make_account("acc_good", "valid_token"),
    ])
    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test-key"},
        json={"model": "muse-spark", "messages": [{"role": "user", "content": "Hello"}], "stream": True},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert resp.text.strip().endswith("data: [DONE]")
    bad = pool.get_account("acc_bad")
    good = pool.get_account("acc_good")
    assert bad.status.value == "expired"
    assert good.success_requests == 1
    assert bad.active_leases == 0
    assert good.active_leases == 0
