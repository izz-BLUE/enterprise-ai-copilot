package com.fantuan.copilot.service.action;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fantuan.copilot.dto.action.ExpenseRevalidationRequest;
import com.fantuan.copilot.dto.action.ExpenseRevalidationResponse;
import com.fantuan.copilot.gateway.expense.ExpenseAuthoritativeRevalidationGateway;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.ExpenseItem;
import com.fantuan.copilot.model.action.PendingAction;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;

class ExpenseConfirmRevalidationServiceTest {
    private static final String EMPLOYEE_ID = "E10001";
    private static final String TRIP_ID = "TRIP-20260818-001";

    private final ExpenseActionPayloadCodec codec = new ExpenseActionPayloadCodec(new ObjectMapper());
    private final ExpensePrecheckService precheck = new ExpensePrecheckService();
    private final ExpenseCalculationService calculation = new ExpenseCalculationService();

    @Test
    void validCurrentFactsPassAndRequestUsesOnlyPersistedIdentifiers() {
        AtomicReference<ExpenseRevalidationRequest> captured = new AtomicReference<>();
        ExpenseConfirmRevalidationService service = service((request, traceId) -> {
            captured.set(request);
            return facts(
                    new ExpenseRevalidationResponse.TripFact(
                            TRIP_ID, EMPLOYEE_ID, "2026-08-18", "2026-08-20", "APPROVED"),
                    invoice("INV-001", "HOTEL", "1600", true, false, true),
                    invoice("INV-002", "TAXI", "230", true, false, true));
        });

        assertEquals(null, service.revalidate(expenseAction(), "trace-1"));
        assertEquals(EMPLOYEE_ID, captured.get().employeeId());
        assertEquals(TRIP_ID, captured.get().tripId());
        assertEquals(List.of("INV-001", "INV-002"), captured.get().invoiceIds());
    }

    @ParameterizedTest
    @MethodSource("staleTripFacts")
    void invalidCurrentTripIsStale(String employeeId, String status,
                                   String startDate, String endDate,
                                   String expectedCode) {
        ExpenseConfirmRevalidationService service = service((request, traceId) -> facts(
                new ExpenseRevalidationResponse.TripFact(
                        TRIP_ID, employeeId, startDate, endDate, status),
                invoice("INV-001", "HOTEL", "1600", true, false, true),
                invoice("INV-002", "TAXI", "230", true, false, true)));

        assertEquals(expectedCode, service.revalidate(expenseAction(), "trace-trip"));
    }

    static Stream<Arguments> staleTripFacts() {
        return Stream.of(
                Arguments.of(EMPLOYEE_ID, "PENDING", "2026-08-18", "2026-08-20", "EXPENSE_TRIP_STALE"),
                Arguments.of(EMPLOYEE_ID, "CANCELLED", "2026-08-18", "2026-08-20", "EXPENSE_TRIP_STALE"),
                Arguments.of("E99999", "APPROVED", "2026-08-18", "2026-08-20", "EXPENSE_TRIP_STALE"),
                Arguments.of(EMPLOYEE_ID, "APPROVED", "bad-date", "2026-08-20", "EXPENSE_TRIP_STALE"),
                Arguments.of(EMPLOYEE_ID, "APPROVED", "2026-08-21", "2026-08-20", "EXPENSE_TRIP_STALE"));
    }

    @Test
    void missingTripIsStale() {
        ExpenseConfirmRevalidationService service = service((request, traceId) -> facts(null,
                invoice("INV-001", "HOTEL", "1600", true, false, true),
                invoice("INV-002", "TAXI", "230", true, false, true)));

        assertEquals("EXPENSE_TRIP_STALE", service.revalidate(expenseAction(), "trace-missing"));
    }

    @ParameterizedTest
    @MethodSource("staleInvoiceFacts")
    void invalidCurrentInvoiceIsStale(Boolean valid, Boolean duplicate,
                                      Boolean ownershipAccepted, String amount,
                                      String category) {
        ExpenseConfirmRevalidationService service = service((request, traceId) -> facts(
                approvedTrip(),
                invoice("INV-001", category, amount, valid, duplicate, ownershipAccepted),
                invoice("INV-002", "TAXI", "230", true, false, true)));

        assertEquals("EXPENSE_INVOICE_STALE", service.revalidate(expenseAction(), "trace-invoice"));
    }

    static Stream<Arguments> staleInvoiceFacts() {
        return Stream.of(
                Arguments.of(false, false, true, "1600", "HOTEL"),
                Arguments.of(true, true, true, "1600", "HOTEL"),
                Arguments.of(true, false, false, "1600", "HOTEL"),
                Arguments.of(true, false, true, "1601", "HOTEL"),
                Arguments.of(true, false, true, "1600", "MEAL"));
    }

    @Test
    void oneStaleInvoiceRejectsTheWholeMultiInvoiceConfirmation() {
        ExpenseConfirmRevalidationService service = service((request, traceId) -> facts(
                approvedTrip(),
                invoice("INV-001", "HOTEL", "1600", true, false, true),
                invoice("INV-002", "TAXI", "230", true, true, true)));

        assertEquals("EXPENSE_INVOICE_STALE", service.revalidate(expenseAction(), "trace-multi"));
    }

    @Test
    void changedTripDatesRecalculateAmountAndRejectStaleProposal() {
        ExpenseConfirmRevalidationService service = service((request, traceId) -> facts(
                new ExpenseRevalidationResponse.TripFact(
                        TRIP_ID, EMPLOYEE_ID, "2026-08-18", "2026-08-21", "APPROVED"),
                invoice("INV-001", "HOTEL", "1600", true, false, true),
                invoice("INV-002", "TAXI", "230", true, false, true)));

        assertEquals("EXPENSE_AMOUNT_STALE", service.revalidate(expenseAction(), "trace-amount"));
    }

    @Test
    void unavailableProviderFailsClosedWithoutCallingBusinessDecision() {
        ExpenseConfirmRevalidationService service = service((request, traceId) -> {
            throw new RuntimeException("provider down");
        });

        ExpenseRevalidationUnavailableException exception = assertThrows(
                ExpenseRevalidationUnavailableException.class,
                () -> service.revalidate(expenseAction(), "trace-unavailable"));
        assertEquals("EXPENSE_REVALIDATION_UNAVAILABLE", exception.errorCode());
    }

    @Test
    void externalBoundaryIsOutsideSpringTransaction() {
        ExpenseConfirmRevalidationService service = service((request, traceId) -> {
            assertFalse(TransactionSynchronizationManager.isActualTransactionActive());
            return facts(approvedTrip(),
                    invoice("INV-001", "HOTEL", "1600", true, false, true),
                    invoice("INV-002", "TAXI", "230", true, false, true));
        });

        assertEquals(null, service.revalidate(expenseAction(), "trace-no-tx"));
    }

    private ExpenseConfirmRevalidationService service(ExpenseAuthoritativeRevalidationGateway gateway) {
        return new ExpenseConfirmRevalidationService(gateway, codec, precheck, calculation);
    }

    private PendingAction expenseAction() {
        List<ExpenseItem> items = List.of(
                new ExpenseItem("INV-001", "HOTEL", new BigDecimal("1600"), "hotel"),
                new ExpenseItem("INV-002", "TAXI", new BigDecimal("230"), "taxi"));
        String payload = codec.encode(TRIP_ID, items, new BigDecimal("1830"),
                new BigDecimal("1730"), "COST-DEFAULT", "expense", List.of("INV-001", "INV-002"));
        return PendingAction.pending("act-revalidate", BusinessActionType.EXPENSE_CLAIM,
                "trace", "user-1", "conversation-1", EMPLOYEE_ID, "User",
                null, null, null, null, null, null, null, new byte[32],
                Instant.parse("2026-08-28T00:00:00Z"),
                Instant.parse("2026-08-29T00:00:00Z"), payload);
    }

    private static ExpenseRevalidationResponse.TripFact approvedTrip() {
        return new ExpenseRevalidationResponse.TripFact(
                TRIP_ID, EMPLOYEE_ID, "2026-08-18", "2026-08-20", "APPROVED");
    }

    private static ExpenseRevalidationResponse.InvoiceFact invoice(
            String invoiceId, String category, String amount,
            Boolean valid, Boolean duplicate, Boolean ownershipAccepted) {
        return new ExpenseRevalidationResponse.InvoiceFact(
                invoiceId, valid, duplicate, new BigDecimal(amount), category,
                ownershipAccepted, null);
    }

    private static ExpenseRevalidationResponse facts(
            ExpenseRevalidationResponse.TripFact trip,
            ExpenseRevalidationResponse.InvoiceFact... invoices) {
        return new ExpenseRevalidationResponse(1, true, trip, List.of(invoices), null, null);
    }
}
