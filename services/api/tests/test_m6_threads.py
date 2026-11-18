"""M6 gate: a thread is one idempotent transaction that leaves its source untouched."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from amsg.db.models import Message, SessionBinding, SourceLink, Space

from .conftest import Harness


def seed(h: Harness) -> list[dict]:
    run = h.send(h.general, "Plan the data centre move for next quarter please").json()["run"]
    h.wait_run(run["id"])
    return h.get(f"/v1/spaces/{h.general}/messages").json()["items"]


def fork(h: Harness, ids: list[str], key: str | None = None, **extra):  # type: ignore[no-untyped-def]
    body = {"source_message_ids": ids, "idempotency_key": key or str(uuid.uuid4()), **extra}
    return h.post(f"/v1/spaces/{h.general}/fork", json=body)


def test_fork_creates_every_record_and_leaves_source_unchanged(h: Harness) -> None:
    items = seed(h)
    before = [(m["id"], m["markdown"], m["revision"]) for m in items]
    response = fork(h, [m["id"] for m in items])
    assert response.status_code == 201
    space = response.json()["space"]
    assert space["title"] == "Plan the data centre move for next quarter please"
    assert space["icon"]["value"] == "thread"
    with h.domain.db.read() as session:
        assert len(session.scalars(select(SourceLink).where(SourceLink.dest_space_id == space["id"])).all()) == 2
        assert session.scalar(select(SessionBinding).where(SessionBinding.space_id == space["id"])) is not None
    after = h.get(f"/v1/spaces/{h.general}/messages").json()["items"]
    assert [(m["id"], m["markdown"], m["revision"]) for m in after] == before
    assert after[0]["links"][0] == {
        "direction": "continued_in",
        "space_id": space["id"],
        "title": space["title"],
        "available": True,
    }
    seed_message = h.get(f"/v1/spaces/{space['id']}/messages").json()["items"][0]
    assert seed_message["role"] == "context"
    assert seed_message["links"][0]["direction"] == "from"


def test_retry_yields_one_space(h: Harness) -> None:
    items = seed(h)
    key = str(uuid.uuid4())
    first = fork(h, [items[0]["id"]], key).json()
    second = fork(h, [items[0]["id"]], key).json()
    assert second["duplicate"] is True and first["space"]["id"] == second["space"]["id"]
    with h.domain.db.read() as session:
        assert len(session.scalars(select(Space).where(Space.kind == "thread")).all()) == 1
    assert fork(h, [items[1]["id"]], key).status_code == 409


def test_source_must_belong_to_space(h: Harness) -> None:
    items = seed(h)
    other = h.post("/v1/spaces", json={"title": "Other"}).json()["id"]
    response = h.post(
        f"/v1/spaces/{other}/fork",
        json={"source_message_ids": [items[0]["id"]], "idempotency_key": str(uuid.uuid4())},
    )
    assert response.status_code == 422


def test_title_is_truncated_at_sixty(h: Harness) -> None:
    run = h.send(h.general, "x" * 200).json()["run"]
    h.wait_run(run["id"])
    items = h.get(f"/v1/spaces/{h.general}/messages").json()["items"]
    space = fork(h, [items[0]["id"]]).json()["space"]
    assert len(space["title"]) == 60


def test_summary_is_labelled_and_optional(h: Harness) -> None:
    items = seed(h)
    space = fork(h, [items[0]["id"]], summary="Generated text").json()["space"]
    seed_message = h.get(f"/v1/spaces/{space['id']}/messages").json()["items"][0]
    assert seed_message["meta"]["generated_summary"] == "Generated text"
    plain = fork(h, [items[0]["id"]]).json()["space"]
    assert h.get(f"/v1/spaces/{plain['id']}/messages").json()["items"][0]["meta"]["generated_summary"] is None


def test_over_budget_selection_keeps_full_snapshot(h: Harness) -> None:
    h.domain.settings.context_budget_chars = 500
    space_messages = []
    for index in range(4):
        space_messages.append(h.domain.post_agent_message(h.general, f"{index} " + "word " * 200, author="a"))
    space = fork(h, [m.id for m in space_messages]).json()["space"]
    seed_message = h.get(f"/v1/spaces/{space['id']}/messages").json()["items"][0]
    assert seed_message["meta"]["truncated"] is True
    assert len(seed_message["markdown"]) <= 520
    page = h.domain.read_thread_context(space["id"], 0, 2)
    assert len(page["items"]) == 2 and page["next_offset"] == 2
    rest = h.domain.read_thread_context(space["id"], 2, 2)
    assert [item["markdown"][0] for item in page["items"] + rest["items"]] == ["0", "1", "2", "3"]


def test_new_thread_turn_receives_context_once(h: Harness) -> None:
    items = seed(h)
    space = fork(h, [items[0]["id"]]).json()["space"]
    from amsg import schemas

    h.domain.settings.max_concurrent_runs = 0  # keep the worker from claiming while the test inspects
    h.domain.accept_message(space["id"], schemas.MessageCreate(client_message_id=str(uuid.uuid4()), markdown="go"))
    with h.domain.db.read() as session:
        target = h.domain.space_or_404(session, space["id"])
        request = session.scalars(
            select(Message).where(Message.space_id == space["id"], Message.role == "user")
        ).first()
        connection = session.get(__import__("amsg.db.models").db.models.AgentConnection, target.agent_connection_id)
        context = h.domain.turn_context(session, target, request, connection)
    assert context["source_context"]["excerpt"].startswith("**User:**")
    assert context["source_context"]["selected_source_ids"] == [items[0]["id"]]
    h.domain.settings.max_concurrent_runs = 2


def test_title_and_icon_remain_editable(h: Harness) -> None:
    items = seed(h)
    space = fork(h, [items[0]["id"]]).json()["space"]
    response = h.patch(
        f"/v1/spaces/{space['id']}",
        json={"expected_revision": space["revision"], "title": "Renamed", "icon": {"kind": "emoji", "value": "🌊"}},
    )
    assert response.json()["title"] == "Renamed"
    assert response.json()["icon"] == {"kind": "emoji", "value": "🌊"}
