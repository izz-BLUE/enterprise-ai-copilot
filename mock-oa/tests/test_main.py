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
