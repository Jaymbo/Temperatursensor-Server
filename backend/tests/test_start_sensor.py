from unittest.mock import patch


def test_start_sensor_creates_new_entry(client):
    with patch("main.add_or_update_custom_text_entry", return_value=42) as mock_add:
        res = client.post("/start_sensor", json={"custom_text": "Test Text"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["entry_id"] == "None_42"
        mock_add.assert_called_once_with("Test Text", 10.0, 60)


def test_start_sensor_handles_exception(client):
    with patch("main.add_or_update_custom_text_entry", side_effect=Exception("Test Exception")):
        res = client.post("/start_sensor", json={"custom_text": "Test Text"})
        # Our FastAPI handler catches and returns 200 with error status, not 500
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "error"
        assert "Ein unerwarteter Fehler" in data["message"]
