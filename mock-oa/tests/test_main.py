import hashlib
import hmac
import json
from decimal import Decimal

import pytest
from app import main
from fastapi.testclient import TestClient


def _payload(amount="100.00", reimbursable_amount=None):
    if reimbursable_amount is None:
        reimbursable_amount = amount
    return {
        "expenseId": "EXP-20260827-000001",
        "employeeId": "E10001",
        "tripId": "TRIP-001",
        "costCenter": "COST-IT",
        "claimedAmount": amount,
        "reimbursableAmount": reimbursable_amount,
    }


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", main.MockOaStore(str(tmp_path / "mock-oa.sqlite3")))
    return TestClient(main.app)


def test_first_post_creates_pending_record_and_get_returns_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    created = client.post(
        "/api/expense-approvals",
        json=_payload(),
        headers={"Idempotency-Key": "expense:EXP-20260827-000001"},
    )
    assert created.status_code == 200
    assert created.json()["status"] == "PENDING"
    fetched = client.get(f"/api/expense-approvals/{created.json()['requestId']}")
    assert fetched.status_code == 200
    assert fetched.json() == created.json()


def test_admin_list_filters_pending_and_maps_payload_fields(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _create_approval(client)

    response = client.get("/api/admin/expense-approvals", params={"status": "PENDING"})

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["items"] == [{
        "requestId": response.json()["items"][0]["requestId"],
        "status": "PENDING",
        "expenseId": "EXP-20260827-000001",
        "employeeId": "E10001",
        "tripId": "TRIP-001",
        "costCenter": "COST-IT",
        "claimedAmount": "100",
        "reimbursableAmount": "100",
        "createdAt": response.json()["items"][0]["createdAt"],
    }]


def test_admin_list_returns_terminal_records_and_status_filter(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    request_id = _create_approval(client)
    monkeypatch.setattr(main, "send_approval_webhook", lambda _: True)
    client.post(f"/api/admin/expense-approvals/{request_id}/approve")

    approved = client.get("/api/admin/expense-approvals", params={"status": "APPROVED"})
    rejected = client.get("/api/admin/expense-approvals", params={"status": "REJECTED"})

    assert approved.status_code == 200
    assert approved.json()["items"][0]["status"] == "APPROVED"
    assert rejected.status_code == 200
    assert rejected.json()["items"] == []


def test_admin_list_has_explicit_limit_and_does_not_expose_internal_columns(tmp_path, monkeypatch):
    store = main.MockOaStore(str(tmp_path / "mock-oa.sqlite3"))
    monkeypatch.setattr(main, "store", store)
    payload = main.ExpenseApprovalSubmission(**_payload())
    for index in range(105):
        store.submit(f"expense:EXP-{index:06d}", payload.model_copy(update={"expenseId": f"EXP-{index:06d}"}))

    response = TestClient(main.app).get("/api/admin/expense-approvals", params={"limit": 100})

    assert response.status_code == 200
    assert response.json()["count"] == 100
    assert all(set(item) == {
        "requestId", "status", "expenseId", "employeeId", "tripId", "costCenter",
        "claimedAmount", "reimbursableAmount", "createdAt",
    } for item in response.json()["items"])


def test_admin_list_rejects_unknown_status(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/api/admin/expense-approvals", params={"status": "CANCELLED"})

    assert response.status_code == 400


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
    assert client.post("/api/expense-approvals", json=_payload("101.00"), headers=headers).status_code == 409


def test_decimal_amounts_are_exact_and_equivalent_scales_share_idempotency_payload(tmp_path):
    store = main.MockOaStore(str(tmp_path / "mock-oa.sqlite3"))
    first_payload = main.ExpenseApprovalSubmission(**_payload("100.1"))
    equivalent_payload = main.ExpenseApprovalSubmission(**_payload("100.10"))

    assert type(first_payload.claimedAmount) is Decimal
    assert first_payload.claimedAmount == Decimal("100.1")
    assert main.MockOaStore._canonical_payload(first_payload) == main.MockOaStore._canonical_payload(
        equivalent_payload
    )
    assert '"claimedAmount":"100.1"' in main.MockOaStore._canonical_payload(first_payload)

    first = store.submit("expense:EXP-20260827-000001", first_payload)
    replay = store.submit("expense:EXP-20260827-000001", equivalent_payload)
    assert replay == first


def test_money_precision_and_order_are_validated(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    headers = {"Idempotency-Key": "expense:EXP-20260827-000001"}

    too_precise = client.post(
        "/api/expense-approvals", json=_payload("100.001"), headers=headers
    )
    too_large_reimbursement = client.post(
        "/api/expense-approvals", json=_payload("100.00", "100.01"), headers=headers
    )

    assert too_precise.status_code == 422
    assert too_large_reimbursement.status_code == 422


@pytest.mark.parametrize("amount", ["-0.01"])
def test_money_amounts_must_be_nonnegative(amount):
    with pytest.raises(ValueError):
        main.ExpenseApprovalSubmission(**_payload(amount))


def test_sqlite_persists_idempotency_across_store_restart(tmp_path):
    path = tmp_path / "mock-oa.sqlite3"
    first_store = main.MockOaStore(str(path))
    payload = main.ExpenseApprovalSubmission(**_payload())
    first = first_store.submit("expense:EXP-20260827-000001", payload)
    restarted_store = main.MockOaStore(str(path))
    replay = restarted_store.submit("expense:EXP-20260827-000001", payload)
    assert replay == first


def _create_approval(client):
    response = client.post(
        "/api/expense-approvals",
        json=_payload(),
        headers={"Idempotency-Key": "expense:EXP-20260827-000001"},
    )
    assert response.status_code == 200
    return response.json()["requestId"]


def test_pending_to_approved_and_webhook_has_no_status(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    request_id = _create_approval(client)
    events = []
    monkeypatch.setattr(main, "send_approval_webhook", lambda value: events.append(value) or True)

    response = client.post(f"/api/admin/expense-approvals/{request_id}/approve")

    assert response.status_code == 200
    assert response.json() == {"requestId": request_id, "status": "APPROVED"}
    assert client.get(f"/api/expense-approvals/{request_id}").json()["status"] == "APPROVED"
    assert events == [request_id]


def test_pending_to_rejected(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    request_id = _create_approval(client)
    monkeypatch.setattr(main, "send_approval_webhook", lambda _: True)

    response = client.post(f"/api/admin/expense-approvals/{request_id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"


def test_same_approved_replay_is_idempotent(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    request_id = _create_approval(client)
    monkeypatch.setattr(main, "send_approval_webhook", lambda _: True)
    client.post(f"/api/admin/expense-approvals/{request_id}/approve")

    replay = client.post(f"/api/admin/expense-approvals/{request_id}/approve")

    assert replay.status_code == 200
    assert replay.json()["status"] == "APPROVED"


def test_approved_to_rejected_is_conflict(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    request_id = _create_approval(client)
    monkeypatch.setattr(main, "send_approval_webhook", lambda _: True)
    client.post(f"/api/admin/expense-approvals/{request_id}/approve")

    response = client.post(f"/api/admin/expense-approvals/{request_id}/reject")

    assert response.status_code == 409
    assert client.get(f"/api/expense-approvals/{request_id}").json()["status"] == "APPROVED"


def test_same_rejected_replay_is_idempotent(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    request_id = _create_approval(client)
    monkeypatch.setattr(main, "send_approval_webhook", lambda _: True)
    client.post(f"/api/admin/expense-approvals/{request_id}/reject")

    replay = client.post(f"/api/admin/expense-approvals/{request_id}/reject")

    assert replay.status_code == 200
    assert replay.json()["status"] == "REJECTED"


def test_rejected_to_approved_is_conflict(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    request_id = _create_approval(client)
    monkeypatch.setattr(main, "send_approval_webhook", lambda _: True)
    client.post(f"/api/admin/expense-approvals/{request_id}/reject")

    response = client.post(f"/api/admin/expense-approvals/{request_id}/approve")

    assert response.status_code == 409
    assert client.get(f"/api/expense-approvals/{request_id}").json()["status"] == "REJECTED"


def test_terminal_status_persists_across_store_reopen(tmp_path):
    path = tmp_path / "mock-oa.sqlite3"
    first_store = main.MockOaStore(str(path))
    payload = main.ExpenseApprovalSubmission(**_payload())
    created = first_store.submit("expense:EXP-20260827-000001", payload)

    assert first_store.decide(created.requestId, "APPROVED").status == "APPROVED"
    reopened_store = main.MockOaStore(str(path))

    assert reopened_store.get(created.requestId).status == "APPROVED"


def test_webhook_signature_matches_exact_body(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_OA_WEBHOOK_URL", "http://java.test/webhook")
    monkeypatch.setenv("MOCK_OA_WEBHOOK_SECRET", "test-webhook-secret")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return b""

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(main, "urlopen", fake_urlopen)

    assert main.send_approval_webhook("OA-EXP-ABC") is True
    request = captured["request"]
    body = request.data
    timestamp = request.get_header("X-mock-oa-timestamp")
    signature = request.get_header("X-mock-oa-signature")
    expected = hmac.new(
        b"test-webhook-secret",
        f"{timestamp}.".encode("utf-8") + body,
        hashlib.sha256,
    ).hexdigest()

    assert json.loads(body) == {
        "eventId": json.loads(body)["eventId"],
        "eventType": "EXPENSE_APPROVAL_CHANGED",
        "requestId": "OA-EXP-ABC",
    }
    assert "status" not in json.loads(body)
    assert signature == f"v1={expected}"
    assert captured["timeout"] <= 30.0


def test_webhook_is_sent_only_after_terminal_state_is_persisted(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    request_id = _create_approval(client)
    observed = []

    def callback(value):
        observed.append(main.store.get(value).status)
        return True

    monkeypatch.setattr(main, "send_approval_webhook", callback)

    assert client.post(f"/api/admin/expense-approvals/{request_id}/approve").status_code == 200
    assert observed == ["APPROVED"]


def test_callback_failure_does_not_rollback_terminal_status(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    request_id = _create_approval(client)
    monkeypatch.setattr(main, "send_approval_webhook", lambda _: False)

    response = client.post(f"/api/admin/expense-approvals/{request_id}/approve")

    assert response.status_code == 200
    assert client.get(f"/api/expense-approvals/{request_id}").json()["status"] == "APPROVED"
