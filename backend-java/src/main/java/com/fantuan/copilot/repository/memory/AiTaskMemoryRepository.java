package com.fantuan.copilot.repository.memory;

import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;

import java.util.Optional;

/**
 * 任务记忆仓储接口。所有方法都强制以 (userId, conversationId) 复合 key 定位，
 * 杜绝只按 conversationId 查询的可能 —— 这是 P0 的安全 invariant。
 *
 * 状态机（与 docs/memory-p0-architecture.md 白名单对齐，实现为原子条件 SQL）：
 *   - 无记录 → 仅允许写入 ACTIVE（首条创建）；
 *   - ACTIVE → 允许写入任意状态（UPSERT 续写 / COMPLETE / ABANDON 终结）；
 *   - COMPLETED → 仅允许幂等重放 COMPLETE；
 *   - ABANDONED → 仅允许幂等重放 ABANDON；
 *   - 其余组合（终态重新激活 / 无记录直接终结）由 SQL 条件拒绝并返回 false。
 */
public interface AiTaskMemoryRepository {
    /** 按 (userId, conversationId) 复合主键读取；不存在返回 Optional.empty()。 */
    Optional<AiTaskMemory> find(String userId, String conversationId);

    /**
     * 状态机受限写入（原子，单条 SQL）：
     * 无记录 + status=ACTIVE 插入；已存在 + 状态机白名单命中时覆盖
     * task_type / status / task_state_json / summary / updated_at。
     * 返回 false 表示状态机拒绝（记录存在且不允许该转换），不写入任何内容。
     */
    boolean upsert(String userId, String conversationId, String taskType, TaskStatus status,
                   String taskStateJson, String summary);

    /**
     * 终态收口：仅 ACTIVE → COMPLETED / ABANDONED（及同终态幂等重放）时更新 status，
     * 保留原 task_type / task_state_json / summary。返回是否实际更新；
     * 记录不存在或已是另一终态时返回 false（不抛错，供业务侧无副作用调用）。
     */
    boolean transitionToTerminal(String userId, String conversationId, TaskStatus target);

    /** 按 (userId, conversationId) 删除；返回受影响行数（0 = 不存在或不属于该用户）。 */
    int delete(String userId, String conversationId);
}