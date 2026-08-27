package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/** Persisted Java facts used to query the authoritative Enterprise OA source. */
public record ExpenseRevalidationRequest(
        @JsonProperty("schema_version") int schemaVersion,
        @JsonProperty("employee_id") String employeeId,
        @JsonProperty("trip_id") String tripId,
        @JsonProperty("invoice_ids") List<String> invoiceIds) {

    public ExpenseRevalidationRequest {
        if (schemaVersion != 1) {
            throw new IllegalArgumentException("Unsupported expense revalidation schema version");
        }
        if (employeeId == null || employeeId.isBlank()
                || tripId == null || tripId.isBlank()
                || invoiceIds == null || invoiceIds.isEmpty()) {
            throw new IllegalArgumentException("Expense revalidation identifiers are required");
        }
        invoiceIds = List.copyOf(invoiceIds);
    }
}
