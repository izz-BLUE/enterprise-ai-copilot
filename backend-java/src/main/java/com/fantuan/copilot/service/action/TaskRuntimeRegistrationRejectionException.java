package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.PendingActionView;

/**
 * Internal continuation contract for a Task Runtime registration rejection.
 *
 * <p>The rejected task and a successfully launched successor are separate
 * lifecycle objects.  This exception carries both facts across the
 * coordinator/controller boundary without making the successor look like the
 * current task's PendingAction.</p>
 */
public final class TaskRuntimeRegistrationRejectionException extends RuntimeException {
    private final ActionException rejection;
    private final PendingActionView successorPendingAction;

    public TaskRuntimeRegistrationRejectionException(ActionException rejection,
                                                      PendingActionView successorPendingAction) {
        super("Task Runtime registration rejected with continuation", rejection);
        this.rejection = java.util.Objects.requireNonNull(rejection, "rejection");
        this.successorPendingAction = successorPendingAction;
    }

    public ActionException rejection() {
        return rejection;
    }

    public PendingActionView successorPendingAction() {
        return successorPendingAction;
    }
}
