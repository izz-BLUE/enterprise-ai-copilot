package com.fantuan.copilot.service.action;

import com.fantuan.copilot.gateway.expense.ExpenseExecutionGateway;
import com.fantuan.copilot.gateway.leave.LeaveExecutionGateway;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.model.task.TaskType;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.service.action.handler.AnnualLeaveActionHandler;
import com.fantuan.copilot.service.action.handler.ExpenseClaimActionHandler;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;

class BusinessActionHandlerMetadataTest {

    @Test
    void handlersOwnTaskStatusAndDomainErrorMetadata() {
        BusinessActionHandler leave = new AnnualLeaveActionHandler(
                mock(LeaveAccountRepository.class), mock(LeaveExecutionGateway.class));
        BusinessActionHandler expense = new ExpenseClaimActionHandler(
                mock(ExpenseExecutionGateway.class), mock(ExpensePrecheckService.class),
                mock(ExpenseCalculationService.class), mock(ExpenseActionPayloadCodec.class));
        assertEquals(BusinessActionType.ANNUAL_LEAVE_REQUEST, leave.supports());
        assertEquals(TaskType.LEAVE_REQUEST, leave.taskType());
        assertEquals(TaskExecutionStatus.COMPLETED, leave.statusAfterConfirmation());
        assertEquals(Set.of(),
                leave.deterministicRegistrationRejectionCodes());
        assertEquals(Set.of(), leave.staleFailureCodes());

        assertEquals(BusinessActionType.EXPENSE_CLAIM, expense.supports());
        assertEquals(TaskType.EXPENSE_CLAIM, expense.taskType());
        assertEquals(TaskExecutionStatus.WAITING_EXTERNAL, expense.statusAfterConfirmation());
        assertEquals(Set.of("EXPENSE_ITEMS_REQUIRED",
                "EXPENSE_AMOUNT_INVALID", "EXPENSE_INVOICES_REQUIRED"),
                expense.deterministicRegistrationRejectionCodes());
        assertEquals(Set.of("EXPENSE_TRIP_STALE", "EXPENSE_INVOICE_STALE",
                "EXPENSE_AMOUNT_STALE"), expense.staleFailureCodes());

    }

    @Test
    void registryResolvesMetadataAndFailsClosedForUnknownAction() {
        BusinessActionHandler leave = new AnnualLeaveActionHandler(
                mock(LeaveAccountRepository.class), mock(LeaveExecutionGateway.class));
        BusinessActionHandlerRegistry registry = new BusinessActionHandlerRegistry(List.of(leave));

        assertEquals(TaskType.LEAVE_REQUEST,
                registry.taskTypeFor(BusinessActionType.ANNUAL_LEAVE_REQUEST).orElseThrow());
        assertTrue(registry.handlerFor(BusinessActionType.EXPENSE_CLAIM).isEmpty());
        assertTrue(registry.taskTypeFor(BusinessActionType.EXPENSE_CLAIM).isEmpty());
        assertFalse(registry.acceptsDeterministicRegistrationRejection(
                BusinessActionType.EXPENSE_CLAIM, "BUSINESS_RULE_VIOLATION"));
        assertFalse(registry.acceptsStaleFailureCode(
                BusinessActionType.EXPENSE_CLAIM, "EXPENSE_INVOICE_STALE"));
        assertFalse(registry.acceptsStaleFailureCode(
                BusinessActionType.ANNUAL_LEAVE_REQUEST, "ACTION_STALE"));
    }

    @Test
    void registryRejectsDuplicateActionTypes() {
        BusinessActionHandler leave = new AnnualLeaveActionHandler(
                mock(LeaveAccountRepository.class), mock(LeaveExecutionGateway.class));

        assertThrows(IllegalStateException.class,
                () -> new BusinessActionHandlerRegistry(List.of(leave, leave)));
    }
}
