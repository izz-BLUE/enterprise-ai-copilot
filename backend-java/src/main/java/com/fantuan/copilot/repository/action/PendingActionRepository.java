package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.PendingAction;

import java.util.Optional;

public interface PendingActionRepository {
    void saveNew(PendingAction action);
    Optional<PendingAction> find(String actionId);
    void maintainBounds();
    int size();
}
