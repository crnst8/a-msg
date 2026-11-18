"""M7 gate: streamed uploads, safe names, range downloads, hide and permanent deletion."""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path

import pytest

from amsg.domain.artifacts import clean_filename
from amsg.errors import ApiError

from .conftest import Harness, make_harness


def upload(h: Harness, name: str = "a.bin", data: bytes = b"data", mime: str = "application/octet-stream"):  # type: ignore[no-untyped-def]
    return h.post("/v1/uploads", files={"file": (name, data, mime)})


def test_binary_round_trip_preserves_checksum(h: Harness) -> None:
    data = os.urandom(3 * 1024 * 1024 + 17)
    artifact = upload(h, "blob.bin", data).json()
    assert artifact["checksum"] == hashlib.sha256(data).hexdigest()
    response = h.get(f"/v1/artifacts/{artifact['id']}/content")
    assert hashlib.sha256(response.content).hexdigest() == artifact["checksum"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]
    assert "sandbox" in response.headers["content-security-policy"]


def test_range_download(h: Harness) -> None:
    artifact = upload(h, data=b"0123456789").json()
    url = f"/v1/artifacts/{artifact['id']}/content"
    response = h.client.get(url, headers={**h.auth, "Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert h.client.get(url, headers={**h.auth, "Range": "bytes=50-"}).status_code == 416


def test_unicode_filename_survives(h: Harness) -> None:
    artifact = upload(h, "Résumé 日本語.txt", b"x", "text/plain").json()
    assert artifact["display_name"] == "Résumé 日本語.txt"
    response = h.get(f"/v1/artifacts/{artifact['id']}/content")
    assert "filename*=UTF-8''R%C3%A9sum%C3%A9" in response.headers["content-disposition"]


@pytest.mark.parametrize("name", ["../etc/passwd", "a/b.txt", "..\\x.txt", ".."])
def test_traversal_names_rejected(h: Harness, name: str) -> None:
    assert upload(h, name).status_code == 422
    assert not list(h.settings.tmp_dir.glob("*.part"))


def test_control_characters_are_rejected() -> None:
    with pytest.raises(ApiError):
        clean_filename("a\x00b")


def test_size_limit_applies_while_streaming(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, upload_limit=1024)
    h = next(harness)
    try:
        response = upload(h, "big.bin", b"x" * 4096)
        assert response.status_code == 413
        assert not list(h.settings.tmp_dir.glob("*.part"))
        assert not list(h.settings.object_dir.glob("*/*"))
    finally:
        harness.close()


def test_incomplete_upload_is_rejected_and_cleaned(h: Harness) -> None:
    boundary = "x" * 16
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.bin"\r\n\r\npartial').encode()
    response = h.client.post(
        "/v1/uploads",
        content=body,
        headers={**h.auth, "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert response.status_code == 422
    assert not list(h.settings.tmp_dir.glob("*.part"))


def test_abandoned_temporary_files_are_swept(h: Harness) -> None:
    path = h.settings.tmp_dir / "stale.part"
    path.write_bytes(b"x")
    os.utime(path, (time.time() - 90000, time.time() - 90000))
    h.domain.store.sweep_temporary()
    assert not path.exists()


def test_unreferenced_object_is_collected(h: Harness) -> None:
    stored = h.domain.store.put_bytes(b"orphan")
    path = h.domain.store.path_for(stored.key)
    os.utime(path, (time.time() - 90000, time.time() - 90000))
    h.domain.store.sweep_orphans(h.domain.known_object_keys())
    assert not path.exists()


def test_html_and_svg_are_never_rendered(h: Harness) -> None:
    html = upload(h, "page.html", b"<script>alert(1)</script>", "text/html").json()
    response = h.get(f"/v1/artifacts/{html['id']}/content")
    assert "attachment" in response.headers["content-disposition"]
    assert "sandbox" in response.headers["content-security-policy"]


def test_path_registration_is_confined(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "ok.txt").write_text("fine")
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    (root / "link.txt").symlink_to(outside)
    harness = make_harness(tmp_path / "data", workspace_roots=[root])
    h = next(harness)
    try:
        ref = h.domain.register_path(str(root / "ok.txt"), space_id=h.general, message_id=None)
        assert ref.display_name == "ok.txt"
        for bad in (outside, root / "link.txt", root / ".." / "secret.txt", root / "missing"):
            with pytest.raises(ApiError):
                h.domain.register_path(str(bad), space_id=h.general, message_id=None)
    finally:
        harness.close()


def test_generated_file_appears_once_in_library_and_chat(h: Harness) -> None:
    run = h.send(h.general, "health").json()["run"]
    h.wait_run(run["id"])
    library = h.get("/v1/artifacts").json()["items"]
    assert [a["display_name"] for a in library] == ["vps-health-2026-11-05.md"]
    reply = h.get(f"/v1/spaces/{h.general}/messages").json()["items"][-1]
    assert reply["artifacts"][0]["id"] == library[0]["id"]


def test_new_agent_output_with_same_name_becomes_a_version(h: Harness) -> None:
    first = h.domain.register_bytes(b"one", "report.md", space_id=h.general, message_id=None)
    second = h.domain.register_bytes(b"two", "report.md", space_id=h.general, message_id=None)
    same = h.domain.register_bytes(b"two", "report.md", space_id=h.general, message_id=None)
    assert first.id == second.id == same.id
    detail = h.get(f"/v1/artifacts/{first.id}").json()
    assert [v["version"] for v in detail["versions"]] == [2, 1]
    assert h.get(f"/v1/artifacts/{first.id}/content", params={"version": 1}).content == b"one"
    assert h.get(f"/v1/artifacts/{first.id}/content").content == b"two"


def attached(h: Harness) -> tuple[dict, dict]:
    artifact = upload(h, "shared.txt", b"bytes", "text/plain").json()
    h.post(
        f"/v1/spaces/{h.general}/messages",
        json={"client_message_id": str(uuid.uuid4()), "markdown": "see attachment", "artifact_ids": [artifact["id"]]},
    )
    return artifact, h.get(f"/v1/spaces/{h.general}/messages").json()["items"][0]


def test_hide_keeps_bytes_and_references(h: Harness) -> None:
    artifact, _ = attached(h)
    assert h.post(f"/v1/artifacts/{artifact['id']}/hide").status_code == 200
    assert h.get("/v1/artifacts").json()["items"] == []
    assert len(h.get("/v1/artifacts", params={"include_hidden": True}).json()["items"]) == 1
    assert h.get(f"/v1/artifacts/{artifact['id']}/content").status_code == 200


def test_permanent_delete_names_references_then_leaves_tombstone(h: Harness) -> None:
    artifact, message = attached(h)
    response = h.delete(f"/v1/artifacts/{artifact['id']}")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["references"][0]["message_id"] == message["id"]
    assert h.delete(f"/v1/artifacts/{artifact['id']}", params={"confirm": True}).status_code == 200
    assert h.get(f"/v1/artifacts/{artifact['id']}/content").status_code == 410
    after = h.get(f"/v1/spaces/{h.general}/messages").json()["items"][0]
    assert after["id"] == message["id"]
    assert after["artifacts"][0]["state"] == "deleted"
    assert after["markdown"] == "see attachment"
    assert not list(h.settings.object_dir.glob("*/*"))


def sent_upload(h: Harness, name: str, data: bytes, mime: str = "application/octet-stream") -> None:
    artifact = upload(h, name, data, mime).json()
    h.post(
        f"/v1/spaces/{h.general}/messages",
        json={"client_message_id": str(uuid.uuid4()), "markdown": "", "artifact_ids": [artifact["id"]]},
    )


def test_staged_upload_stays_out_of_library_until_sent(h: Harness) -> None:
    artifact = upload(h, "draft.txt", b"x", "text/plain").json()
    assert h.get("/v1/artifacts").json()["items"] == []
    h.post(
        f"/v1/spaces/{h.general}/messages",
        json={"client_message_id": str(uuid.uuid4()), "markdown": "here", "artifact_ids": [artifact["id"]]},
    )
    assert [a["id"] for a in h.get("/v1/artifacts").json()["items"]] == [artifact["id"]]


def test_abandoned_staged_upload_is_removed(h: Harness) -> None:
    from amsg.db.models import Artifact

    artifact = upload(h, "draft.txt", b"x", "text/plain").json()
    with h.domain.db.write() as session:
        session.get(Artifact, artifact["id"]).created_at = "2020-01-01T00:00:00.000Z"
    assert h.domain.remove_abandoned_uploads() == 1
    assert h.get(f"/v1/artifacts/{artifact['id']}/content").status_code == 410


def test_library_filters(h: Harness) -> None:
    sent_upload(h, "photo.png", b"\x89PNG\r\n\x1a\n123")
    sent_upload(h, "notes.txt", b"x", "text/plain")
    sent_upload(h, "data.bin", b"\x00\x01")
    types = {
        name: [a["display_name"] for a in h.get("/v1/artifacts", params={"type": name}).json()["items"]]
        for name in ("image", "text", "other")
    }
    assert types == {"image": ["photo.png"], "text": ["notes.txt"], "other": ["data.bin"]}
    assert [a["display_name"] for a in h.get("/v1/artifacts", params={"q": "note"}).json()["items"]] == ["notes.txt"]


def test_large_upload_does_not_buffer_in_memory(tmp_path: Path) -> None:
    """Measured over a real socket: the test client would buffer the body itself."""
    import threading
    import tracemalloc

    import httpx
    import uvicorn

    from amsg.api.app import create_app
    from amsg.config import Settings

    port = 18794
    settings = Settings(data_dir=tmp_path, provider="fake", dev_mode=True, port=port)
    app = create_app(settings)
    token = app.state.services.domain.issue_device_token("t")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base}/health/live")
            break
        except httpx.TransportError:
            time.sleep(0.05)
    chunk = b"z" * (1024 * 1024)

    def body():  # type: ignore[no-untyped-def]
        yield b'--BOUNDARY000000\r\nContent-Disposition: form-data; name="file"; filename="big.bin"\r\n\r\n'
        for _ in range(100):
            yield chunk
        yield b"\r\n--BOUNDARY000000--\r\n"

    tracemalloc.start()
    try:
        response = httpx.post(
            f"{base}/v1/uploads",
            content=body(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "multipart/form-data; boundary=BOUNDARY000000",
            },
            timeout=120,
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        app.state.stopping.set()
        server.should_exit = True
        thread.join(timeout=10)
    assert response.status_code == 201, response.text
    assert response.json()["size"] == 100 * 1024 * 1024
    assert peak < 32 * 1024 * 1024
