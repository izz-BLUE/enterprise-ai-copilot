package com.fantuan.copilot.service.agent;

import com.fantuan.copilot.dto.InternalAgentChatRequest;
import com.fantuan.copilot.dto.memory.AgentMemoryProposal;
import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Optional;

/** Agent 请求的 Memory 读取与旁路写入协调器。 */
public final class AgentMemoryCoordinator {
    private static final Logger log = LoggerFactory.getLogger(AgentMemoryCoordinator.class);
    private final AiTaskMemoryService memoryService;

    public AgentMemoryCoordinator(AiTaskMemoryService memoryService) {
        this.memoryService = memoryService;
    }

    public Optional<InternalAgentChatRequest.MemoryContextView> load(
            String userId, String conversationId, String traceId) {
        try {
            Optional<AiTaskMemory> found = memoryService.find(userId, conversationId);
            if (found.isEmpty() || found.get().status() != TaskStatus.ACTIVE) {
                return Optional.empty();
            }
            AiTaskMemory memory = found.get();
            return Optional.of(new InternalAgentChatRequest.MemoryContextView(
                    memory.taskType(), memory.status().name(),
                    memory.taskStateJson(), memory.summary()));
        } catch (RuntimeException exception) {
            log.warn("[{}] memory context 读取失败: {}", traceId, exception.getMessage());
            return Optional.empty();
        }
    }

    public void persist(AgentMemoryProposal proposal, String userId,
                        String conversationId, String traceId) {
        if (proposal == null) {
            return;
        }
        try {
            memoryService.upsertActiveFromAgent(userId, conversationId, proposal.taskType(),
                    proposal.taskState(), proposal.summary());
        } catch (RuntimeException exception) {
            log.warn("[{}] Memory proposal 持久化失败，主响应继续: type={}",
                    traceId, exception.getClass().getSimpleName());
        }
    }

    public void persistExpenseReasonContinuation(String originalRequest, String userId,
                                                 String conversationId, String traceId) {
        try {
            memoryService.upsertActiveExpenseReasonContinuation(userId, conversationId,
                    originalRequest);
        } catch (RuntimeException exception) {
            log.warn("[{}] Expense reason continuation 持久化失败: type={}", traceId,
                    exception.getClass().getSimpleName());
        }
    }

    public void persistForNextTask(AgentMemoryProposal proposal, String userId,
                                   String conversationId, String traceId) {
        if (proposal == null) {
            return;
        }
        try {
            memoryService.upsertActiveForNextTask(userId, conversationId,
                    proposal.taskType(), proposal.taskState(), proposal.summary());
        } catch (RuntimeException exception) {
            log.warn("[{}] 下一 Task Memory proposal 持久化失败: type={}",
                    traceId, exception.getClass().getSimpleName());
        }
    }

    public void abandon(String userId, String conversationId, String traceId) {
        try {
            memoryService.abandon(userId, conversationId);
        } catch (RuntimeException exception) {
            log.warn("[{}] Task Memory 终态收口失败: type={}", traceId,
                    exception.getClass().getSimpleName());
        }
    }
}
