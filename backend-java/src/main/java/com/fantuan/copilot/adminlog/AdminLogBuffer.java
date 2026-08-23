package com.fantuan.copilot.adminlog;

import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/**
 * 进程内管理员日志环形缓冲区。
 *
 * 设计：ArrayDeque + synchronized 写锁。容量 500，超出时淘汰最旧。
 * 不引入复杂事件体系、配置框架或第三方依赖；服务重启即清空。
 */
@Component
public class AdminLogBuffer {

    public static final int DEFAULT_CAPACITY = 500;
    public static final int DEFAULT_LIMIT = 50;
    public static final int MAX_LIMIT = 100;

    private static final Set<String> ALLOWED_LEVELS = Set.of(
            AdminLogEvent.LEVEL_INFO,
            AdminLogEvent.LEVEL_WARN,
            AdminLogEvent.LEVEL_ERROR);

    private static final Set<String> ALLOWED_CATEGORIES = Set.of(
            AdminLogEvent.CATEGORY_REQUEST,
            AdminLogEvent.CATEGORY_AGENT,
            AdminLogEvent.CATEGORY_BUSINESS_ACTION,
            AdminLogEvent.CATEGORY_MEMORY,
            AdminLogEvent.CATEGORY_SECURITY,
            AdminLogEvent.CATEGORY_SYSTEM);

    private final int capacity;
    private final ArrayDeque<AdminLogEvent> deque = new ArrayDeque<>();

    public AdminLogBuffer() {
        this(DEFAULT_CAPACITY);
    }

    AdminLogBuffer(int capacity) {
        if (capacity <= 0) {
            throw new IllegalArgumentException("capacity must be positive");
        }
        this.capacity = capacity;
    }

    public synchronized void record(AdminLogEvent event) {
        if (event == null) {
            return;
        }
        while (deque.size() >= capacity) {
            deque.pollFirst();
        }
        deque.addLast(event);
    }

    /**
     * 写入便捷方法。所有字符串字段必须已经脱敏。
     */
    public synchronized void record(String level,
                                   String category,
                                   String event,
                                   String traceId,
                                   String userRef,
                                   String actionRef,
                                   String statusFrom,
                                   String statusTo,
                                   Long durationMs,
                                   String message) {
        record(level, category, event, traceId, userRef, actionRef,
                statusFrom, statusTo, durationMs, message,
                null, null, null);
    }

    /**
     * REQUEST 类别专用：携带 HTTP 方法 / 规范化路径 / 响应状态。
     */
    public synchronized void record(String level,
                                   String category,
                                   String event,
                                   String traceId,
                                   String userRef,
                                   String actionRef,
                                   String statusFrom,
                                   String statusTo,
                                   Long durationMs,
                                   String message,
                                   String httpMethod,
                                   String path,
                                   Integer httpStatus) {
        record(new AdminLogEvent(
                UUID.randomUUID().toString(),
                Instant.now(),
                requireLevel(level),
                requireCategory(category),
                event,
                traceId,
                AdminLogEvent.SERVICE,
                userRef,
                actionRef,
                statusFrom,
                statusTo,
                durationMs,
                message,
                httpMethod,
                path,
                httpStatus));
    }

    public synchronized int size() {
        return deque.size();
    }

    public int capacity() {
        return capacity;
    }

    /**
     * 按时间倒序返回快照。可选 level / category / traceId 过滤。
     * limit 默认 50，最大 100。非法参数抛 IllegalArgumentException，
     * 由 ControllerAdvice 转 400。
     */
    public synchronized List<AdminLogEvent> snapshot(String level,
                                                    String category,
                                                    String traceId,
                                                    Integer limit) {
        int effectiveLimit = limit == null ? DEFAULT_LIMIT : limit;
        if (effectiveLimit < 1 || effectiveLimit > MAX_LIMIT) {
            throw new IllegalArgumentException(
                    "limit must be between 1 and " + MAX_LIMIT);
        }
        String effectiveLevel = normalize(level);
        String effectiveCategory = normalize(category);
        if (effectiveLevel != null && !ALLOWED_LEVELS.contains(effectiveLevel)) {
            throw new IllegalArgumentException(
                    "level must be one of " + ALLOWED_LEVELS);
        }
        if (effectiveCategory != null && !ALLOWED_CATEGORIES.contains(effectiveCategory)) {
            throw new IllegalArgumentException(
                    "category must be one of " + ALLOWED_CATEGORIES);
        }
        String traceNeedle = normalize(traceId);

        List<AdminLogEvent> matched = new ArrayList<>();
        // 倒序遍历：deque 尾是最新
        var it = deque.descendingIterator();
        while (it.hasNext()) {
            AdminLogEvent e = it.next();
            if (effectiveLevel != null && !effectiveLevel.equals(e.level())) continue;
            if (effectiveCategory != null && !effectiveCategory.equals(e.category())) continue;
            if (traceNeedle != null
                    && (e.traceId() == null || !e.traceId().contains(traceNeedle))) continue;
            matched.add(e);
            if (matched.size() >= effectiveLimit) break;
        }
        // 防御性二次排序，确保时间倒序
        matched.sort(Comparator.comparing(AdminLogEvent::timestamp).reversed());
        return matched;
    }

    private static String normalize(String value) {
        if (value == null) return null;
        String trimmed = value.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }

    private static String requireLevel(String level) {
        if (level == null) return AdminLogEvent.LEVEL_INFO;
        if (!ALLOWED_LEVELS.contains(level)) {
            throw new IllegalArgumentException("level must be one of " + ALLOWED_LEVELS);
        }
        return level;
    }

    private static String requireCategory(String category) {
        if (category == null) {
            throw new IllegalArgumentException("category is required");
        }
        if (!ALLOWED_CATEGORIES.contains(category)) {
            throw new IllegalArgumentException("category must be one of " + ALLOWED_CATEGORIES);
        }
        return category;
    }
}