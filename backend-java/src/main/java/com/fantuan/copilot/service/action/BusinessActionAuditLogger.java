package com.fantuan.copilot.service.action;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.adminlog.AdminLogEvent;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.PendingAction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HexFormat;

/** 业务动作审计输出与脱敏引用生成。 */
final class BusinessActionAuditLogger {
    private static final Logger log = LoggerFactory.getLogger(BusinessActionService.class);
    private final AdminLogBuffer buffer;

    BusinessActionAuditLogger(AdminLogBuffer buffer) {
        this.buffer = buffer;
    }

    void audit(String traceId, PendingAction action, ActionStatus from,
               ActionStatus to, String resultCode, String requestId, Instant completedAt) {
        log.info("action_audit traceId={} originTraceId={} actionRef={} actionType={} "
                        + "statusFrom={} statusTo={} resultCode={} requestRef={} createdAt={} completedAt={}",
                traceId, action.originTraceId(), auditRef(action.actionId()), action.actionType(), from, to,
                resultCode, auditRef(requestId), action.createdAt(), completedAt);
        try {
            buffer.record(level(to), AdminLogEvent.CATEGORY_BUSINESS_ACTION, resultCode,
                    traceId, auditRef(action.employeeId()), auditRef(action.actionId()),
                    from == null ? null : from.name(), to == null ? null : to.name(),
                    null, message(resultCode));
        } catch (RuntimeException exception) {
            log.warn("[{}] admin log 写入失败: {}", traceId, exception.getMessage());
        }
    }

    private static String level(ActionStatus status) {
        if (status == ActionStatus.EXPIRED) return AdminLogEvent.LEVEL_WARN;
        if (status == ActionStatus.FAILED) return AdminLogEvent.LEVEL_ERROR;
        return AdminLogEvent.LEVEL_INFO;
    }

    private static String message(String resultCode) {
        return switch (resultCode) {
            case "ACTION_CREATED" -> "Business action created";
            case "ACTION_SUCCEEDED" -> "Business action succeeded";
            case "ACTION_CANCELLED" -> "Business action cancelled";
            case "ACTION_EXPIRED" -> "Business action expired";
            case "ACTION_FAILED" -> "Business action failed";
            case "ACTION_STALE" -> "Business action stale";
            case "ACTION_CONVERSATION_IN_PROGRESS" -> "Conversation has active action";
            case "ACTION_CAPACITY_EXCEEDED" -> "Pending action capacity exceeded";
            default -> "Business action event";
        };
    }

    static String auditRef(String rawId) {
        if (rawId == null) return "-";
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(rawId.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest, 0, 6);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 unavailable", exception);
        }
    }
}
