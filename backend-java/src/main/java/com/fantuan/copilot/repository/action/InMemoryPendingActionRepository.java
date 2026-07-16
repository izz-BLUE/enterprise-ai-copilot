package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionProperties;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Repository;

import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

@Repository
public class InMemoryPendingActionRepository implements PendingActionRepository {
    private final Map<String, PendingAction> actions = new HashMap<>();
    private final BusinessActionProperties properties;
    private final Clock clock;

    public InMemoryPendingActionRepository(BusinessActionProperties properties, Clock clock) {
        this.properties = properties;
        this.clock = clock;
    }

    @Override
    public synchronized void saveNew(PendingAction action) {
        expirePending();
        long pending = actions.values().stream()
                .filter(item -> item.status() == ActionStatus.PENDING_CONFIRMATION
                        || item.status() == ActionStatus.PROCESSING)
                .count();
        if (pending >= properties.getMaxPending()) {
            throw new ActionException(HttpStatus.SERVICE_UNAVAILABLE,
                    "ACTION_CAPACITY_EXCEEDED", "待确认申请数量已达到上限。", null, null);
        }
        actions.put(action.actionId(), action);
        trimCompleted();
    }

    @Override
    public synchronized Optional<PendingAction> find(String actionId) {
        PendingAction action = actions.get(actionId);
        if (action != null) {
            synchronized (action) {
                action.markExpired(clock.instant());
            }
        }
        trimCompleted();
        return Optional.ofNullable(action);
    }

    @Override
    public synchronized void maintainBounds() {
        expirePending();
        trimCompleted();
    }

    @Override
    public synchronized int size() { return actions.size(); }

    private void expirePending() {
        Instant now = clock.instant();
        actions.values().forEach(action -> {
            synchronized (action) {
                action.markExpired(now);
            }
        });
    }

    private void trimCompleted() {
        ArrayList<PendingAction> completed = new ArrayList<>(actions.values().stream()
                .filter(action -> action.status() != ActionStatus.PENDING_CONFIRMATION
                        && action.status() != ActionStatus.PROCESSING)
                .sorted(Comparator.comparing(PendingAction::completedAt,
                        Comparator.nullsLast(Comparator.naturalOrder())))
                .toList());
        int removeCount = Math.max(0, completed.size() - properties.getMaxCompleted());
        for (int index = 0; index < removeCount; index++) {
            actions.remove(completed.get(index).actionId());
        }
    }
}
