package com.fantuan.copilot.controller.memory;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.dto.memory.InternalMemoryWriteRequest;
import com.fantuan.copilot.dto.memory.MemoryWriteResponse;
import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import com.fantuan.copilot.service.memory.MemoryWriteException;
import com.fantuan.copilot.service.memory.MemoryWriteScopeService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import jakarta.validation.ValidatorFactory;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Memory write endpoint 的 service-token + Java-signed scope 边界测试。 */
class MemoryWriteControllerTest {

    private static final String INTERNAL_TOKEN = "internal-token";
    private static final String USER_A = "U10001";
    private static final String USER_B = "U10002";
    private static final String CONV = "11111111-1111-1111-1111-111111111111";

    private AiTaskMemoryService memoryService;
    private MemoryWriteScopeService scopeService;
    private MemoryWriteController controller;
    private Validator validator;

    @BeforeEach
    void setUp() {
        memoryService = mock(AiTaskMemoryService.class);
        scopeService = new MemoryWriteScopeService(
                INTERNAL_TOKEN,
                Clock.fixed(Instant.parse("2026-08-20T00:00:00Z"), ZoneOffset.UTC));
        controller = new MemoryWriteController(memoryService, scopeService, new AdminLogBuffer());
        ValidatorFactory factory = Validation.buildDefaultValidatorFactory();
        validator = factory.getValidator();
    }

    @Test
    void javaIssuedScopeDeterminesOwnerAndWrites() {
        when(memoryService.writeFromCommand(eq(USER_A), eq(CONV), eq("UPSERT"),
                eq("LEAVE_REQUEST"), eq("ACTIVE"), any(), eq("摘要")))
                .thenReturn(saved(USER_A, CONV, TaskStatus.ACTIVE));

        ResponseEntityHolder response = call(USER_A, CONV, validRequest(), INTERNAL_TOKEN);

        assertEquals(200, response.status);
        verify(memoryService).writeFromCommand(eq(USER_A), eq(CONV), eq("UPSERT"),
                eq("LEAVE_REQUEST"), eq("ACTIVE"), any(), eq("摘要"));
    }

    @Test
    void bodyIdentityFieldsCannotOverrideScopeOwner() {
        Map<String, Object> state = new LinkedHashMap<>();
        state.put("userId", USER_B);
        InternalMemoryWriteRequest request = new InternalMemoryWriteRequest(
                "UPSERT", "LEAVE_REQUEST", "ACTIVE", state, "摘要");
        when(memoryService.writeFromCommand(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(saved(USER_A, CONV, TaskStatus.ACTIVE));

        call(USER_A, CONV, request, INTERNAL_TOKEN);

        verify(memoryService).writeFromCommand(eq(USER_A), eq(CONV), any(), any(), any(), any(), any());
    }

    @Test
    void internalTokenIsRequired() {
        var exception = assertThrows(MemoryWriteException.class,
                () -> call(USER_A, CONV, validRequest(), "wrong-token"));
        assertEquals("MEMORY_INTERNAL_TOKEN_REQUIRED", exception.errorCode());
        assertEquals(403, exception.httpStatus().value());
        verify(memoryService, never()).writeFromCommand(any(), any(), any(), any(), any(), any(), any());
    }

    @Test
    void missingOrForgedScopeIsRejected() {
        HttpServletRequest request = mockRequest("trace-no-scope", null);
        var exception = assertThrows(MemoryWriteException.class,
                () -> controller.write(CONV, INTERNAL_TOKEN, validRequest(), request));
        assertEquals("MEMORY_SCOPE_INVALID", exception.errorCode());

        HttpServletRequest forged = mockRequest("trace-forged", "forged-scope");
        exception = assertThrows(MemoryWriteException.class,
                () -> controller.write(CONV, INTERNAL_TOKEN, validRequest(), forged));
        assertEquals("MEMORY_SCOPE_INVALID", exception.errorCode());
    }

    @Test
    void scopeConversationMustMatchPath() {
        var exception = assertThrows(MemoryWriteException.class,
                () -> controller.write("other-conversation", INTERNAL_TOKEN,
                        validRequest(), mockRequest("trace-mismatch", scopeService.issue(USER_A, CONV))));
        assertEquals("MEMORY_SCOPE_MISMATCH", exception.errorCode());
        verify(memoryService, never()).writeFromCommand(any(), any(), any(), any(), any(), any(), any());
    }

    @Test
    void invalidConversationPathIsRejected() {
        var exception = assertThrows(MemoryWriteException.class,
                () -> controller.write("../evil/path", INTERNAL_TOKEN,
                        validRequest(), mockRequest("trace-path", scopeService.issue(USER_A, "../evil/path"))));
        assertEquals("MEMORY_CONVERSATION_ID_INVALID", exception.errorCode());
    }

    @Test
    void scopeIsBoundToDifferentUsersWithSameConversation() {
        when(memoryService.writeFromCommand(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(saved(USER_A, CONV, TaskStatus.ACTIVE));

        call(USER_A, CONV, validRequest(), INTERNAL_TOKEN);
        call(USER_B, CONV, validRequest(), INTERNAL_TOKEN);

        verify(memoryService).writeFromCommand(eq(USER_A), eq(CONV), any(), any(), any(), any(), any());
        verify(memoryService).writeFromCommand(eq(USER_B), eq(CONV), any(), any(), any(), any(), any());
    }

    @Test
    void serviceValidationErrorsStayInMemoryErrorContract() {
        when(memoryService.writeFromCommand(any(), any(), any(), any(), any(), any(), any()))
                .thenThrow(new IllegalArgumentException("taskState 包含 trusted 字段，禁止写入: jwt"));

        var exception = assertThrows(MemoryWriteException.class,
                () -> call(USER_A, CONV, validRequest(), INTERNAL_TOKEN));
        assertEquals("MEMORY_TRUSTED_KEY_REJECTED", exception.errorCode());
        assertEquals(400, exception.httpStatus().value());
    }

    @Test
    void dtoLimitsSummary() {
        var request = new InternalMemoryWriteRequest(
                "UPSERT", "LEAVE_REQUEST", "ACTIVE", new LinkedHashMap<>(), "a".repeat(501));
        assertFalse(validator.validate(request).isEmpty());
    }

    @Test
    void pythonWriteEndpointRejectsTerminalActions() {
        // 终态只能由 Java PendingAction 生命周期收口；Python 写入口一律拒绝。
        InternalMemoryWriteRequest complete = new InternalMemoryWriteRequest(
                "COMPLETE", "LEAVE_REQUEST", "COMPLETED", new LinkedHashMap<>(), "done");
        var exception = assertThrows(MemoryWriteException.class,
                () -> call(USER_A, CONV, complete, INTERNAL_TOKEN));
        assertEquals("MEMORY_TERMINAL_NOT_ALLOWED", exception.errorCode());
        assertEquals(409, exception.httpStatus().value());

        InternalMemoryWriteRequest abandon = new InternalMemoryWriteRequest(
                "ABANDON", "LEAVE_REQUEST", "ABANDONED", new LinkedHashMap<>(), "cancelled");
        exception = assertThrows(MemoryWriteException.class,
                () -> call(USER_A, CONV, abandon, INTERNAL_TOKEN));
        assertEquals("MEMORY_TERMINAL_NOT_ALLOWED", exception.errorCode());
        verify(memoryService, never()).writeFromCommand(any(), any(), any(), any(), any(), any(), any());
    }

    @Test
    void pythonWriteEndpointRejectsTerminalStatusViaUpsert() {
        // UPSERT + COMPLETED / ABANDONED 是终态的伪装写法，同样拒绝且不落库。
        InternalMemoryWriteRequest upsertCompleted = new InternalMemoryWriteRequest(
                "UPSERT", "LEAVE_REQUEST", "COMPLETED", new LinkedHashMap<>(), "done");
        var exception = assertThrows(MemoryWriteException.class,
                () -> call(USER_A, CONV, upsertCompleted, INTERNAL_TOKEN));
        assertEquals("MEMORY_TERMINAL_NOT_ALLOWED", exception.errorCode());

        InternalMemoryWriteRequest upsertAbandoned = new InternalMemoryWriteRequest(
                "UPSERT", "LEAVE_REQUEST", "ABANDONED", new LinkedHashMap<>(), "cancelled");
        exception = assertThrows(MemoryWriteException.class,
                () -> call(USER_A, CONV, upsertAbandoned, INTERNAL_TOKEN));
        assertEquals("MEMORY_TERMINAL_NOT_ALLOWED", exception.errorCode());
        verify(memoryService, never()).writeFromCommand(any(), any(), any(), any(), any(), any(), any());
    }

    @Test
    void responseDoesNotExposeScopeOwner() {
        when(memoryService.writeFromCommand(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(saved(USER_A, CONV, TaskStatus.ACTIVE));
        MemoryWriteResponse body = call(USER_A, CONV, validRequest(), INTERNAL_TOKEN).body;
        String text = body.toString();
        assertFalse(text.contains(USER_A));
        assertFalse(text.contains(CONV));
        assertTrue(text.contains("UPSERT"));
    }

    private ResponseEntityHolder call(String userId, String conversationId,
                                      InternalMemoryWriteRequest request, String token) {
        var response = controller.write(conversationId, token, request,
                mockRequest("trace-test", scopeService.issue(userId, conversationId)));
        return new ResponseEntityHolder(response.getStatusCode().value(), response.getBody());
    }

    private InternalMemoryWriteRequest validRequest() {
        return new InternalMemoryWriteRequest(
                "UPSERT", "LEAVE_REQUEST", "ACTIVE",
                new LinkedHashMap<>(Map.of("phase", "clarify")), "摘要");
    }

    private HttpServletRequest mockRequest(String traceId, String scope) {
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn(traceId);
        when(request.getHeader("X-Memory-Write-Scope")).thenReturn(scope);
        return request;
    }

    private AiTaskMemory saved(String userId, String conversationId, TaskStatus status) {
        return new AiTaskMemory(userId, conversationId, "LEAVE_REQUEST", status,
                "{\"phase\":\"clarify\"}", "摘要",
                Instant.parse("2026-08-19T00:00:00Z"), Instant.parse("2026-08-20T00:00:00Z"));
    }

    private record ResponseEntityHolder(int status, MemoryWriteResponse body) {
    }
}
