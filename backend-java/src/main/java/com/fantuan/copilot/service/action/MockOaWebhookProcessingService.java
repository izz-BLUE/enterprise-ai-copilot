package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.webhook.MockOaExpenseApprovalWebhook;
import org.springframework.stereotype.Service;

/** Treats the webhook as a refresh notification for the shared Mock OA status path. */
@Service
public class MockOaWebhookProcessingService {
    private final ExpenseExternalApprovalStatusSyncService statusSyncService;

    public MockOaWebhookProcessingService(ExpenseExternalApprovalStatusSyncService statusSyncService) {
        this.statusSyncService = statusSyncService;
    }

    public void process(MockOaExpenseApprovalWebhook webhook) {
        try {
            statusSyncService.sync(webhook.requestId());
        } catch (ExpenseExternalApprovalStatusSyncException exception) {
            throw new MockOaWebhookProcessingException("Mock OA authoritative status refresh failed", exception);
        }
    }
}
