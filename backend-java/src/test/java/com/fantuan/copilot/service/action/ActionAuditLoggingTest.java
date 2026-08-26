package com.fantuan.copilot.service.action;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import ch.qos.logback.classic.Level;
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
                null,
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
                now.plusSeconds(600),
                null); // action_payload_json: P2-A V6 新增，测试直接构造不填充
        // V2 §十七: Service 依赖 HandlerRegistry；业务 handler 在其它测试验证。
        BusinessActionService service = new BusinessActionService(
                new BusinessActionProperties(),
                mock(com.fantuan.copilot.service.AdminAccessService.class),
                mock(com.fantuan.copilot.repository.action.PendingActionRepository.class),
                new BusinessActionHandlerRegistry(java.util.List.of()),
                new ActionNonceService(),
                mock(com.fantuan.copilot.service.memory.AiTaskMemoryService.class),
                new AdminLogBuffer(),
                java.time.Clock.systemUTC());

        Logger logger = (Logger) LoggerFactory.getLogger(BusinessActionService.class);
        Level previousLevel = logger.getLevel();
        logger.setLevel(Level.INFO);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            service.audit("trace-audit", action, ActionStatus.PROCESSING,
                    ActionStatus.SUCCEEDED, "ACTION_SUCCEEDED", requestId, now);
        } finally {
            logger.detachAppender(appender);
            appender.stop();
            logger.setLevel(previousLevel);
        }

        String joined = appender.list.stream()
                .map(ILoggingEvent::getFormattedMessage)
                .reduce("", (left, right) -> left + "\n" + right);
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
