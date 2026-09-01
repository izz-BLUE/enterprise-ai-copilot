package com.fantuan.copilot.service.action.handler;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.action.PurchaseActionProposal;
import com.fantuan.copilot.gateway.purchase.PurchaseExecutionGateway;
import com.fantuan.copilot.gateway.purchase.PurchaseExecutionResult;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.PurchaseActionPayload;
import com.fantuan.copilot.service.action.PurchaseActionPayloadCodec;
import com.fantuan.copilot.service.action.PurchaseFactsService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class PurchaseRequestActionHandlerTest {
    private static final Instant NOW = Instant.parse("2026-09-02T10:00:00Z");
    private static final VerifiedIdentity IDENTITY = new VerifiedIdentity(
            "U10001", "zhangsan", "E10001", "张三", AuthRole.EMPLOYEE, true,
            VerifiedIdentity.Source.JWT);

    private PurchaseExecutionGateway gateway;
    private PurchaseActionPayloadCodec codec;
    private PurchaseRequestActionHandler handler;

    @BeforeEach
    void setUp() {
        gateway = mock(PurchaseExecutionGateway.class);
        codec = new PurchaseActionPayloadCodec(new ObjectMapper());
        handler = new PurchaseRequestActionHandler(gateway, new PurchaseFactsService(), codec);
    }

    @Test
    void planUsesJavaFactsAndProducesCanonicalPayload() {
        var plan = handler.planPending(proposal(new BigDecimal("6800.00")),
                IDENTITY, LocalDate.of(2026, 9, 2), NOW);

        PurchaseActionPayload payload = codec.decode(plan.payloadJson());
        assertEquals(1, payload.schemaVersion());
        assertEquals("MacBook Pro", payload.itemName());
        assertEquals(new BigDecimal("6800.00"), payload.requestedBudget());
        assertEquals(new BigDecimal("20000.00"), payload.availableBudget());
        assertEquals("PASS", payload.policyResult());
    }

    @Test
    void planRejectsPythonBudgetFactThatDoesNotMatchJava() {
        ActionException exception = assertThrows(ActionException.class,
                () -> handler.planPending(new PurchaseActionProposal(
                        BusinessActionType.PURCHASE_REQUEST, "MacBook Pro",
                        new BigDecimal("6800.00"), "开发工作站", new BigDecimal("99999.00"), "PASS"),
                        IDENTITY, LocalDate.of(2026, 9, 2), NOW));

        assertEquals("PURCHASE_FACTS_MISMATCH", exception.errorCode());
    }

    @Test
    void confirmRevalidationRejectsChangedOrDeniedPayloadAndExecutePersistsThroughGateway() {
        PurchaseActionPayload payload = new PurchaseActionPayload(
                1, "MacBook Pro", new BigDecimal("6800.00"), "开发工作站",
                new BigDecimal("20000.00"), "PASS");
        PendingAction action = PendingAction.pending(
                "act-purchase-1", BusinessActionType.PURCHASE_REQUEST, "trace-1",
                IDENTITY.userId(), "conv-1", IDENTITY.employeeId(), IDENTITY.displayName(),
                null, null, null, null, null, null, null, new byte[32], NOW,
                NOW.plusSeconds(600), codec.encode(payload));

        assertEquals(null, handler.revalidateBeforeExecute(action));
        when(gateway.submit(any())).thenReturn(new PurchaseExecutionResult("PUR-202609-000001", NOW));
        var result = handler.execute(action, NOW);
        assertEquals("PUR-202609-000001", result.requestId());
        verify(gateway).submit(any());

        PurchaseActionPayload denied = new PurchaseActionPayload(
                1, "办公椅", new BigDecimal("6800.00"), "个人娱乐",
                new BigDecimal("20000.00"), "PASS");
        PendingAction deniedAction = PendingAction.pending(
                "act-purchase-2", BusinessActionType.PURCHASE_REQUEST, "trace-2",
                IDENTITY.userId(), "conv-2", IDENTITY.employeeId(), IDENTITY.displayName(),
                null, null, null, null, null, null, null, new byte[32], NOW,
                NOW.plusSeconds(600), codec.encode(denied));
        assertEquals("PURCHASE_POLICY_STALE", handler.revalidateBeforeExecute(deniedAction));
    }

    @Test
    void unknownProposalSubtypeFailsClosedDuringContractDeserialization() {
        ObjectMapper mapper = new ObjectMapper();

        assertThrows(JsonProcessingException.class, () -> mapper.readValue("""
                {"action_type":"UNKNOWN_PURCHASE_REQUEST","item_name":"MacBook Pro",
                 "requested_budget":6800,"justification":"开发工作",
                 "available_budget":20000,"policy_result":"PASS"}
                """, BusinessActionProposal.class));
    }

    private static PurchaseActionProposal proposal(BigDecimal budget) {
        return new PurchaseActionProposal(BusinessActionType.PURCHASE_REQUEST,
                "MacBook Pro", budget, "开发工作站", new BigDecimal("20000.00"), "PASS");
    }
}
