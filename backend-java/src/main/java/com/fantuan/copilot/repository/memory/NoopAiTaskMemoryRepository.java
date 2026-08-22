package com.fantuan.copilot.repository.memory;

import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;

import java.util.Optional;

/** 仅供 NoopAiTaskMemoryService 使用的占位仓储，所有操作空实现。 */
public final class NoopAiTaskMemoryRepository implements AiTaskMemoryRepository {
    @Override
    public Optional<AiTaskMemory> find(String userId, String conversationId) {
        return Optional.empty();
    }

    @Override
    public boolean upsert(String userId, String conversationId, String taskType, TaskStatus status,
                          String taskStateJson, String summary) {
        return true;
    }

    @Override
    public boolean transitionToTerminal(String userId, String conversationId, TaskStatus target) {
        return false;
    }

    @Override
    public int delete(String userId, String conversationId) {
        return 0;
    }
}