package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.AdminAccessService;
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
import java.util.Optional;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class BusinessActionServiceTest {
    private static final String ADMIN = "test-admin";

    @Test
    void validProposalStoresOnlyNonceDigestAndDoesNotDeductBalance() {
        Fixture f = fixture();
        PendingActionView view = f.service.createPending(standardProposal(), "origin", ADMIN);
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
        assertCode("BUSINESS_ACTIONS_DISABLED", () -> f.service.createPending(standardProposal(), "o", ADMIN));
        f.properties.setEnabled(true);
        assertCode("ADMIN_REQUIRED", () -> f.service.createPending(standardProposal(), "o", "wrong"));

        when(f.actions.countActive()).thenReturn(100);
        assertCode("ACTION_CAPACITY_EXCEEDED", () -> f.service.createPending(standardProposal(), "o", ADMIN));
        when(f.actions.countActive()).thenReturn(0);
        when(f.accounts.findBalanceForUpdate(anyString())).thenReturn(Optional.of(BigDecimal.ZERO));
        assertCode("BUSINESS_RULE_VIOLATION", () -> f.service.createPending(standardProposal(), "o", ADMIN));
        when(f.accounts.findBalanceForUpdate(anyString())).thenReturn(Optional.of(new BigDecimal("5.0")));
        when(f.requests.hasConflict(anyString(), any(), any())).thenReturn(true);
        assertCode("BUSINESS_RULE_VIOLATION", () -> f.service.createPending(standardProposal(), "o", ADMIN));
    }

    @ParameterizedTest
    @MethodSource("invalidProposals")
    void javaRevalidatesUntrustedProposal(AnnualLeaveActionProposal proposal) {
        assertCode("BUSINESS_RULE_VIOLATION",
                () -> fixture().service.createPending(proposal, "origin", ADMIN));
    }

    @Test
    void weekendAndHalfDayCalculationsAreAuthoritative() {
        Fixture f = fixture();
        PendingActionView range = f.service.createPending(proposal(LocalDate.of(2026, 7, 17),
                LocalDate.of(2026, 7, 20), HalfDay.NONE, "x"), "o", ADMIN);
        PendingActionView half = f.service.createPending(proposal(LocalDate.of(2026, 7, 21),
                LocalDate.of(2026, 7, 21), HalfDay.AM, "x"), "o", ADMIN);
        assertEquals(new BigDecimal("2.0"), range.summary().days());
        assertEquals(new BigDecimal("0.5"), half.summary().days());
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
        LeaveRequestRepository requests = mock(LeaveRequestRepository.class);
        when(accounts.findBalanceForUpdate(anyString())).thenReturn(Optional.of(new BigDecimal("5.0")));
        Clock clock = Clock.fixed(Instant.parse("2026-07-16T00:00:00Z"), ZoneId.of("Asia/Shanghai"));
        BusinessActionService service = new BusinessActionService(properties,
                new AdminAccessService(ADMIN), actions, accounts, requests,
                new ActionNonceService(), clock);
        return new Fixture(properties, actions, accounts, requests, service);
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
                           LeaveRequestRepository requests,
                           BusinessActionService service) {}
}
