package com.fantuan.copilot.controller;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.BusinessActionService;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpEntity;
import org.springframework.http.ResponseEntity;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.client.RestTemplate;

import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class LangGraphAgentActionMappingTest {
    @Test
    void permitIsReleasedBeforePendingActionCreationAndAdminTokenIsNotForwarded() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        BusinessActionService actionService = mock(BusinessActionService.class);
        AdminAccessService admin = mock(AdminAccessService.class);
        when(admin.isAdmin("admin")).thenReturn(true);
        when(actionService.isAllowed("admin")).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 7, 16));
        AnnualLeaveActionProposal proposal = new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST, LocalDate.of(2026, 7, 20),
                LocalDate.of(2026, 7, 20), "私事", HalfDay.NONE);
        PythonAgentResponse python = new PythonAgentResponse("draft", "action", true,
                "business_action", "", List.of(), true, "origin", proposal, List.of());
        when(restTemplate.postForEntity(anyString(), any(HttpEntity.class), eq(PythonAgentResponse.class)))
                .thenReturn(ResponseEntity.ok(python));
        when(actionService.createPending(eq(proposal), eq("origin"), eq("admin")))
                .thenAnswer(invocation -> {
                    assertEquals(0, bulkhead.snapshot().get("active"));
                    return mock(PendingActionView.class);
                });

        LangGraphAgentController controller = new LangGraphAgentController(
                restTemplate, bulkhead, admin, actionService);
        ReflectionTestUtils.setField(controller, "agentBaseUrl", "http://python-agent");
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("trace");
        when(request.getHeader("X-Admin-Token")).thenReturn("admin");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request"), request);
        assertNotNull(response.getBody());
        assertNotNull(response.getBody().pendingAction());

        ArgumentCaptor<HttpEntity> entity = ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(anyString(), entity.capture(), eq(PythonAgentResponse.class));
        assertEquals("true", entity.getValue().getHeaders().getFirst("X-Allow-Business-Actions"));
        assertEquals("2026-07-16", entity.getValue().getHeaders().getFirst("X-Business-Date"));
        assertFalse(entity.getValue().getHeaders().containsKey("X-Admin-Token"));
    }
}
