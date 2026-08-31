package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.ExpenseItem;

import java.math.BigDecimal;
import java.util.List;

/** canonical EXPENSE_CLAIM payload 的聚焦不可变值对象。 */
public record ExpenseActionPayload(
        int schemaVersion,
        String tripId,
        List<ExpenseItem> items,
        BigDecimal claimedAmount,
        BigDecimal reimbursableAmount,
        String costCenter,
        String reason,
        List<String> invoiceIds) {

    public ExpenseActionPayload {
        if (schemaVersion != 1) {
            throw new IllegalArgumentException("Unsupported expense payload schema version");
        }
        if (tripId == null || tripId.isBlank()
                || items == null || items.isEmpty()
                || claimedAmount == null || reimbursableAmount == null
                || costCenter == null || costCenter.isBlank()
                || reason == null || reason.isBlank()
                || invoiceIds == null || invoiceIds.isEmpty()) {
            throw new IllegalArgumentException("Expense payload fields are required");
        }
        if (items.stream().anyMatch(item -> item == null
                || item.invoiceId() == null || item.invoiceId().isBlank()
                || item.category() == null || item.category().isBlank()
                || item.amount() == null)) {
            throw new IllegalArgumentException("Expense payload items are invalid");
        }
        if (invoiceIds.stream().anyMatch(id -> id == null || id.isBlank())) {
            throw new IllegalArgumentException("Expense payload invoice ids are invalid");
        }
        items = List.copyOf(items);
        invoiceIds = List.copyOf(invoiceIds);
    }
}
