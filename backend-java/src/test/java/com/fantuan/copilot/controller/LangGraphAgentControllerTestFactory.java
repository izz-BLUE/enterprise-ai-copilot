package com.fantuan.copilot.controller;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.identity.IdentityContext;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.BusinessActionHitlCoordinator;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import com.fantuan.copilot.service.task.TaskRuntimeService;

import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** Explicit test wiring for the same mandatory constructor contract as production. */
public final class LangGraphAgentControllerTestFactory {
    private LangGraphAgentControllerTestFactory() {
    }

    public static LangGraphAgentController create(PythonAgentGateway pythonAgentGateway,
                                                  AdminAccessService adminAccessService,
                                                  BusinessActionService businessActionService,
                                                  IdentityContext identityContext,
                                                  AiTaskMemoryService memoryService,
                                                  AdminLogBuffer adminLogBuffer) {
        BusinessActionHitlCoordinator hitlCoordinator = mock(BusinessActionHitlCoordinator.class);
        when(hitlCoordinator.reconcileExpiredBeforeChat(
                (String) any(), (String) any(), any(), (String) any()))
                .thenReturn(true);
        TaskRuntimeService taskRuntimeService = mock(TaskRuntimeService.class);
        when(taskRuntimeService.reconcile(anyString(), anyString()))
                .thenReturn(Optional.empty());
        return new LangGraphAgentController(
                pythonAgentGateway, adminAccessService, businessActionService, identityContext,
                memoryService, adminLogBuffer, new AgentRuntimeThreadIdService(),
                new AgentRuntimeThreadExecutionGuard(), hitlCoordinator, taskRuntimeService);
    }
}
