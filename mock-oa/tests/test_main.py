from fastapi.testclient import TestClient

from app import main


def _payload(amount=100.0):
    return {
        "expenseId": "EXP-20260827-000001",
        "employeeId": "E10001",
        "tripId": "TRIP-001",
        "costCenter": "COST-IT",
        "claimedAmount": amount,
        "reimbursableAmount": amount,
    }


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", main.MockOaStore(str(tmp_path / "mock-oa.sqlite3")))
    return TestClient(main.app)


def test_first_post_creates_pending_record_and_get_returns_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    created = client.post("/api/expense-approvals", json=_payload(), headers={"Idempotency-Key": "expense:EXP-20260827-000001"})
    assert created.status_code == 200
    assert created.json()["status"] == "PENDING"
    fetched = client.get(f"/api/expense-approvals/{created.json()['requestId']}")
    assert fetched.status_code == 200
    assert fetched.json() == created.json()


def test_same_key_and_payload_reuses_exact_request_id(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    headers = {"Idempotency-Key": "expense:EXP-20260827-000001"}
    first = client.post("/api/expense-approvals", json=_payload(), headers=headers)
    replay = client.post("/api/expense-approvals", json=_payload(), headers=headers)
    assert replay.status_code == 200
    assert replay.json() == first.json()


def test_same_key_with_different_business_payload_is_conflict(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    headers = {"Idempotency-Key": "expense:EXP-20260827-000001"}
    assert client.post("/api/expense-approvals", json=_payload(), headers=headers).status_code == 200
    assert client.post("/api/expense-approvals", json=_payload(101.0), headers=headers).status_code == 409


def test_sqlite_persists_idempotency_across_store_restart(tmp_path):
    path = tmp_path / "mock-oa.sqlite3"
    first_store = main.MockOaStore(str(path))
    payload = main.ExpenseApprovalSubmission(**_payload())
    first = first_store.submit("expense:EXP-20260827-000001", payload)
    restarted_store = main.MockOaStore(str(path))
    replay = restarted_store.submit("expense:EXP-20260827-000001", payload)
    assert replay == first
