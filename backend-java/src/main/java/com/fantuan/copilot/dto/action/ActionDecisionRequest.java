package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import jakarta.validation.constraints.NotBlank;

public final class ActionDecisionRequest {
    @NotBlank
    private String confirmationNonce;

    public ActionDecisionRequest() {
    }

    public String confirmationNonce() {
        return confirmationNonce;
    }

    public void setConfirmationNonce(String confirmationNonce) {
        this.confirmationNonce = confirmationNonce;
    }

    @JsonAnySetter
    public void rejectUnknown(String field, Object value) {
        throw new IllegalArgumentException("unsupported action request field");
    }
}
