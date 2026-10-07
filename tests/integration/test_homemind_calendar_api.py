"""HTTP contract for calendars, occurrences and reminders (Stage 1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from homemind.api.app import build_app as build_homemind_app
from octop.infra.server import OctopServer
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin


@pytest.fixture
async def calendar_env(
    tmp_octop_home: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, OctopServer, dict[str, str]]]:
    async with octop_client(tmp_octop_home, app_factory=build_homemind_app) as (client, srv):
        await bootstrap_admin(client, tmp_octop_home)
        yield client, srv, await auth_header(client)


async def _family(client: httpx.AsyncClient, auth: dict[str, str]) -> str:
    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Calendar Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _soon(*, hours: int) -> int:
    moment = datetime.now(UTC) + timedelta(hours=hours)
    return int(moment.replace(minute=0, second=0, microsecond=0).timestamp())


@pytest.mark.asyncio
async def test_calendar_and_event_round_trip(
    calendar_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = calendar_env
    family_id = await _family(client, auth)

    response = await client.post(
        f"/api/homemind/families/{family_id}/calendars",
        headers=auth,
        json={"name": "Shared", "color": "#3366ff"},
    )
    assert response.status_code == 201
    calendar = response.json()
    assert calendar["timezone"] is None
    assert calendar["visibility"] == "FAMILY"

    response = await client.get(f"/api/homemind/families/{family_id}/calendars", headers=auth)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [calendar["id"]]

    starts = _soon(hours=48)
    response = await client.post(
        f"/api/homemind/families/{family_id}/calendar-events",
        headers=auth,
        json={
            "calendar_id": calendar["id"],
            "title": "Dentist",
            "starts_at": starts,
            "ends_at": starts + 3600,
            "timezone": "Asia/Shanghai",
            "recurrence_rule": "rrule:freq=weekly;byday=tu",
        },
    )
    assert response.status_code == 201
    event = response.json()
    # The stored rule is normalised, not kept as it arrived.
    assert event["recurrence_rule"] == "FREQ=WEEKLY;BYDAY=TU"
    assert event["status"] == "CONFIRMED"
    assert event["version"] == 1

    response = await client.get(f"/api/homemind/families/{family_id}/calendar-events", headers=auth)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [event["id"]]

    response = await client.get(
        f"/api/homemind/families/{family_id}/calendar-occurrences",
        headers=auth,
        params={"from": starts - 86400, "to": starts + 86400 * 21},
    )
    assert response.status_code == 200
    occurrences = response.json()
    assert len(occurrences) == 3
    assert all(row["is_recurring"] for row in occurrences)
    assert all(row["recurrence_rule"] == "FREQ=WEEKLY;BYDAY=TU" for row in occurrences)
    # Rendered in the event's own zone the time stays at the hour the
    # user typed, even as the UTC instant shifts with DST.
    hours = {
        datetime.fromtimestamp(row["starts_at"], tz=UTC)
        .astimezone(__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))
        .hour
        for row in occurrences
    }
    assert len(hours) == 1


@pytest.mark.asyncio
async def test_calendar_rejects_an_invalid_recurrence_rule(
    calendar_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = calendar_env
    family_id = await _family(client, auth)
    calendar = (
        await client.post(
            f"/api/homemind/families/{family_id}/calendars",
            headers=auth,
            json={"name": "Shared"},
        )
    ).json()
    starts = _soon(hours=24)

    response = await client.post(
        f"/api/homemind/families/{family_id}/calendar-events",
        headers=auth,
        json={
            "calendar_id": calendar["id"],
            "title": "Natural language",
            "starts_at": starts,
            "ends_at": starts + 60,
            "recurrence_rule": "every other tuesday",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FAMILY_INVALID"


@pytest.mark.asyncio
async def test_calendar_rejects_an_oversized_occurrence_window(
    calendar_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = calendar_env
    family_id = await _family(client, auth)
    now = int(datetime.now(UTC).timestamp())
    response = await client.get(
        f"/api/homemind/families/{family_id}/calendar-occurrences",
        headers=auth,
        params={"from": now, "to": now + 86400 * 800},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_stale_event_edit_is_rejected_with_conflict(
    calendar_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = calendar_env
    family_id = await _family(client, auth)
    calendar = (
        await client.post(
            f"/api/homemind/families/{family_id}/calendars",
            headers=auth,
            json={"name": "Shared"},
        )
    ).json()
    starts = _soon(hours=24)
    event = (
        await client.post(
            f"/api/homemind/families/{family_id}/calendar-events",
            headers=auth,
            json={
                "calendar_id": calendar["id"],
                "title": "Original",
                "starts_at": starts,
                "ends_at": starts + 3600,
            },
        )
    ).json()

    response = await client.patch(
        f"/api/homemind/families/{family_id}/calendar-events/{event['id']}",
        headers=auth,
        json={"title": "First", "expected_version": event["version"]},
    )
    assert response.status_code == 200
    assert response.json()["version"] == event["version"] + 1

    response = await client.patch(
        f"/api/homemind/families/{family_id}/calendar-events/{event['id']}",
        headers=auth,
        json={"title": "Stale", "expected_version": event["version"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FAMILY_CONFLICT"

    response = await client.get(
        f"/api/homemind/families/{family_id}/calendar-events/{event['id']}", headers=auth
    )
    assert response.json()["title"] == "First"


@pytest.mark.asyncio
async def test_cancelling_an_event_hides_it_from_occurrences(
    calendar_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = calendar_env
    family_id = await _family(client, auth)
    calendar = (
        await client.post(
            f"/api/homemind/families/{family_id}/calendars",
            headers=auth,
            json={"name": "Shared"},
        )
    ).json()
    starts = _soon(hours=24)
    event = (
        await client.post(
            f"/api/homemind/families/{family_id}/calendar-events",
            headers=auth,
            json={
                "calendar_id": calendar["id"],
                "title": "Called off",
                "starts_at": starts,
                "ends_at": starts + 1800,
            },
        )
    ).json()

    response = await client.post(
        f"/api/homemind/families/{family_id}/calendar-events/{event['id']}/cancel",
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"

    response = await client.get(
        f"/api/homemind/families/{family_id}/calendar-occurrences",
        headers=auth,
        params={"from": starts - 3600, "to": starts + 3600},
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_reminder_lifecycle_over_http(
    calendar_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = calendar_env
    family_id = await _family(client, auth)

    response = await client.get(f"/api/homemind/families/{family_id}/reminders", headers=auth)
    assert response.status_code == 200
    assert response.json() == []

    task = (
        await client.post(
            f"/api/homemind/families/{family_id}/tasks",
            headers=auth,
            json={"title": "Renew the warranty"},
        )
    ).json()

    payload = {
        "target_type": "TASK",
        "target_id": task["id"],
        "remind_at": _soon(hours=2),
        "lead_seconds": 3600,
    }
    response = await client.post(
        f"/api/homemind/families/{family_id}/reminders", headers=auth, json=payload
    )
    assert response.status_code == 201
    reminder = response.json()
    assert reminder["status"] == "PENDING"
    assert reminder["channel"] == "IN_APP"

    # Idempotent: the same reminder for the same target fires once.
    response = await client.post(
        f"/api/homemind/families/{family_id}/reminders", headers=auth, json=payload
    )
    assert response.status_code == 201
    assert response.json()["id"] == reminder["id"]

    response = await client.get(f"/api/homemind/families/{family_id}/reminders", headers=auth)
    assert len(response.json()) == 1

    response = await client.get(
        f"/api/homemind/families/{family_id}/reminders/summary", headers=auth
    )
    assert response.status_code == 200
    assert response.json()["counts"]["PENDING"] == 1

    response = await client.post(
        f"/api/homemind/families/{family_id}/reminders/{reminder['id']}/cancel",
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_moving_a_task_due_date_rewrites_its_reminder(
    calendar_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = calendar_env
    family_id = await _family(client, auth)

    due = _soon(hours=72)
    task = (
        await client.post(
            f"/api/homemind/families/{family_id}/tasks",
            headers=auth,
            json={"title": "Pay tuition"},
        )
    ).json()

    # A reminder is derived from the due date, not created alongside the
    # task: the family asks for one, and every later move re-derives it.
    response = await client.patch(
        f"/api/homemind/families/{family_id}/tasks/{task['id']}",
        headers=auth,
        json={"due_at": due},
    )
    assert response.status_code == 200

    response = await client.get(
        f"/api/homemind/families/{family_id}/reminders",
        headers=auth,
        params={"status": "PENDING"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["remind_at"] == due - 3600

    # Now move it: the old reminder must die rather than fire at a date
    # that no longer exists.
    moved_due = due + 86400
    response = await client.patch(
        f"/api/homemind/families/{family_id}/tasks/{task['id']}",
        headers=auth,
        json={"due_at": moved_due},
    )
    assert response.status_code == 200

    response = await client.get(
        f"/api/homemind/families/{family_id}/reminders",
        headers=auth,
        params={"status": "PENDING"},
    )
    pending = response.json()
    assert len(pending) == 1
    assert pending[0]["target_id"] == task["id"]
    assert pending[0]["remind_at"] == moved_due - 3600

    response = await client.get(
        f"/api/homemind/families/{family_id}/reminders",
        headers=auth,
        params={"status": "CANCELLED"},
    )
    assert [row["remind_at"] for row in response.json()] == [due - 3600]


@pytest.mark.asyncio
async def test_clearing_a_task_due_date_cancels_its_reminder(
    calendar_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = calendar_env
    family_id = await _family(client, auth)
    task = (
        await client.post(
            f"/api/homemind/families/{family_id}/tasks",
            headers=auth,
            json={"title": "Renew the passport"},
        )
    ).json()

    await client.patch(
        f"/api/homemind/families/{family_id}/tasks/{task['id']}",
        headers=auth,
        json={"due_at": _soon(hours=96)},
    )
    response = await client.patch(
        f"/api/homemind/families/{family_id}/tasks/{task['id']}",
        headers=auth,
        json={"due_at": None},
    )
    assert response.status_code == 200

    response = await client.get(
        f"/api/homemind/families/{family_id}/reminders",
        headers=auth,
        params={"status": "PENDING"},
    )
    assert response.json() == []


@pytest.mark.asyncio
async def test_calendar_is_invisible_to_a_non_member(
    calendar_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
    tmp_path: Path,
) -> None:
    client, _, auth = calendar_env
    family_id = await _family(client, auth)
    calendar = (
        await client.post(
            f"/api/homemind/families/{family_id}/calendars",
            headers=auth,
            json={"name": "Private plans"},
        )
    ).json()

    # A second admin account is still a non-member of this family.
    await client.post(
        "/api/users",
        headers=auth,
        json={"username": "stranger", "password": "Str0ngPass!2026", "role": "user"},
    )
    stranger = await client.post(
        "/api/auth/login",
        json={"username": "stranger", "password": "Str0ngPass!2026"},
    )
    assert stranger.status_code == 200
    stranger_auth = {"Authorization": f"Bearer {stranger.json()['access_token']}"}

    response = await client.get(
        f"/api/homemind/families/{family_id}/calendars", headers=stranger_auth
    )
    assert response.status_code == 403
    assert calendar["name"] == "Private plans"
