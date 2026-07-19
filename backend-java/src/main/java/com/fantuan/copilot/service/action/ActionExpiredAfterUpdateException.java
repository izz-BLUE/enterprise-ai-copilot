package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.ActionStatus;
import org.springframework.http.HttpStatus;

final class ActionExpiredAfterUpdateException extends ActionException {
    ActionExpiredAfterUpdateException(String actionId) {
        super(HttpStatus.GONE, "ACTION_EXPIRED",
                "该申请草稿已过期，请重新生成。", actionId, ActionStatus.EXPIRED);
    }
}
