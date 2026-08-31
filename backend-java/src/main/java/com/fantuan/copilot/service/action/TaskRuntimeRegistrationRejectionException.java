package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.PendingActionView;

/**
 * Task Runtime 注册被拒绝时使用的内部 continuation 契约。
 *
 * <p>被拒绝的任务和成功启动的 successor 是两个独立的生命周期对象。此异常
 * 将两项事实跨 coordinator/controller 边界传递，同时不会让 successor 看起来像
 * 当前任务的 PendingAction。</p>
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
