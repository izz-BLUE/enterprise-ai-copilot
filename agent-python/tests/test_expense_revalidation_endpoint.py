"""Deterministic tests for the Java-facing Enterprise OA fact adapter."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import main
from app.integrations.mcp.enterprise_oa_client import OaMcpClientError


class FakeEnterpriseOaClient:
    def __init__(self) -> None:
        self.travel_calls: list[dict] = []
        self.invoice_calls: list[dict] = []
        self.travel_result = {
            'success': True,
            'items': [{
                'trip_id': 'TRIP-1',
                'employee_id': 'E10001',
                'start_date': '2026-08-18',
                'end_date': '2026-08-20',
                'status': 'APPROVED',
            }],
        }
        self.invoice_results = {
            'INV-1': {
                'success': True,
                'invoice_id': 'INV-1',
                'valid': True,
                'duplicate': False,
                'amount': 1600,
                'category': 'HOTEL',
            },
        }

    def travel_record_get(self, *, employee_id: str, limit: int = 10) -> dict:
        self.travel_calls.append({'employee_id': employee_id, 'limit': limit})
        return self.travel_result

    def invoice_verify(self, *, invoice_id: str, employee_id: str) -> dict:
        self.invoice_calls.append({'invoice_id': invoice_id, 'employee_id': employee_id})
        result = self.invoice_results[invoice_id]
        if isinstance(result, Exception):
            raise result
        return result


client = TestClient(main.app)


def request_body(*invoice_ids: str) -> dict:
    return {
        'schema_version': 1,
        'employee_id': 'E10001',
        'trip_id': 'TRIP-1',
        'invoice_ids': list(invoice_ids),
    }


def test_happy_path_returns_current_facts_without_agent_execution(monkeypatch):
    fake = FakeEnterpriseOaClient()
    monkeypatch.setattr(main, 'get_enterprise_oa_client', lambda: fake)

    response = client.post('/agent/internal/expense/revalidate', json=request_body('INV-1'))

    assert response.status_code == 200
    assert response.json() == {
        'schema_version': 1,
        'success': True,
        'trip': {
            'trip_id': 'TRIP-1',
            'employee_id': 'E10001',
            'start_date': '2026-08-18',
            'end_date': '2026-08-20',
            'status': 'APPROVED',
        },
        'invoices': [{
            'invoice_id': 'INV-1',
            'valid': True,
            'duplicate': False,
            'amount': '1600',
            'category': 'HOTEL',
            'ownership_accepted': True,
            'error_code': None,
        }],
        'error_code': None,
        'message': None,
    }
    assert fake.travel_calls == [{'employee_id': 'E10001', 'limit': 20}]
    assert fake.invoice_calls == [{'invoice_id': 'INV-1', 'employee_id': 'E10001'}]


def test_missing_trip_is_a_current_fact_not_transport_failure(monkeypatch):
    fake = FakeEnterpriseOaClient()
    fake.travel_result = {'success': True, 'items': []}
    monkeypatch.setattr(main, 'get_enterprise_oa_client', lambda: fake)

    response = client.post('/agent/internal/expense/revalidate', json=request_body('INV-1'))

    assert response.status_code == 200
    assert response.json()['success'] is True
    assert response.json()['trip'] is None


def test_invoice_ownership_business_error_is_returned_as_fact(monkeypatch):
    fake = FakeEnterpriseOaClient()
    fake.invoice_results['INV-1'] = {
        'success': False,
        'error_code': 'OA_MCP_INVOICE_OWNERSHIP',
        'message': 'ownership rejected',
    }
    monkeypatch.setattr(main, 'get_enterprise_oa_client', lambda: fake)

    response = client.post('/agent/internal/expense/revalidate', json=request_body('INV-1'))

    assert response.status_code == 200
    assert response.json()['invoices'] == [{
        'invoice_id': None,
        'valid': None,
        'duplicate': None,
        'amount': None,
        'category': None,
        'ownership_accepted': None,
        'error_code': 'OA_MCP_INVOICE_OWNERSHIP',
    }]


def test_mcp_transport_failure_is_retryable_503(monkeypatch):
    fake = FakeEnterpriseOaClient()
    fake.invoice_results['INV-1'] = OaMcpClientError('OA_MCP_TIMEOUT', 'timeout')
    monkeypatch.setattr(main, 'get_enterprise_oa_client', lambda: fake)

    response = client.post('/agent/internal/expense/revalidate', json=request_body('INV-1'))

    assert response.status_code == 503
    assert response.json()['error_code'] == 'EXPENSE_REVALIDATION_UNAVAILABLE'
    assert response.headers['Retry-After'] == '1'


def test_request_is_strict_and_invoice_count_is_bounded(monkeypatch):
    fake = FakeEnterpriseOaClient()
    monkeypatch.setattr(main, 'get_enterprise_oa_client', lambda: fake)

    extra = client.post(
        '/agent/internal/expense/revalidate',
        json={**request_body('INV-1'), 'memory': 'must-not-be-accepted'},
    )
    too_many = client.post(
        '/agent/internal/expense/revalidate',
        json=request_body(*[f'INV-{i}' for i in range(21)]),
    )

    assert extra.status_code == 422
    assert too_many.status_code == 422
    assert fake.travel_calls == []
