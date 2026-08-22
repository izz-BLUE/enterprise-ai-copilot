package com.fantuan.copilot.service.memory;

import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.repository.memory.AiTaskMemoryRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;

/**
 * Scoped Conversation Memory / Task Continuity P0 —— 任务记忆服务。
 *
 * 设计 invariant：
 *  1. userId 永远来自服务端 VerifiedIdentity.userId()，不接受客户端传入。
 *  2. 所有方法都强制以 (userId, conversationId) 复合 key 定位，不允许只按 conversationId 查询。
 *  3. 写入前对 task_state_json / summary 做长度边界检查（与 DB CHECK 约束对齐），
 *     并拒绝任何疑似敏感字段写入；字符串值做敏感内容脱敏（与 Python 同规则兜底）。
 *  4. 状态机：无记录仅允许 ACTIVE；ACTIVE 可写入任意状态；
 *     COMPLETED / ABANDONED 仅允许同终态幂等重放；其余转换由仓储原子 SQL 拒绝
 *     （抛 MEMORY_STATE_CONFLICT 409），终态不可能被后写重新激活。
 *  5. P0 阶段不引入 Safety Guard / Memory Write Guard / Prompt 注入 / Planner 联动。
 */
@Service
public class AiTaskMemoryService {

    /** 与 DB CHECK ck_ai_task_memory_state_json_len 保持一致 (octet_length <= 16384)。 */
    static final int MAX_TASK_STATE_JSON_BYTES = 16 * 1024;
    /** 与 DB CHECK ck_ai_task_memory_summary_len 保持一致 (char_length <= 500)。 */
    static final int MAX_SUMMARY_CHARS = 500;
    static final int MAX_TASK_TYPE_CHARS = 64;
    static final String DEFAULT_TASK_TYPE = "GENERIC";

    /**
     * taskState 中禁止出现的顶层 / 嵌套 trusted key 集合。
     * 与 Python memory_write_policy._FORBIDDEN_TASK_STATE_KEYS 对齐（camelCase + snake_case 同检）。
     * 不依赖 Python 已过滤 —— Java 侧兜底。
     */
    static final java.util.Set<String> FORBIDDEN_TASK_STATE_KEYS = java.util.Set.of(
            "userId", "user_id",
            "employeeId", "employee_id",
            "conversationId", "conversation_id",
            "role", "permission",
            "allowEval", "allow_eval",
            "allowBusinessActions", "allow_business_actions",
            "businessDate", "business_date",
            "traceId", "trace_id",
            "token", "jwt", "password",
            "nonce", "idempotencyKey", "idempotency_key"
    );

    /**
     * 敏感字符串内容 marker（子串匹配、大小写不敏感），与 Python
     * memory_write_policy._REDACT_MARKERS 对齐。结构化路径（Map）命中后
     * 整串替换为 [REDACTED]；JSON 字符串路径（无法安全替换序列化内容）命中后拒绝。
     */
    static final List<String> SENSITIVE_VALUE_MARKERS = List.of(
            "bearer ", "jwt", "password=", "password:", "token=", "token:",
            "nonce=", "idempotency-key=");
    static final String REDACTED = "[REDACTED]";

    private final AiTaskMemoryRepository repository;
    private final ObjectMapper objectMapper;

    @org.springframework.beans.factory.annotation.Autowired
    public AiTaskMemoryService(AiTaskMemoryRepository repository) {
        this(repository, new ObjectMapper());
    }

    public AiTaskMemoryService(AiTaskMemoryRepository repository, ObjectMapper objectMapper) {
        this.repository = repository;
        this.objectMapper = objectMapper;
    }

    public Optional<AiTaskMemory> find(String userId, String conversationId) {
        return repository.find(userId, conversationId);
    }

    /**
     * upsert：以 (userId, conversationId) 命中则更新，否则插入。
     * 输入做基础边界检查，越界或含敏感字段时抛 IllegalArgumentException。
     * 状态机不允许的转换（无记录写终态 / 终态重新激活）抛 409 MEMORY_STATE_CONFLICT。
     */
    public void upsert(String userId, String conversationId, String taskType, TaskStatus status,
                       String taskStateJson, String summary) {
        requireOwner("userId", userId);
        requireOwner("conversationId", conversationId);
        String safeTaskType = sanitizeTaskType(taskType);
        TaskStatus safeStatus = requireStatus(status);
        String safeJson = sanitizeTaskStateJson(taskStateJson);
        String safeSummary = sanitizeSummary(summary);
        if (!repository.upsert(userId, conversationId, safeTaskType, safeStatus, safeJson, safeSummary)) {
            throw stateConflict(userId, conversationId, safeStatus);
        }
    }

    /**
     * Phase 4B MemoryWrite API 入口：接受 Map 形式 taskState + action 字符串。
     *
     * 行为：
     *  1. 校验 userId / conversationId；
     *  2. trusted-key 剥离（递归）：拒绝含 FORBIDDEN_TASK_STATE_KEYS 的 taskState；
     *  3. action → status 映射（UPSERT 保留 body.status；COMPLETE→COMPLETED；ABANDON→ABANDONED）；
     *  4. taskState → JSON 序列化 → 大小校验 → 写入（状态机受限）；
     *  5. 写入后 re-fetch 拿 updated_at 返回。
     *
     * 抛 IllegalArgumentException 用于 DTO / 业务校验失败（Controller 层映射为 400）；
     * 状态机拒绝映射为 409 MEMORY_STATE_CONFLICT。
     */
    public AiTaskMemory writeFromCommand(String userId, String conversationId,
                                         String action, String taskType, String status,
                                         Map<String, Object> taskState, String summary) {
        requireOwner("userId", userId);
        requireOwner("conversationId", conversationId);

        TaskStatus resolvedStatus = resolveStatus(action, status);
        String safeTaskType = sanitizeTaskType(taskType);

        Map<String, Object> sanitizedState = sanitizeTaskStateMap(taskState);
        String safeJson = serializeTaskState(sanitizedState);
        String safeSummary = sanitizeSummary(summary);

        if (!repository.upsert(userId, conversationId, safeTaskType, resolvedStatus,
                safeJson, safeSummary)) {
            throw stateConflict(userId, conversationId, resolvedStatus);
        }
        return repository.find(userId, conversationId)
                .orElseThrow(() -> new IllegalStateException(
                        "Memory upsert 后立即 re-fetch 失败: userId=" + userId
                                + " conversationId=" + conversationId));
    }

    /**
     * 便捷 upsert：默认值填充，用于 P0 阶段绝大多数"先存一个空记录"的入口。
     */
    public void upsert(String userId, String conversationId) {
        upsert(userId, conversationId, DEFAULT_TASK_TYPE, TaskStatus.ACTIVE, "{}", "");
    }

    /**
     * 业务终态收口：仅 ACTIVE → COMPLETED（同终态幂等）。
     * 记录不存在或已是 ABANDONED 时返回 false，不抛错 —— 供 PendingAction
     * 确认 / 成功等生命周期收口路径无副作用调用。
     */
    public boolean complete(String userId, String conversationId) {
        return repository.transitionToTerminal(userId, conversationId, TaskStatus.COMPLETED);
    }

    /**
     * 业务终态收口：仅 ACTIVE → ABANDONED（同终态幂等）。
     * 记录不存在或已是 COMPLETED 时返回 false，不抛错 —— 供 PendingAction
     * 取消 / 过期 / 创建失败等生命周期收口路径无副作用调用。
     */
    public boolean abandon(String userId, String conversationId) {
        return repository.transitionToTerminal(userId, conversationId, TaskStatus.ABANDONED);
    }

    /** 按 (userId, conversationId) 删除。返回受影响行数：0 = 不存在或不属于该用户。 */
    public int delete(String userId, String conversationId) {
        return repository.delete(userId, conversationId);
    }

    // -----------------------------------------------------------------------
    // Validation helpers
    // -----------------------------------------------------------------------

    private static void requireOwner(String field, String value) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " 不能为空");
        }
        if (value.length() > 64) {
            throw new IllegalArgumentException(field + " 长度超过 64");
        }
    }

    private static TaskStatus requireStatus(TaskStatus status) {
        if (status == null) {
            throw new IllegalArgumentException("status 不能为空");
        }
        return status;
    }

    private static TaskStatus resolveStatus(String action, String status) {
        if (action == null) {
            throw new IllegalArgumentException("action 不能为空");
        }
        switch (action) {
            case "UPSERT":
                if (status == null || status.isBlank()) {
                    throw new IllegalArgumentException("action=UPSERT 必须显式提供 status");
                }
                return TaskStatus.parse(status)
                        .orElseThrow(() -> new IllegalArgumentException(
                                "status 非法: " + status + "，必须是 ACTIVE/COMPLETED/ABANDONED"));
            case "COMPLETE":
                if (status == null || status.isBlank() || "COMPLETED".equals(status)) {
                    return TaskStatus.COMPLETED;
                }
                throw new IllegalArgumentException(
                        "action=COMPLETE 与 status=" + status + " 不匹配；期望 COMPLETED");
            case "ABANDON":
                if (status == null || status.isBlank() || "ABANDONED".equals(status)) {
                    return TaskStatus.ABANDONED;
                }
                throw new IllegalArgumentException(
                        "action=ABANDON 与 status=" + status + " 不匹配；期望 ABANDONED");
            default:
                throw new IllegalArgumentException(
                        "action 非法: " + action + "，必须是 UPSERT/COMPLETE/ABANDON");
        }
    }

    private static String sanitizeTaskType(String taskType) {
        String value = taskType == null ? DEFAULT_TASK_TYPE : taskType.trim();
        if (value.isEmpty()) {
            value = DEFAULT_TASK_TYPE;
        }
        if (value.length() > MAX_TASK_TYPE_CHARS) {
            throw new IllegalArgumentException("task_type 长度超过 " + MAX_TASK_TYPE_CHARS);
        }
        return value;
    }

    private static String sanitizeTaskStateJson(String raw) {
        String value = raw == null ? "{}" : raw;
        // octet_length：以 UTF-8 字节数与 DB CHECK 对齐。
        if (value.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > MAX_TASK_STATE_JSON_BYTES) {
            throw new IllegalArgumentException(
                    "task_state_json 字节数超过 " + MAX_TASK_STATE_JSON_BYTES);
        }
        // JSON 字符串路径无法在保持结构的前提下安全替换敏感内容，命中即拒绝（fail-closed）。
        if (containsSensitiveMarker(value)) {
            throw new IllegalArgumentException("task_state_json 包含敏感内容，禁止写入");
        }
        return value;
    }

    private static String sanitizeSummary(String raw) {
        String value = raw == null ? "" : raw;
        if (value.length() > MAX_SUMMARY_CHARS) {
            throw new IllegalArgumentException("summary 长度超过 " + MAX_SUMMARY_CHARS);
        }
        return value;
    }

    /**
     * 递归剥离 taskState 中的 trusted key。命中任意 forbidden key 立即抛 IllegalArgumentException，
     * 不静默忽略（保守策略：fail-closed）。
     */
    private static Map<String, Object> sanitizeTaskStateMap(Map<String, Object> raw) {
        if (raw == null) {
            return new LinkedHashMap<>();
        }
        Map<String, Object> cleaned = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : raw.entrySet()) {
            String key = entry.getKey();
            if (FORBIDDEN_TASK_STATE_KEYS.contains(key)) {
                throw new IllegalArgumentException(
                        "taskState 包含 trusted 字段，禁止写入: " + key);
            }
            cleaned.put(key, scrubValue(entry.getValue()));
        }
        return cleaned;
    }

    @SuppressWarnings("unchecked")
    private static Object scrubValue(Object value) {
        if (value instanceof Map<?, ?> map) {
            // 二次校验：嵌套 map 内也可能含 trusted key
            Map<String, Object> cleaned = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                String key = String.valueOf(entry.getKey());
                if (FORBIDDEN_TASK_STATE_KEYS.contains(key)) {
                    throw new IllegalArgumentException(
                            "taskState 嵌套字段包含 trusted key，禁止写入: " + key);
                }
                cleaned.put(key, scrubValue(entry.getValue()));
            }
            return cleaned;
        }
        if (value instanceof List<?> list) {
            List<Object> cleaned = new ArrayList<>(list.size());
            for (Object item : list) {
                cleaned.add(scrubValue(item));
            }
            return cleaned;
        }
        if (value instanceof String text) {
            // Java 独立内容安全边界：与 Python 同 marker 规则，命中整串替换，保持结构。
            return containsSensitiveMarker(text) ? REDACTED : text;
        }
        return value;
    }

    /** 子串扫描（大小写不敏感）：与 Python memory_write_policy 的脱敏规则保持一致。 */
    static boolean containsSensitiveMarker(String value) {
        String lowered = value.toLowerCase(Locale.ROOT);
        for (String marker : SENSITIVE_VALUE_MARKERS) {
            if (lowered.contains(marker)) {
                return true;
            }
        }
        return false;
    }

    private static MemoryWriteException stateConflict(String userId, String conversationId,
                                                      TaskStatus target) {
        return new MemoryWriteException(HttpStatus.CONFLICT, "MEMORY_STATE_CONFLICT",
                "Memory 状态机拒绝转换: target=" + target.name()
                        + " userId=" + userId + " conversationId=" + conversationId);
    }

    private String serializeTaskState(Map<String, Object> sanitized) {
        try {
            String json = objectMapper.writeValueAsString(sanitized);
            if (json.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > MAX_TASK_STATE_JSON_BYTES) {
                throw new IllegalArgumentException(
                        "task_state_json 字节数超过 " + MAX_TASK_STATE_JSON_BYTES);
            }
            return json;
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("taskState 序列化失败: " + e.getMessage(), e);
        }
    }
}
