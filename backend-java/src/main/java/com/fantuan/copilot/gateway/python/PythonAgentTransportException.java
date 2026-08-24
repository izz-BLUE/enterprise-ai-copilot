package com.fantuan.copilot.gateway.python;

import org.springframework.http.HttpStatus;

public class PythonAgentTransportException extends RuntimeException {
    private final HttpStatus responseStatus;

    public PythonAgentTransportException(HttpStatus responseStatus, String message, Throwable cause) {
        super(message, cause);
        this.responseStatus = responseStatus;
    }

    public HttpStatus responseStatus() {
        return responseStatus;
    }
}
