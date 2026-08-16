import sqlite3

import db as dbmod


def test_get_series_normal_session_returns_data_and_comments(client):
    # Seed: one session with two measurements and a comment
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO sessions (sensor_id, start_time) VALUES ('S2', datetime('now','localtime'))")
    sess_id = cur.lastrowid
    cur.executemany(
        "INSERT INTO measurements (session_id, timestamp, temperature) VALUES (?, ?, ?)",
        [
            (sess_id, "2025-01-01T00:00:00", 20.0),
            (sess_id, "2025-01-01T00:00:01", 20.1),
        ],
    )
    cur.execute(
        "INSERT INTO comments (sensor_session, timestamp, temperature, comment) VALUES (?, ?, ?, ?)",
        (f"S2_{sess_id}", "2025-01-01T00:00:00", 20.0, "ok"),
    )
    conn.commit(); conn.close()

    res = client.get(f"/series/S2_{sess_id}")
    assert res.status_code == 200
    body = res.json()
    assert "data" in body and len(body["data"]) == 2
    assert "comments" in body and len(body["comments"]) == 1


def test_get_series_normal_session_no_comments(client):
    # Seed: one session without comments
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO sessions (sensor_id, start_time) VALUES ('S3', datetime('now','localtime'))")
    sess_id = cur.lastrowid
    conn.commit(); conn.close()

    res = client.get(f"/series/S3_{sess_id}")
    assert res.status_code == 200
    body = res.json()
    assert "comments" in body and body["comments"] == []
