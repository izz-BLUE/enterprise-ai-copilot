package com.fantuan.copilot.gateway.expense;

/** 模拟 OA 管理调用的安全错误，避免把下游响应正文或堆栈暴露给浏览器。 */
public class MockOaAdminException extends RuntimeException {
    private final int httpStatus;
    private final String errorCode;

    public MockOaAdminException(int httpStatus, String errorCode, String message) {
        super(message);
        this.httpStatus = httpStatus;
        this.errorCode = errorCode;
    }

    public int httpStatus() {
        return httpStatus;
    }

    public String errorCode() {
        return errorCode;
    }
}
