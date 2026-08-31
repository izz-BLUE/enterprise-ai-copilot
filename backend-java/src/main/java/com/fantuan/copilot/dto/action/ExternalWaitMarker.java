package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fantuan.copilot.model.action.BusinessActionType;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.regex.Pattern;

/**
 * 仅供内部使用的 Python -> Java 持久化外部报销等待关联信息。
 * 它有意不属于任一浏览器响应 DTO。
 */
public record ExternalWaitMarker(
        @JsonAlias("schema_version") Integer schemaVersion,
        String kind,
        @JsonAlias("wait_id") String waitId,
        @JsonAlias("execution_id") String executionId,
        @JsonAlias("action_type") BusinessActionType actionType,
        @JsonAlias("request_id") String requestId) {

    private static final byte[] DOMAIN =
            "enterprise-ai-copilot:external-wait:v1\0".getBytes(StandardCharsets.UTF_8);
    private static final Pattern WAIT_ID = Pattern.compile("^extwait_[0-9a-f]{64}$");
    private static final Pattern EXECUTION_ID = Pattern.compile("^ex_[0-9a-f]{32}$");
    private static final Pattern REQUEST_ID =
            Pattern.compile("^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$");

    public boolean structurallyValid() {
        return schemaVersion != null && schemaVersion == 1
                && "EXPENSE_APPROVAL".equals(kind)
                && waitId != null && WAIT_ID.matcher(waitId).matches()
                && executionId != null && EXECUTION_ID.matcher(executionId).matches()
                && actionType == BusinessActionType.EXPENSE_CLAIM
                && requestId != null && REQUEST_ID.matcher(requestId).matches();
    }

    public boolean hasExpectedWaitId() {
        return structurallyValid() && waitId.equals(expectedWaitId(executionId, requestId));
    }

    public static String expectedWaitId(String executionId, String expenseId) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(DOMAIN);
            digest.update(executionId.getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(expenseId.getBytes(StandardCharsets.UTF_8));
            return "extwait_" + HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 unavailable", exception);
        }
    }
}
