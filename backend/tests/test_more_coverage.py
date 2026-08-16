import asyncio
import sqlite3
from typing import Any

import db as dbmod


def _seed_session(sensor_id: str, calibration: str | None = None) -> str:
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (sensor_id, start_time, calibration_data) VALUES (?, datetime('now','localtime'), ?)",
        (sensor_id, calibration),
    )
    session_id = cur.lastrowid
    conn.commit()
    conn.close()
    return f"{sensor_id}_{session_id}"


def test_get_series_empty_and_comments_flow(client):
    sensor_session = _seed_session("1")

    # Initially: no data, no comments
    res = client.get(f"/series/{sensor_session}")
    assert res.status_code == 200
    payload = res.json()
    assert payload["data"] == []
    assert payload["comments"] == []

    # Add comment
    comment = {
        "sensor_session": sensor_session,
        "timestamp": "2025-01-01T00:00:00",
        "temperature": 23.4,
        "comment": "hello",
    }
    res = client.post("/comments", json=comment)
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    # Verify comment present
    res = client.get(f"/series/{sensor_session}")
    data = res.json()
    assert len(data["comments"]) == 1
    assert data["comments"][0]["comment"] == "hello"

    # Delete comment
    res = client.request("DELETE", "/comments", json=comment)
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    # Verify comment removed
    res = client.get(f"/series/{sensor_session}")
    data = res.json()
    assert data["comments"] == []


def test_fetch_calibration_points_empty(client):
    # Session without calibration — get_calibration_points returns
    # [{"calibration": None, "correction_points": None}] nicht []
    _ = _seed_session("2", calibration=None)
    res = client.get("/calibration", params={"sensor_id": "2"})
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["calibration"] is None
    assert data[0]["correction_points"] is None


def test_websocket_connect_and_disconnect(client):
    # Connect, send a message, close
    with client.websocket_connect("/ws") as ws:
        ws.send_text("ping")
        # No receive expected; server only logs


def test_notify_broadcast_uses_active_connections(client, monkeypatch):
    # Create a fake connection to capture broadcasted text
    captured: list[str] = []

    class FakeConn:
        async def send_text(self, msg: str):
            captured.append(msg)

    import main

    # Swap active connections with our fake
    main.manager.active_connections = [FakeConn()]
    sensor_session = _seed_session("3")

    res = client.post(f"/notify/{sensor_session}", json={"action": "test"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    # Ensure our fake received a message
    assert captured and sensor_session in captured[0]


def test_process_relative_data_errors(client):
    # empty inputs
    try:
        dbmod.process_relative_data([], [])
        assert False, "expected ValueError for empty inputs"
    except ValueError:
        pass

    # length mismatch
    try:
        dbmod.process_relative_data([0, 1], [10.0])
        assert False, "expected ValueError for length mismatch"
    except ValueError:
        pass
