package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.LeaveBalanceResponse;
import com.fantuan.copilot.dto.action.LeaveRequestListResponse;
import com.fantuan.copilot.model.action.LeaveRequest;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class LeaveReadServiceTest {

    private LeaveAccountRepository accounts;
    private LeaveRequestRepository requests;
    private LeaveReadService serviceReal;

    @BeforeEach
    void setUp() {
        accounts = mock(LeaveAccountRepository.class);
        requests = mock(LeaveRequestRepository.class);
        serviceReal = new LeaveReadService(accounts, requests);
    }

    @Test
    void getBalancePassesEmployeeIdStrictlyToRepository() {
        when(accounts.findBalance("EMP-001")).thenReturn(Optional.of(new BigDecimal("4.0")));
        LeaveBalanceResponse response = serviceReal.getBalance("EMP-001");
        assertEquals("EMP-001", response.employeeId());
        assertEquals(new BigDecimal("4.0"), response.annualBalance());
        verify(accounts).findBalance("EMP-001");
    }

    @Test
    void getBalanceBlankEmployeeIdRaisesBadRequest() {
        ActionException ex = assertThrows(ActionException.class,
                () -> serviceReal.getBalance(""));
        assertEquals("EMPLOYEE_ID_REQUIRED", ex.errorCode());
        assertEquals(org.springframework.http.HttpStatus.BAD_REQUEST, ex.httpStatus());
    }

    @Test
    void getBalanceNullEmployeeIdRaisesBadRequest() {
        ActionException ex = assertThrows(ActionException.class,
                () -> serviceReal.getBalance(null));
        assertEquals("EMPLOYEE_ID_REQUIRED", ex.errorCode());
    }

    @Test
    void getBalanceMissingAccountRaises404() {
        when(accounts.findBalance(anyString())).thenReturn(Optional.empty());
        ActionException ex = assertThrows(ActionException.class,
                () -> serviceReal.getBalance("EMP-001"));
        assertEquals("LEAVE_ACCOUNT_NOT_FOUND", ex.errorCode());
        assertEquals(org.springframework.http.HttpStatus.NOT_FOUND, ex.httpStatus());
    }

    @Test
    void listRequestsPassesEmployeeIdStrictlyToRepository() {
        when(requests.findRecentByEmployee("EMP-001", 5)).thenReturn(List.of());
        LeaveRequestListResponse response = serviceReal.listRequests("EMP-001", 5);
        assertEquals("EMP-001", response.employeeId());
        assertEquals(0, response.total());
        verify(requests).findRecentByEmployee("EMP-001", 5);
    }

    @Test
    void listRequestsLimitAboveMaxIsClamped() {
        when(requests.findRecentByEmployee(eq("EMP-001"), eq(50))).thenReturn(List.of());
        serviceReal.listRequests("EMP-001", 999);
        verify(requests).findRecentByEmployee("EMP-001", 50);
    }

    @Test
    void listRequestsLimitNonPositiveFallsBackToDefault() {
        when(requests.findRecentByEmployee(eq("EMP-001"), eq(20))).thenReturn(List.of());
        serviceReal.listRequests("EMP-001", 0);
        verify(requests).findRecentByEmployee("EMP-001", 20);
    }

    @Test
    void listRequestsBlankEmployeeIdRaisesBadRequest() {
        ActionException ex = assertThrows(ActionException.class,
                () -> serviceReal.listRequests("", 5));
        assertEquals("EMPLOYEE_ID_REQUIRED", ex.errorCode());
        verify(requests, org.mockito.Mockito.never()).findRecentByEmployee(anyString(), anyInt());
    }

    @Test
    void listRequestsReturnsItemsWithStatusSucceeded() {
        LeaveRequest r = new LeaveRequest("LR-001", "EMP-001", "ANNUAL",
                LocalDate.of(2026, 7, 1), LocalDate.of(2026, 7, 3),
                com.fantuan.copilot.model.action.HalfDay.NONE, new BigDecimal("3.0"),
                Instant.parse("2026-07-01T00:00:00Z"));
        when(requests.findRecentByEmployee("EMP-001", 20)).thenReturn(List.of(r));
        LeaveRequestListResponse response = serviceReal.listRequests("EMP-001", null);
        assertEquals("EMP-001", response.employeeId());
        assertEquals(1, response.total());
        assertEquals("LR-001", response.items().get(0).requestId());
        assertEquals("SUCCEEDED", response.items().get(0).status());
    }
}