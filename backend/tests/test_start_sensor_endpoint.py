def test_start_sensor_creates_entry(client):
	payload = {"custom_text": "test 3"}
	res = client.post("/start_sensor", json=payload)
	assert res.status_code == 200
	data = res.json()
	assert data["status"] == "success"
	assert data["entry_id"].startswith("None_")
