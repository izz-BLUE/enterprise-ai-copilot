package com.fantuan.copilot.gateway.purchase;

public interface PurchaseExecutionGateway {
    PurchaseExecutionResult submit(PurchaseSubmission submission);
}
