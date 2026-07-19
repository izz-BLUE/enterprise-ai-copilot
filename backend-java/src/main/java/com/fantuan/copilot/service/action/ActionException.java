package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.ActionStatus;
import org.springframework.http.HttpStatus;

public class ActionException extends RuntimeException {
    private final HttpStatus httpStatus;
    private final String errorCode;
    private final String actionId;
    private final ActionStatus actionStatus;

    public ActionException(HttpStatus httpStatus, String errorCode, String message,
                           String actionId, ActionStatus actionStatus) {
        super(message);
        this.httpStatus = httpStatus;
        this.errorCode = errorCode;
        this.actionId = actionId;
        this.actionStatus = actionStatus;
    }

    public HttpStatus httpStatus() { return httpStatus; }
    public String errorCode() { return errorCode; }
    public String actionId() { return actionId; }
    public ActionStatus actionStatus() { return actionStatus; }
}
