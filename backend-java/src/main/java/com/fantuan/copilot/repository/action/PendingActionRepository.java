package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.PendingAction;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface PendingActionRepository {
    void lockControl();
    void saveNew(PendingAction action);
    Optional<PendingAction> find(String actionId);
    Optional<PendingAction> findForUpdate(String actionId);
    Optional<PendingAction> findByHitlWaitId(String hitlWaitId);
    Optional<PendingAction> findByHitlWaitIdForUpdate(String hitlWaitId);
    Optional<PendingAction> findPendingConfirmationByOwnerAndConversationForUpdate(
            String ownerUserId, String conversationId);
    Optional<PendingAction> findLatestExpiredHitlByOwnerAndConversation(
            String ownerUserId, String conversationId);
    void updateConfirmationNonceDigest(String actionId, byte[] nonceDigest);
    int countActive();
    /** 同一 (ownerUserId, conversationId) 是否已有活动动作（PENDING_CONFIRMATION / PROCESSING）。 */
    boolean hasActiveByOwnerAndConversation(String ownerUserId, String conversationId);
    void markProcessing(String actionId, UUID idempotencyKey);
    void markSucceeded(String actionId, String requestId, String message, Instant completedAt);
    void markCancelled(String actionId, String message, Instant completedAt);
    void markFailed(String actionId, String failureCode, Instant completedAt);
    void markExpired(String actionId, Instant completedAt);
    /** 待过期动作快照：供调用方在批量过期前做 Memory 收口（需 owner/conversation 关联信息）。 */
    List<PendingAction> findExpired(Instant now);
    int expirePending(Instant now);
    void maintainBounds(int maxCompleted);
    int size();
}
