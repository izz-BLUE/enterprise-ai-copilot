package com.fantuan.copilot.repository.memory;

import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;

import java.util.Optional;

/**
 * 任务记忆仓储接口。所有方法都强制以 (userId, conversationId) 复合 key 定位，
 * 杜绝只按 conversationId 查询的可能 —— 这是 P0 的安全 invariant。
 */
public interface AiTaskMemoryRepository {
    /** 按 (userId, conversationId) 复合主键读取；不存在返回 Optional.empty()。 */
    Optional<AiTaskMemory> find(String userId, String conversationId);

    /** upsert：以 (userId, conversationId) 命中时更新 task_type / status / task_state_json / summary / updated_at。 */
    void upsert(String userId, String conversationId, String taskType, TaskStatus status,
                String taskStateJson, String summary);

    /** 按 (userId, conversationId) 删除；返回受影响行数（0 = 不存在或不属于该用户）。 */
    int delete(String userId, String conversationId);
}