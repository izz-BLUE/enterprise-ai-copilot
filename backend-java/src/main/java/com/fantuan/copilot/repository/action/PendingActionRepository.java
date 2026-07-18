package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.PendingAction;

import java.time.Instant;
import java.util.Optional;
import java.util.UUID;

public interface PendingActionRepository {
    void lockControl();
    void saveNew(PendingAction action);
    Optional<PendingAction> find(String actionId);
    Optional<PendingAction> findForUpdate(String actionId);
    int countActive();
    void markProcessing(String actionId, UUID idempotencyKey);
    void markSucceeded(String actionId, String requestId, String message, Instant completedAt);
    void markCancelled(String actionId, String message, Instant completedAt);
    void markFailed(String actionId, String failureCode, Instant completedAt);
    void markExpired(String actionId, Instant completedAt);
    int expirePending(Instant now);
    void maintainBounds(int maxCompleted);
    int size();
}
