package com.fantuan.copilot.service.memory;

import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;

import java.util.Optional;

/**
 * 仅供 LangGraphAgentController 5-arg 兼容构造器使用的占位实现，
 * 让旧版单测（不连接数据库）也能构造控制器；不做任何持久化。
 *
 * 注意：本类不是 Spring bean，由兼容构造器直接 new。
 */
public final class NoopAiTaskMemoryService extends AiTaskMemoryService {

    public NoopAiTaskMemoryService() {
        super(new com.fantuan.copilot.repository.memory.NoopAiTaskMemoryRepository());
    }

    @Override
    public Optional<AiTaskMemory> find(String userId, String conversationId) {
        return Optional.empty();
    }

    @Override
    public void upsert(String userId, String conversationId, String taskType, TaskStatus status,
                       String taskStateJson, String summary) {
        // no-op：兼容构造器场景不依赖持久化
    }

    @Override
    public void upsert(String userId, String conversationId) {
        // no-op
    }

    @Override
    public int delete(String userId, String conversationId) {
        return 0;
    }
}