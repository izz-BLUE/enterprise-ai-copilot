package com.fantuan.copilot.model.action;

import java.math.BigDecimal;

/**
 * 报销单费用项（V2 §二十一）。
 *
 * category 取值（Demo）：HOTEL / TAXI / MEAL / TRAIN / FLIGHT；
 * 金额为申报金额；invoiceId 是发票编号（必须经过 invoice_verify_tool 验真）。
 */
public record ExpenseItem(
        String invoiceId,
        String category,
        BigDecimal amount,
        String description) {
}
