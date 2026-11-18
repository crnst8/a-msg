"""Browser access: password and authenticator sign-in, the session cookie, the proxy listener and the interface."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from amsg.api.app import create_app
from amsg.config import Settings
from amsg.db.models import DeviceCredential
from amsg.security import passwords, totp

ORIGIN = "https://ag.test"
PASSWORD = "correct horse battery"


def test_totp_matches_rfc_6238_vectors() -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
    assert totp.code_at(secret, 59 // 30) == "287082"
    assert totp.code_at(secret, 1111111109 // 30) == "081804"
    assert totp.match(secret, "287082", moment=59) == 1
    assert totp.match(secret, "287 082", moment=59 + 30) == 1  # one step late is accepted
    assert totp.match(secret, "287082", moment=59 + 90) is None
    assert totp.match(secret, "12345", moment=59) is None
    assert totp.provisioning_uri(secret, "ag.test").startswith("otpauth://totp/a-msg%3Aag.test?secret=")


def test_supplied_secrets_are_checked() -> None:
    assert totp.normalize("gezd gnbv gy3t qojq gezd gnbv gy3t qojq") == "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    for bad in ("SHORT", "GEZDGNBVGY3TQOJQGEZDGNBVG1", "not base32 at all!!!!!!!!!!!"):
        with pytest.raises(ValueError):
            totp.normalize(bad)


def test_passwords_hash_and_verify() -> None:
    stored = passwords.hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert passwords.verify_password(PASSWORD, stored)
    assert not passwords.verify_password("wrong", stored)
    assert not passwords.verify_password(PASSWORD, None)
    assert not passwords.verify_password(PASSWORD, "garbage")


def _app(tmp_path: Path, **overrides):  # type: ignore[no-untyped-def]
    values = {"data_dir": tmp_path / "data", "provider": "fake", "dev_mode": True, **overrides}
    return create_app(Settings(**values))


@pytest.fixture
def web(tmp_path: Path) -> Iterator[tuple[TestClient, object, str]]:
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html><head><style>.splash{opacity:1}</style></head><body></body></html>")
    (root / "assets" / "index-AbCdEf12.js").write_text("console.log(1)")
    (root / "assets" / "fixed.svg").write_text("<svg/>")
    (root / "sw.js").write_text("// push worker")
    app = _app(tmp_path, web_origin=ORIGIN, web_listen="127.0.0.1:443", web_root=root, trusted_proxies=["testclient"])
    domain = app.state.services.domain
    domain.web_set_password(PASSWORD)
    secret = domain.web_new_totp()
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        yield client, domain, secret


def _sign_in(client: TestClient, secret: str, step_offset: int = 0, password: str = PASSWORD):  # type: ignore[no-untyped-def]
    code = totp.code_at(secret, totp.current_step() + step_offset)
    return client.post(
        "/v1/web/sign-in",
        json={"password": password, "code": code, "device_name": "Safari on iPhone"},
        headers={"Origin": ORIGIN},
    )


def test_web_is_off_unless_an_origin_is_set(tmp_path: Path) -> None:
    app = _app(tmp_path)
    domain = app.state.services.domain
    token = domain.issue_device_token("browser")
    with TestClient(app) as client:
        assert client.get("/v1/web/session").status_code == 404
        client.cookies.set("__Host-amsg", token)
        assert client.get("/v1/spaces").status_code == 401
        assert client.get("/").status_code == 404


def test_sign_in_sets_a_strict_cookie_and_the_cookie_authorises(web) -> None:  # type: ignore[no-untyped-def]
    client, domain, secret = web
    assert client.get("/v1/web/session").json() == {
        "configured": True,
        "signed_in": False,
        "service_id": None,
        "device_id": None,
    }
    assert client.get("/v1/spaces").status_code == 401
    response = _sign_in(client, secret)
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    for part in ("__Host-amsg=amsg_", "HttpOnly", "Max-Age=7776000", "Path=/", "SameSite=strict", "Secure"):
        assert part in cookie
    assert "token" not in response.json()
    session = client.get("/v1/web/session").json()
    assert session["signed_in"] and session["device_id"] == response.json()["device_id"]
    assert client.get("/v1/spaces").status_code == 200
    devices = client.get("/v1/devices").json()
    assert any(d["name"] == "Safari on iPhone" and d["current"] for d in devices)


def test_cookie_writes_need_the_page_origin(web) -> None:  # type: ignore[no-untyped-def]
    client, _, secret = web
    _sign_in(client, secret)
    body = {"title": "From the browser"}
    assert client.post("/v1/spaces", json=body).json()["error"]["code"] == "cross_origin"
    assert client.post("/v1/spaces", json=body, headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.post("/v1/spaces", json=body, headers={"Origin": ORIGIN}).status_code == 201


def test_wrong_factors_fail_alike_and_codes_work_once(web) -> None:  # type: ignore[no-untyped-def]
    client, _, secret = web
    wrong_password = _sign_in(client, secret, password="nope")
    wrong_code = client.post(
        "/v1/web/sign-in", json={"password": PASSWORD, "code": "000000"}, headers={"Origin": ORIGIN}
    )
    assert wrong_password.status_code == wrong_code.status_code == 401
    assert wrong_password.json()["error"]["message"] == wrong_code.json()["error"]["message"]
    assert _sign_in(client, secret).status_code == 200
    client.cookies.clear()
    replay = _sign_in(client, secret)
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "code_used"
    assert _sign_in(client, secret, step_offset=-1).json()["error"]["code"] == "code_used"


def test_sign_in_is_refused_from_another_origin(web) -> None:  # type: ignore[no-untyped-def]
    client, _, secret = web
    code = totp.code_at(secret, totp.current_step())
    response = client.post("/v1/web/sign-in", json={"password": PASSWORD, "code": code})
    assert response.status_code == 403


def test_attempts_are_limited_per_forwarded_client(web) -> None:  # type: ignore[no-untyped-def]
    client, _, secret = web
    body = {"password": "nope", "code": "000000"}
    for _ in range(5):
        response = client.post("/v1/web/sign-in", json=body, headers={"Origin": ORIGIN, "X-Real-IP": "203.0.113.9"})
        assert response.status_code == 401
    blocked = client.post("/v1/web/sign-in", json=body, headers={"Origin": ORIGIN, "X-Real-IP": "203.0.113.9"})
    assert blocked.status_code == 429
    other = client.post("/v1/web/sign-in", json=body, headers={"Origin": ORIGIN, "X-Real-IP": "203.0.113.10"})
    assert other.status_code == 401


def test_sign_out_revokes_the_session(web) -> None:  # type: ignore[no-untyped-def]
    client, domain, secret = web
    device_id = _sign_in(client, secret).json()["device_id"]
    assert client.post("/v1/web/sign-out").status_code == 403
    response = client.post("/v1/web/sign-out", headers={"Origin": ORIGIN})
    assert response.status_code == 204 and "__Host-amsg=" in response.headers["set-cookie"]
    with domain.db.read() as session:
        assert session.get(DeviceCredential, device_id).revoked_at is not None


def test_old_and_idle_sessions_expire(web) -> None:  # type: ignore[no-untyped-def]
    client, domain, secret = web
    device_id = _sign_in(client, secret).json()["device_id"]
    with domain.db.write() as session:
        session.get(DeviceCredential, device_id).last_seen_at = "2020-01-01T00:00:00.000Z"
    assert client.get("/v1/spaces").status_code == 401
    with domain.db.read() as session:
        assert session.get(DeviceCredential, device_id).revoked_at is not None


def test_device_tokens_do_not_work_as_cookies(web) -> None:  # type: ignore[no-untyped-def]
    client, domain, _ = web
    client.cookies.set("__Host-amsg", domain.issue_device_token("mac"))
    assert client.get("/v1/spaces").status_code == 401


def test_proxy_listener_checks_host_and_hides_connector_routes(web) -> None:  # type: ignore[no-untyped-def]
    client, _, _ = web
    assert client.get("/v1/connector/poll").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/v1/web/session", headers={"Host": "other.test"}).status_code == 421


def test_proxy_listener_accepts_only_the_configured_proxies(tmp_path: Path) -> None:
    app = _app(tmp_path, web_origin=ORIGIN, web_listen="127.0.0.1:443", trusted_proxies=["192.0.2.1"])
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/v1/web/session").status_code == 403
    with TestClient(app, base_url="http://127.0.0.1:80") as client:
        assert client.get("/health/live").status_code == 200


def test_interface_is_served_with_a_strict_policy(web) -> None:  # type: ignore[no-untyped-def]
    client, _, _ = web
    page = client.get("/")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"
    policy = page.headers["content-security-policy"]
    assert "script-src 'self';" in policy and "'unsafe-inline'" not in policy and "'sha256-" in policy
    assert "frame-ancestors 'none'" in policy
    assert page.headers["x-frame-options"] == "DENY"
    assert "immutable" in client.get("/assets/index-AbCdEf12.js").headers["cache-control"]
    assert client.get("/assets/fixed.svg").headers["cache-control"] == "public, max-age=86400"
    assert client.get("/v1/web/session").headers["cache-control"] == "no-store"


def test_push_requires_web_session_and_origin(web) -> None:  # type: ignore[no-untyped-def]
    client, domain, secret = web
    assert client.get("/v1/web/push").status_code == 401
    _sign_in(client, secret)
    status = client.get("/v1/web/push").json()
    assert len(status["public_key"]) == 87 and not status["enabled"]
    body = {
        "subscription": {"endpoint": "https://web.push.apple.com/test", "keys": {"p256dh": "A" * 87, "auth": "B" * 22}},
        "preferences": {},
    }
    assert client.post("/v1/web/push", json=body).status_code == 403
    assert client.post("/v1/web/push", json=body, headers={"Origin": ORIGIN}).status_code == 204
    assert client.get("/v1/web/push").json()["enabled"]
    assert client.delete("/v1/web/push", headers={"Origin": ORIGIN}).status_code == 204
    assert not client.get("/v1/web/push").json()["enabled"]
    body["subscription"]["endpoint"] = "https://127.0.0.1/private"
    assert client.post("/v1/web/push", json=body, headers={"Origin": ORIGIN}).status_code == 422
    worker = client.get("/sw.js")
    assert worker.status_code == 200 and worker.headers["cache-control"] == "no-store"
    assert worker.headers["cdn-cache-control"] == "no-store"
    assert "worker-src 'self'" in client.get("/").headers["content-security-policy"]


def test_push_delivery_preferences_expiry_and_revocation(web, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json
    from types import SimpleNamespace

    from amsg.db.models import Meta
    from amsg.domain.base import now
    from amsg.push import PREFIX

    client, domain, secret = web
    device_id = _sign_in(client, secret).json()["device_id"]
    body = {
        "subscription": {"endpoint": "https://web.push.apple.com/test", "keys": {"p256dh": "A" * 87, "auth": "B" * 22}},
        "preferences": {"notifyFailures": False},
    }
    client.post("/v1/web/push", json=body, headers={"Origin": ORIGIN})
    calls = []
    monkeypatch.setattr("amsg.push.webpush", lambda *a, **kw: calls.append(kw))
    push = client.app.state.push
    event = SimpleNamespace(
        seq=99999,
        type="run.failed",
        created_at=now(),
        payload={
            "space": {"id": "space-1", "title": "General", "kind": "general"},
            "message": {"markdown": "## Done\n\nThe **build** passed. See [the log](https://example.test/log)."},
        },
    )
    push.send_event(event)
    assert not calls
    event.type = "run.completed"
    push.send_event(event)
    assert len(calls) == 1
    sent = json.loads(calls[0]["data"])
    assert sent["spaceId"] == "space-1"
    assert sent["title"] == "a-msg"
    assert sent["body"] == "Done The build passed. See the log."
    assert calls[0]["ttl"] == 3600 and calls[0]["timeout"] == 10
    assert calls[0]["vapid_private_key"]
    domain.revoke(device_id)
    push.send_event(event)
    assert len(calls) == 1
    with domain.db.read() as session:
        assert session.get(Meta, PREFIX + device_id) is None


def test_push_notification_text() -> None:
    from types import SimpleNamespace

    from amsg.push import BODY_LIMIT, notification

    def event(kind, space, message, run=None):  # type: ignore[no-untyped-def]
        return SimpleNamespace(seq=7, type=kind, payload={"space": space, "message": message, "run": run or {}})

    thread = {"id": "s2", "title": "Trip planning", "kind": "thread"}
    sent = notification(event("run.completed", thread, {"markdown": "- one\n- `two`\n\n```py\nx = 1\n```"}))
    assert sent == {"title": "Trip planning", "body": "one two x = 1", "spaceId": "s2", "tag": "run-7"}
    long = notification(event("run.completed", thread, {"markdown": "word " * 500}))
    assert len(long["body"]) == BODY_LIMIT and long["body"].endswith("…")
    files = notification(event("run.completed", thread, {"markdown": "", "artifacts": [{}, {}]}))
    assert files["body"] == "Sent 2 files."
    failed = notification(event("run.failed", thread, {"markdown": "partial"}, {"error_message": "Agent offline"}))
    assert failed["body"] == "Reply failed: Agent offline"
    assert notification(event("run.completed", {}, {"markdown": "x"})) is None


def test_push_key_persists_and_gone_endpoints_are_removed(web, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from pywebpush import WebPushException
    from requests import Response

    from amsg.push import PushDelivery

    client, domain, secret = web
    _sign_in(client, secret)
    public = client.get("/v1/web/push").json()["public_key"]
    assert PushDelivery(domain).public == public
    client.post(
        "/v1/web/push",
        headers={"Origin": ORIGIN},
        json={
            "subscription": {
                "endpoint": "https://web.push.apple.com/test",
                "keys": {"p256dh": "A" * 87, "auth": "B" * 22},
            },
            "preferences": {},
        },
    )
    response = Response()
    response.status_code = 410

    def gone(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise WebPushException("Gone", response=response)

    monkeypatch.setattr("amsg.push.webpush", gone)
    client.app.state.push.send_event(
        SimpleNamespace(seq=1, type="run.completed", payload={"space": {"id": "space-1", "title": "General"}})
    )
    assert not client.get("/v1/web/push").json()["enabled"]
