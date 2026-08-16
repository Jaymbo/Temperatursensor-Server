import db as dbmod


def _seed_initial_session(sensor_id: str, calibration: str | None = None):
	import sqlite3

	conn = sqlite3.connect(dbmod.DB_PATH)
	cur = conn.cursor()
	cur.execute(
		"INSERT INTO sessions (sensor_id, start_time, calibration_data) VALUES (?, datetime('now','localtime'), ?)",
		(sensor_id, calibration),
	)
	conn.commit()
	conn.close()


def test_update_clones_latest_session_and_broadcasts(client):
	sensor_id = "1"
	# Ensure at least one existing session to clone from
	_seed_initial_session(sensor_id, calibration="1000,3.9083e-3,-5.775e-7,3.32,981.7")

	res = client.post(f"/update/{sensor_id}")
	assert res.status_code == 200
	data = res.json()
	assert data["status"] == "success"
	assert data["new_sensor_session"].startswith(f"{sensor_id}_")
	assert "calibration_data" in data and data["calibration_data"]