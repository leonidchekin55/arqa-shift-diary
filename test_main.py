import json

from fastapi.testclient import TestClient

import main


def test_summary_calculation():
    assert main.summarize([
        {"amount": 2400, "commission": 360, "payment": "card"},
        {"amount": 1500, "commission": 225, "payment": "cash"},
    ]) == {"trip_count": 2, "revenue": 3900, "commission": 585,
           "take_home": 3315, "cash": 1500, "card": 2400}


def test_duplicate_submission_is_idempotent(tmp_path, monkeypatch):
    data_file = tmp_path / "trips.json"
    monkeypatch.setattr(main, "DATA_FILE", data_file)
    client = TestClient(main.app)
    body = {"id": "retry-1", "start": "2026-10-01T08:10:00+05:00",
            "end": "2026-10-01T08:32:00+05:00", "amount": 2400,
            "payment": "card", "commission": 360}
    first = client.post("/api/trips", json=body)
    second = client.post("/api/trips", json=body)
    assert first.status_code == 201 and first.json()["duplicate"] is False
    assert second.status_code == 201 and second.json()["duplicate"] is True
    assert len(json.loads(data_file.read_text())) == 1


def test_validation_and_same_payload_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_FILE", tmp_path / "trips.json")
    client = TestClient(main.app)
    bad = {"start": "2026-10-01T09:00:00Z", "end": "2026-10-01T08:00:00Z",
           "amount": 0, "payment": "cash", "commission": 0}
    assert client.post("/api/trips", json=bad).status_code == 422
    body = {"start": "2026-10-01T08:10:00+05:00", "end": "2026-10-01T08:32:00+05:00",
            "amount": 2400, "payment": "card", "commission": 360}
    assert client.post("/api/trips", json=body).json()["duplicate"] is False
    assert client.post("/api/trips", json=body).json()["duplicate"] is True
