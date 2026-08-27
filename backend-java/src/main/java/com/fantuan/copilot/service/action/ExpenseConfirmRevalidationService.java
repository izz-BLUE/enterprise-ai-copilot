package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.ExpenseRevalidationRequest;
import com.fantuan.copilot.dto.action.ExpenseRevalidationResponse;
import com.fantuan.copilot.gateway.expense.ExpenseAuthoritativeRevalidationGateway;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.ExpenseItem;
import com.fantuan.copilot.model.action.PendingAction;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Confirm-time authoritative expense check.
 *
 * <p>This service has no transaction boundary. It only transports current OA
 * facts and makes the deterministic Java decision; local state is finalized
 * by {@link BusinessActionService} in a separate short transaction.</p>
 */
@Service
public class ExpenseConfirmRevalidationService {
    private static final String TRIP_STALE = "EXPENSE_TRIP_STALE";
    private static final String INVOICE_STALE = "EXPENSE_INVOICE_STALE";
    private static final String AMOUNT_STALE = "EXPENSE_AMOUNT_STALE";

    private final ExpenseAuthoritativeRevalidationGateway gateway;
    private final ExpenseActionPayloadCodec payloadCodec;
    private final ExpensePrecheckService precheck;
    private final ExpenseCalculationService calculation;

    public ExpenseConfirmRevalidationService(
            ExpenseAuthoritativeRevalidationGateway gateway,
            ExpenseActionPayloadCodec payloadCodec,
            ExpensePrecheckService precheck,
            ExpenseCalculationService calculation) {
        this.gateway = gateway;
        this.payloadCodec = payloadCodec;
        this.precheck = precheck;
        this.calculation = calculation;
    }

    /** Returns a bounded stale code, or null when current facts still match. */
    public String revalidate(PendingAction action, String traceId) {
        if (action == null || action.actionType() != BusinessActionType.EXPENSE_CLAIM) {
            return null;
        }

        ExpenseActionPayload payload;
        try {
            payload = payloadCodec.decode(action.actionPayloadJson());
        } catch (RuntimeException exception) {
            return INVOICE_STALE;
        }

        ExpenseRevalidationRequest request;
        try {
            // employeeId and all business identifiers come from the persisted
            // PendingAction payload, never from the browser or Memory.
            request = new ExpenseRevalidationRequest(1, action.employeeId(),
                    payload.tripId(), payload.invoiceIds());
        } catch (RuntimeException exception) {
            return INVOICE_STALE;
        }

        ExpenseRevalidationResponse facts;
        try {
            facts = gateway.revalidate(request, traceId);
        } catch (RuntimeException exception) {
            throw new ExpenseRevalidationUnavailableException(
                    action.actionId(), action.status(), exception);
        }
        if (facts == null || facts.schemaVersion() != 1 || !facts.success()) {
            throw new ExpenseRevalidationUnavailableException(
                    action.actionId(), action.status(), null);
        }

        String tripStale = validateTrip(action, payload, facts.trip());
        if (tripStale != null) {
            return tripStale;
        }
        String invoiceStale = validateInvoices(payload, facts.invoices());
        if (invoiceStale != null) {
            return invoiceStale;
        }

        LocalDate start = parseDate(facts.trip().startDate());
        LocalDate end = parseDate(facts.trip().endDate());
        if (start == null || end == null || end.isBefore(start)) {
            return TRIP_STALE;
        }
        int stayNights = Math.max((int) ChronoUnit.DAYS.between(start, end), 1);

        Map<String, ExpenseRevalidationResponse.InvoiceFact> currentById =
                indexInvoices(facts.invoices());
        List<ExpenseItem> currentItems = payload.items().stream()
                .map(item -> {
                    ExpenseRevalidationResponse.InvoiceFact current = currentById.get(item.invoiceId());
                    return new ExpenseItem(item.invoiceId(), current.category(),
                            current.amount(), item.description());
                })
                .toList();
        if (!precheck.validate(currentItems, payload.invoiceIds()).valid()) {
            return INVOICE_STALE;
        }
        ExpenseCalculationService.CalculationResult current =
                calculation.calculate(currentItems, stayNights);
        if (payload.claimedAmount().compareTo(current.claimedAmount()) != 0
                || payload.reimbursableAmount().compareTo(current.reimbursableAmount()) != 0) {
            return AMOUNT_STALE;
        }
        return null;
    }

    private String validateTrip(PendingAction action, ExpenseActionPayload payload,
                                ExpenseRevalidationResponse.TripFact trip) {
        if (trip == null
                || !Objects.equals(payload.tripId(), trip.tripId())
                || !Objects.equals(action.employeeId(), trip.employeeId())
                || !"APPROVED".equals(trip.status())) {
            return TRIP_STALE;
        }
        return null;
    }

    private String validateInvoices(ExpenseActionPayload payload,
                                    List<ExpenseRevalidationResponse.InvoiceFact> invoices) {
        if (invoices == null || invoices.size() != payload.invoiceIds().size()) {
            return INVOICE_STALE;
        }
        Map<String, ExpenseRevalidationResponse.InvoiceFact> currentById = indexInvoices(invoices);
        if (currentById.size() != payload.invoiceIds().size()) {
            return INVOICE_STALE;
        }
        for (ExpenseItem item : payload.items()) {
            ExpenseRevalidationResponse.InvoiceFact current = currentById.get(item.invoiceId());
            if (current == null
                    || !Objects.equals(item.invoiceId(), current.invoiceId())
                    || !Boolean.TRUE.equals(current.valid())
                    || !Boolean.FALSE.equals(current.duplicate())
                    || !Boolean.TRUE.equals(current.ownershipAccepted())
                    || current.amount() == null
                    || item.amount().compareTo(current.amount()) != 0
                    || !Objects.equals(item.category(), current.category())
                    || current.errorCode() != null) {
                return INVOICE_STALE;
            }
        }
        return null;
    }

    private Map<String, ExpenseRevalidationResponse.InvoiceFact> indexInvoices(
            List<ExpenseRevalidationResponse.InvoiceFact> invoices) {
        Map<String, ExpenseRevalidationResponse.InvoiceFact> indexed = new HashMap<>();
        if (invoices == null) {
            return indexed;
        }
        for (ExpenseRevalidationResponse.InvoiceFact invoice : invoices) {
            if (invoice == null || invoice.invoiceId() == null
                    || indexed.put(invoice.invoiceId(), invoice) != null) {
                return Map.of();
            }
        }
        return indexed;
    }

    private LocalDate parseDate(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            return LocalDate.parse(value);
        } catch (RuntimeException exception) {
            return null;
        }
    }
}
