import json
from concurrent.futures import ThreadPoolExecutor

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


def test_assignment_sample_and_summary_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_FILE", tmp_path / "trips.json")
    client = TestClient(main.app)
    sample = [
        {"id": "t1", "start": "2026-10-01T08:10:00+05:00", "end": "2026-10-01T08:32:00+05:00", "amount": 2400, "payment": "card", "commission": 360},
        {"id": "t2", "start": "2026-10-01T09:05:00+05:00", "end": "2026-10-01T09:20:00+05:00", "amount": 1500, "payment": "cash", "commission": 225},
    ]
    for trip in sample:
        assert client.post("/api/trips", json=trip).status_code == 201
    assert client.get("/api/days").json() == ["2026-10-01"]
    assert len(client.get("/api/trips?day=2026-10-01").json()) == 2
    assert client.get("/api/summary?day=2026-10-01").json() == {
        "day": "2026-10-01", "trip_count": 2, "revenue": 3900,
        "commission": 585, "take_home": 3315, "cash": 1500, "card": 2400,
    }


def test_same_trip_without_id_is_deduplicated_even_with_equivalent_offset(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_FILE", tmp_path / "trips.json")
    client = TestClient(main.app)
    trip = {"start": "2026-10-01T08:10:00+05:00", "end": "2026-10-01T08:32:00+05:00",
            "amount": 2400, "payment": "card", "commission": 360}
    equivalent = {**trip, "start": "2026-10-01T03:10:00Z", "end": "2026-10-01T03:32:00Z"}
    assert client.post("/api/trips", json=trip).json()["duplicate"] is False
    assert client.post("/api/trips", json=equivalent).json()["duplicate"] is True
    stored = json.loads((tmp_path / "trips.json").read_text())
    assert len(stored) == 1
    assert "fingerprint" not in client.get("/api/trips?day=2026-10-01").json()[0]


def test_retry_matches_seed_record_with_id_and_preserves_fractional_seconds(tmp_path, monkeypatch):
    data_file = tmp_path / "trips.json"
    data_file.write_text(json.dumps([{
        "id": "seed-1", "start": "2026-10-01T08:10:00.123456+05:00",
        "end": "2026-10-01T08:10:01.123456+05:00", "amount": 100,
        "payment": "cash", "commission": 0,
    }]))
    monkeypatch.setattr(main, "DATA_FILE", data_file)
    client = TestClient(main.app)
    retry = {"start": "2026-10-01T03:10:00.123456Z", "end": "2026-10-01T03:10:01.123456Z",
             "amount": 100, "payment": "cash", "commission": 0}
    response = client.post("/api/trips", json=retry)
    assert response.status_code == 201
    assert response.json()["duplicate"] is True
    assert len(json.loads(data_file.read_text())) == 1


def test_concurrent_retries_write_only_one_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_FILE", tmp_path / "trips.json")
    client = TestClient(main.app)
    body = {"start": "2026-10-01T08:10:00+05:00", "end": "2026-10-01T08:32:00+05:00",
            "amount": 2400, "payment": "card", "commission": 360}
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.post("/api/trips", json=body), range(8)))
    assert sum(response.json()["duplicate"] is False for response in responses) == 1
    assert len(json.loads((tmp_path / "trips.json").read_text())) == 1


def test_mixed_timezone_presence_is_rejected():
    client = TestClient(main.app)
    body = {"start": "2026-10-01T08:10:00+05:00", "end": "2026-10-01T08:32:00",
            "amount": 2400, "payment": "card", "commission": 360}
    assert client.post("/api/trips", json=body).status_code == 422


def test_reusing_id_for_different_trip_returns_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_FILE", tmp_path / "trips.json")
    client = TestClient(main.app)
    body = {"id": "same-id", "start": "2026-10-01T08:10:00+05:00",
            "end": "2026-10-01T08:32:00+05:00", "amount": 2400,
            "payment": "card", "commission": 360}
    assert client.post("/api/trips", json=body).status_code == 201
    assert client.post("/api/trips", json={**body, "amount": 2600}).status_code == 409
