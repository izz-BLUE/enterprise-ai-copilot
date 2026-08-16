package com.fantuan.copilot.service.action;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.PendingAction;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;

class ActionAuditLoggingTest {

    @Test
    void auditLogsUseStableIrreversibleReferences() {
        String actionId = "act_sensitive-business-identifier";
        String requestId = "REQ-sensitive-business-identifier";
        Instant now = Instant.parse("2026-07-16T00:00:00Z");
        PendingAction action = PendingAction.pending(
                actionId,
                BusinessActionType.ANNUAL_LEAVE_REQUEST,
                "origin-log",
                "DEMO-001",
                "Demo User",
                LocalDate.of(2026, 7, 20),
                LocalDate.of(2026, 7, 20),
                HalfDay.NONE,
                "private reason",
                new BigDecimal("1.0"),
                new BigDecimal("5.0"),
                new BigDecimal("4.0"),
                new byte[32],
                now,
                now.plusSeconds(600));
        BusinessActionService service = new BusinessActionService(
                new BusinessActionProperties(),
                mock(com.fantuan.copilot.service.AdminAccessService.class),
                mock(com.fantuan.copilot.repository.action.PendingActionRepository.class),
                mock(com.fantuan.copilot.repository.action.LeaveAccountRepository.class),
                mock(com.fantuan.copilot.gateway.leave.LeaveExecutionGateway.class),
                new ActionNonceService(),
                java.time.Clock.systemUTC());

        Logger logger = (Logger) LoggerFactory.getLogger(BusinessActionService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            service.audit("trace-audit", action, ActionStatus.PROCESSING,
                    ActionStatus.SUCCEEDED, "ACTION_SUCCEEDED", requestId, now);
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        String joined = appender.list.stream()
                .map(ILoggingEvent::getFormattedMessage)
                .reduce("", (left, right) -> left + "\n" + right);
        if (!joined.contains("actionRef=" + BusinessActionService.auditRef(actionId))) {
            System.err.println("[DIAG] action_audit capture failed: events=" + appender.list.size()
                    + " effectiveLevel=" + logger.getEffectiveLevel()
                    + " isInfoEnabled=" + logger.isInfoEnabled()
                    + " mdcTraceId=" + org.slf4j.MDC.get("traceId")
                    + " joined=[" + joined + "]");
        }
        assertFalse(joined.contains(actionId));
        assertFalse(joined.contains(requestId));
        assertFalse(joined.contains("private reason"));
        assertTrue(joined.contains("actionRef=" + BusinessActionService.auditRef(actionId)));
        assertTrue(joined.contains("requestRef=" + BusinessActionService.auditRef(requestId)));
        assertTrue(joined.contains("trace-audit"));
        assertTrue(joined.contains("statusTo=SUCCEEDED"));
        assertEquals(BusinessActionService.auditRef(actionId),
                BusinessActionService.auditRef(actionId));
        assertNotEquals(BusinessActionService.auditRef(actionId),
                BusinessActionService.auditRef(requestId));
        assertEquals("-", BusinessActionService.auditRef(null));
    }
}
