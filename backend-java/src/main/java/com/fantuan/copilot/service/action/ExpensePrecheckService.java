package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.ExpenseItem;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 报销业务确定性前置校验（V2 §十三 / §十四）。
 *
 * 不是 Planner Tool；只在 ExpenseClaimActionHandler 内部调用。
 * 规则为 V1 固定 demo 规则，输入相同 → 输出确定（禁止 LLM 参与计算）。
 *
 * 当前校验：
 * - items 非空且 amount 均为正数
 * - invoiceIds 与 items 的 invoiceId 一一对应
 */
@Component
public class ExpensePrecheckService {

    public record PrecheckResult(boolean valid, String errorCode, String message) {
    }

    public PrecheckResult validate(List<ExpenseItem> items, List<String> invoiceIds) {
        if (items == null || items.isEmpty()) {
            return new PrecheckResult(false, "EXPENSE_ITEMS_REQUIRED", "报销明细不能为空。");
        }
        for (ExpenseItem item : items) {
            if (item.amount() == null || item.amount().signum() <= 0) {
                return new PrecheckResult(false, "EXPENSE_AMOUNT_INVALID",
                        "费用明细金额必须为正数。");
            }
            if (item.category() == null || item.category().isBlank()) {
                return new PrecheckResult(false, "EXPENSE_ITEMS_REQUIRED",
                        "费用明细缺少类别。");
            }
        }
        if (invoiceIds == null || invoiceIds.isEmpty()) {
            return new PrecheckResult(false, "EXPENSE_INVOICES_REQUIRED",
                    "缺少发票凭证。");
        }
        List<String> itemInvoiceIds = items.stream()
                .map(ExpenseItem::invoiceId).filter(id -> id != null).toList();
        if (itemInvoiceIds.size() != invoiceIds.size()
                || !itemInvoiceIds.containsAll(invoiceIds)
                || !invoiceIds.containsAll(itemInvoiceIds)) {
            return new PrecheckResult(false, "EXPENSE_INVOICES_REQUIRED",
                    "发票凭证与费用明细不一致。");
        }
        return new PrecheckResult(true, null, null);
    }
}
