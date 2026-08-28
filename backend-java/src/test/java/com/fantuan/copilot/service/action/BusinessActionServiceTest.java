package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.gateway.leave.LeaveExecutionGateway;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.handler.AnnualLeaveActionHandler;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.List;
import java.util.Optional;
import java.util.stream.Stream;
import java.lang.reflect.Field;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class BusinessActionServiceTest {
    private static final String ADMIN = "test-admin";
    private static final VerifiedIdentity USER_A = new VerifiedIdentity(
            "U10001", "zhangsan", "E10001", "张三",
            AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);

    @Test
    void serviceDependsOnHandlerRegistryAndNotLeaveRepositories() {
        // V2 §十七: Service 依赖 BusinessActionHandlerRegistry（通用生命周期），
        // 不再直接持有 LeaveExecutionGateway / LeaveAccountRepository。
        var fieldTypes = Stream.of(BusinessActionService.class.getDeclaredFields())
                .map(Field::getType)
                .toList();
        assertTrue(fieldTypes.contains(BusinessActionHandlerRegistry.class));
        assertFalse(fieldTypes.contains(
                com.fantuan.copilot.repository.action.LeaveRequestRepository.class));
        assertFalse(fieldTypes.contains(LeaveExecutionGateway.class));
        assertFalse(fieldTypes.contains(LeaveAccountRepository.class));
    }

    @Test
    void validProposalStoresOnlyNonceDigestAndDoesNotDeductBalance() {
        Fixture f = fixture();
        PendingActionView view = f.service.createPending(standardProposal(), "origin", ADMIN, USER_A, null);
        ArgumentCaptor<PendingAction> captor = ArgumentCaptor.forClass(PendingAction.class);
        verify(f.actions).saveNew(captor.capture());
        assertNotNull(view.confirmationNonce());
        assertEquals(32, captor.getValue().confirmationNonceDigest().length);
        assertFalse(new String(captor.getValue().confirmationNonceDigest()).contains(view.confirmationNonce()));
        verify(f.accounts, never()).updateBalance(anyString(), any(), any());
    }

    @Test
    void featureFlagAdminBalanceConflictAndCapacityAreEnforced() {
        Fixture f = fixture();
        f.properties.setEnabled(false);
        assertCode("BUSINESS_ACTIONS_DISABLED", () -> f.service.createPending(
                standardProposal(), "o", ADMIN, USER_A, null));
        f.properties.setEnabled(true);
        assertCode("ADMIN_REQUIRED", () -> f.service.createPending(
                standardProposal(), "o", "wrong", USER_A, null));

        when(f.actions.countActive()).thenReturn(100);
        assertCode("ACTION_CAPACITY_EXCEEDED", () -> f.service.createPending(
                standardProposal(), "o", ADMIN, USER_A, null));
        when(f.actions.countActive()).thenReturn(0);
        when(f.accounts.findBalanceForUpdate(anyString())).thenReturn(Optional.of(BigDecimal.ZERO));
        assertCode("BUSINESS_RULE_VIOLATION", () -> f.service.createPending(
                standardProposal(), "o", ADMIN, USER_A, null));
        when(f.accounts.findBalanceForUpdate(anyString())).thenReturn(Optional.of(new BigDecimal("5.0")));
        when(f.gateway.hasConflict(anyString(), any(), any())).thenReturn(true);
        assertCode("BUSINESS_RULE_VIOLATION", () -> f.service.createPending(
                standardProposal(), "o", ADMIN, USER_A, null));
    }

    @Test
    void sameConversationRejectsSecondActiveAction() {
        Fixture f = fixture();
        when(f.actions.hasActiveByOwnerAndConversation(USER_A.userId(), "conv-1")).thenReturn(true);
        assertCode("ACTION_CONVERSATION_IN_PROGRESS", () -> f.service.createPending(
                standardProposal(), "o", ADMIN, USER_A, "conv-1"));
        verify(f.actions, never()).saveNew(any());
    }

    @Test
    void nullConversationSkipsSameConversationGuard() {
        Fixture f = fixture();
        PendingActionView view = f.service.createPending(
                standardProposal(), "o", ADMIN, USER_A, null);
        assertNotNull(view.confirmationNonce());
        verify(f.actions, never()).hasActiveByOwnerAndConversation(anyString(), anyString());
    }

    @ParameterizedTest
    @MethodSource("invalidProposals")
    void javaRevalidatesUntrustedProposal(AnnualLeaveActionProposal proposal) {
        assertCode("BUSINESS_RULE_VIOLATION",
                () -> fixture().service.createPending(proposal, "origin", ADMIN, USER_A, null));
    }

    @Test
    void weekendAndHalfDayCalculationsAreAuthoritative() {
        Fixture f = fixture();
        PendingActionView range = f.service.createPending(proposal(LocalDate.of(2026, 7, 17),
                LocalDate.of(2026, 7, 20), HalfDay.NONE, "x"), "o", ADMIN, USER_A, null);
        PendingActionView half = f.service.createPending(proposal(LocalDate.of(2026, 7, 21),
                LocalDate.of(2026, 7, 21), HalfDay.AM, "x"), "o", ADMIN, USER_A, null);
        // V2 §二十五: summary 多态为 Object，按 type 强转具体 record。
        assertEquals(new BigDecimal("2.0"),
                ((com.fantuan.copilot.dto.action.AnnualLeaveSummary) range.summary()).days());
        assertEquals(new BigDecimal("0.5"),
                ((com.fantuan.copilot.dto.action.AnnualLeaveSummary) half.summary()).days());
    }

    static Stream<Arguments> invalidProposals() {
        return Stream.of(
                Arguments.of(new AnnualLeaveActionProposal(null, LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 20), "x", HalfDay.NONE)),
                Arguments.of(proposal(LocalDate.of(2026, 7, 15), LocalDate.of(2026, 7, 16), HalfDay.NONE, "x")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 21), LocalDate.of(2026, 7, 20), HalfDay.NONE, "x")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 8, 20), HalfDay.NONE, "x")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 20), HalfDay.NONE, "")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 20), HalfDay.NONE, "x".repeat(201))),
                Arguments.of(proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 20), HalfDay.NONE, "x\n")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 18), LocalDate.of(2026, 7, 19), HalfDay.NONE, "x")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 21), HalfDay.AM, "x")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 18), LocalDate.of(2026, 7, 18), HalfDay.PM, "x")));
    }

    private static Fixture fixture() {
        BusinessActionProperties properties = new BusinessActionProperties();
        properties.setEnabled(true);
        properties.setRequireAdmin(true);
        PendingActionRepository actions = mock(PendingActionRepository.class);
        LeaveAccountRepository accounts = mock(LeaveAccountRepository.class);
        LeaveExecutionGateway gateway = mock(LeaveExecutionGateway.class);
        when(accounts.findBalanceForUpdate(anyString())).thenReturn(Optional.of(new BigDecimal("5.0")));
        Clock clock = Clock.fixed(Instant.parse("2026-07-16T00:00:00Z"), ZoneId.of("Asia/Shanghai"));
        // V2 §十七: Service 依赖 HandlerRegistry；AnnualLeaveActionHandler 由
        // 真实 handler 承载业务逻辑，accounts/gateway mock 注入 handler。
        BusinessActionHandlerRegistry registry = new BusinessActionHandlerRegistry(
                List.of(new AnnualLeaveActionHandler(accounts, gateway)));
        BusinessActionService service = new BusinessActionService(properties,
                new AdminAccessService(ADMIN), actions, registry,
                new ActionNonceService(), mock(AiTaskMemoryService.class),
                new com.fantuan.copilot.adminlog.AdminLogBuffer(), clock);
        return new Fixture(properties, actions, accounts, gateway, service);
    }

    private static AnnualLeaveActionProposal standardProposal() {
        return proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 20), HalfDay.NONE, "私事");
    }

    private static AnnualLeaveActionProposal proposal(LocalDate start, LocalDate end,
                                                       HalfDay halfDay, String reason) {
        return new AnnualLeaveActionProposal(BusinessActionType.ANNUAL_LEAVE_REQUEST,
                start, end, reason, halfDay);
    }

    private static void assertCode(String code, org.junit.jupiter.api.function.Executable executable) {
        assertEquals(code, assertThrows(ActionException.class, executable).errorCode());
    }

    private record Fixture(BusinessActionProperties properties,
                           PendingActionRepository actions,
                           LeaveAccountRepository accounts,
                           LeaveExecutionGateway gateway,
                           BusinessActionService service) {}
}
