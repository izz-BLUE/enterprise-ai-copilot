package com.fantuan.copilot.gateway.expense;

import com.fantuan.copilot.dto.admin.MockOaApprovalActionResponse;
import com.fantuan.copilot.dto.admin.MockOaApprovalListResponse;
import com.fantuan.copilot.dto.admin.MockOaApprovalView;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestTemplate;

import java.net.SocketTimeoutException;
import java.net.URI;
import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class MockOaAdminGatewayTest {
    private final RestTemplate restTemplate = mock(RestTemplate.class);
    private final MockOaAdminGateway gateway = new MockOaAdminGateway(
            restTemplate, true, "http://mock-oa:8010/");

    @Test
    void listUsesBoundedEndpointAndMapsSafeBusinessFields() {
        MockOaApprovalView view = new MockOaApprovalView(
                "OA-EXP-1", "PENDING", "EXP-1", "E10001", "TRIP-1", "COST-IT",
                new java.math.BigDecimal("100.00"), new java.math.BigDecimal("90.00"),
                Instant.parse("2026-08-31T00:00:00Z"));
        when(restTemplate.getForObject(any(URI.class), eq(MockOaApprovalListResponse.class)))
                .thenReturn(new MockOaApprovalListResponse(List.of(view), 1));

        MockOaApprovalListResponse response = gateway.list("PENDING");

        assertEquals("OA-EXP-1", response.items().get(0).requestId());
        assertEquals("EXP-1", response.items().get(0).expenseId());
        var uri = org.mockito.ArgumentCaptor.forClass(URI.class);
        verify(restTemplate).getForObject(uri.capture(), eq(MockOaApprovalListResponse.class));
        assertEquals("http://mock-oa:8010/api/admin/expense-approvals?limit=100&status=PENDING",
                uri.getValue().toString());
    }

    @Test
    void decideCallsApproveEndpointWithoutBrowserCredential() {
        when(restTemplate.postForObject(any(URI.class), eq(null), eq(MockOaApprovalActionResponse.class)))
                .thenReturn(new MockOaApprovalActionResponse("OA-EXP-1", "APPROVED"));

        MockOaApprovalActionResponse response = gateway.decide("OA-EXP-1", "APPROVED");

        assertEquals("APPROVED", response.status());
        var uri = org.mockito.ArgumentCaptor.forClass(URI.class);
        verify(restTemplate).postForObject(uri.capture(), eq(null), eq(MockOaApprovalActionResponse.class));
        assertEquals("http://mock-oa:8010/api/admin/expense-approvals/OA-EXP-1/approve",
                uri.getValue().toString());
    }

    @Test
    void disabledProviderReturnsServiceUnavailable() {
        MockOaAdminGateway disabled = new MockOaAdminGateway(restTemplate, false, "http://mock-oa:8010");

        MockOaAdminException exception = assertThrows(MockOaAdminException.class,
                () -> disabled.list(null));

        assertEquals(HttpStatus.SERVICE_UNAVAILABLE.value(), exception.httpStatus());
        assertEquals("MOCK_OA_DISABLED", exception.errorCode());
    }

    @Test
    void downstreamNotFoundAndConflictRemainDistinct() {
        when(restTemplate.postForObject(any(URI.class), eq(null), eq(MockOaApprovalActionResponse.class)))
                .thenThrow(HttpClientErrorException.create(HttpStatus.NOT_FOUND, "not found",
                        HttpHeaders.EMPTY, new byte[0], null));
        MockOaAdminException notFound = assertThrows(MockOaAdminException.class,
                () -> gateway.decide("OA-EXP-MISSING", "APPROVED"));
        assertEquals(HttpStatus.NOT_FOUND.value(), notFound.httpStatus());
        assertEquals("MOCK_OA_APPROVAL_NOT_FOUND", notFound.errorCode());

        when(restTemplate.postForObject(any(URI.class), eq(null), eq(MockOaApprovalActionResponse.class)))
                .thenThrow(HttpClientErrorException.create(HttpStatus.CONFLICT, "conflict",
                        HttpHeaders.EMPTY, new byte[0], null));
        MockOaAdminException conflict = assertThrows(MockOaAdminException.class,
                () -> gateway.decide("OA-EXP-1", "REJECTED"));
        assertEquals(HttpStatus.CONFLICT.value(), conflict.httpStatus());
        assertEquals("MOCK_OA_STATE_CONFLICT", conflict.errorCode());
    }

    @Test
    void timeoutReturnsUnknownResultMessageAndServiceUnavailable() {
        when(restTemplate.postForObject(any(URI.class), eq(null), eq(MockOaApprovalActionResponse.class)))
                .thenThrow(new ResourceAccessException("Read timed out", new SocketTimeoutException("read timed out")));

        MockOaAdminException exception = assertThrows(MockOaAdminException.class,
                () -> gateway.decide("OA-EXP-1", "APPROVED"));

        assertEquals(HttpStatus.SERVICE_UNAVAILABLE.value(), exception.httpStatus());
        assertEquals("MOCK_OA_TIMEOUT", exception.errorCode());
        org.junit.jupiter.api.Assertions.assertTrue(exception.getMessage().contains("结果未知"));
    }
}
