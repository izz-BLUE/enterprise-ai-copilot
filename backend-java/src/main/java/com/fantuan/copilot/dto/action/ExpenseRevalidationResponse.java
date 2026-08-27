package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.math.BigDecimal;
import java.util.List;

/** Current facts returned by the deterministic Python Enterprise OA adapter. */
public record ExpenseRevalidationResponse(
        @JsonProperty("schema_version") int schemaVersion,
        boolean success,
        TripFact trip,
        List<InvoiceFact> invoices,
        @JsonProperty("error_code") String errorCode,
        String message) {

    public record TripFact(
            @JsonProperty("trip_id") String tripId,
            @JsonProperty("employee_id") String employeeId,
            @JsonProperty("start_date") String startDate,
            @JsonProperty("end_date") String endDate,
            String status) {
    }

    public record InvoiceFact(
            @JsonProperty("invoice_id") String invoiceId,
            Boolean valid,
            Boolean duplicate,
            BigDecimal amount,
            String category,
            @JsonProperty("ownership_accepted") Boolean ownershipAccepted,
            @JsonProperty("error_code") String errorCode) {
    }
}
