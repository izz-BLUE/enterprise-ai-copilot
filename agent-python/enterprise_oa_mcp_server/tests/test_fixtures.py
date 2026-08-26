"""test_fixtures.py —— Enterprise OA MCP server 内存 fixture 完整性

V2 §七：
- 3 条 travel fixture
- 5 张 invoice fixture，覆盖 valid / duplicate / invalid / 他员工
"""

from __future__ import annotations

from enterprise_oa_mcp_server import fixtures


class TestTravelFixtures:
    def test_three_trips_for_employee_e10001(self):
        trips = fixtures.list_trips_for_employee("E10001")
        assert len(trips) == 3, "应为 3 条出差 fixture"

    def test_trip_has_expense_documents(self):
        trips = fixtures.list_trips_for_employee("E10001")
        trip_with_docs = next(t for t in trips if t["trip_id"] == "TRIP-20260818-001")
        docs = trip_with_docs["expense_documents"]
        assert len(docs) >= 1
        for doc in docs:
            assert "invoice_id" in doc
            assert "category" in doc
            assert "declared_amount" in doc

    def test_unknown_employee_returns_empty(self):
        assert fixtures.list_trips_for_employee("E99999") == []

    def test_other_employee_has_own_trip(self):
        trips = fixtures.list_trips_for_employee("E10002")
        assert len(trips) >= 1
        assert all(t["employee_id"] == "E10002" for t in trips)


class TestInvoiceFixtures:
    def test_invoice_lookup_returns_record(self):
        inv = fixtures.get_invoice("INV-001")
        assert inv is not None
        assert inv["owner_employee_id"] == "E10001"

    def test_duplicate_invoice_flag(self):
        inv = fixtures.get_invoice("INV-004")
        assert inv["duplicate"] is True

    def test_invalid_invoice_flag(self):
        inv = fixtures.get_invoice("INV-006")
        assert inv["valid"] is False

    def test_other_employee_invoice(self):
        inv = fixtures.get_invoice("INV-005")
        assert inv["owner_employee_id"] == "E10002"

    def test_unknown_invoice_returns_none(self):
        assert fixtures.get_invoice("INV-DOES-NOT-EXIST") is None

    def test_invoice_fixtures_cover_required_states(self):
        """至少 1 valid + 1 duplicate + 1 invalid + 1 他员工（V2 §七）。"""
        e10001 = fixtures.get_invoice("INV-001")
        assert e10001["valid"] is True and e10001["duplicate"] is False
        assert fixtures.get_invoice("INV-004")["duplicate"] is True
        assert fixtures.get_invoice("INV-006")["valid"] is False
        assert fixtures.get_invoice("INV-005")["owner_employee_id"] != "E10001"
