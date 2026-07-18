package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.ActionStatus;
import org.springframework.http.HttpStatus;

final class ActionStaleException extends ActionException {
    ActionStaleException(String actionId) {
        super(HttpStatus.CONFLICT, "ACTION_STALE",
                "申请状态已变化，请重新生成草稿。", actionId, ActionStatus.FAILED);
    }
}
