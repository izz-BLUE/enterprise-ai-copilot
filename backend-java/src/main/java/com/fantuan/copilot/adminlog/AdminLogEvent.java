package com.fantuan.copilot.adminlog;

import java.time.Instant;

/**
 * 管理员运行日志事件。
 *
 * 同时作为内存缓冲对象与接口返回对象；所有字段在写入前已完成脱敏。
 * 不允许在 message / statusFrom / statusTo 中放入 request body、token、
 * 聊天文本、taskState、scope、summary 等敏感数据。
 */
public record AdminLogEvent(
        String id,
        Instant timestamp,
        String level,
        String category,
        String event,
        String traceId,
        String service,
        String userRef,
        String actionRef,
        String statusFrom,
        String statusTo,
        Long durationMs,
        String message,
        String httpMethod,
        String path,
        Integer httpStatus) {

    public static final String LEVEL_INFO = "INFO";
    public static final String LEVEL_WARN = "WARN";
    public static final String LEVEL_ERROR = "ERROR";

    public static final String CATEGORY_REQUEST = "REQUEST";
    public static final String CATEGORY_AGENT = "AGENT";
    public static final String CATEGORY_BUSINESS_ACTION = "BUSINESS_ACTION";
    public static final String CATEGORY_MEMORY = "MEMORY";
    public static final String CATEGORY_SECURITY = "SECURITY";
    public static final String CATEGORY_SYSTEM = "SYSTEM";

    public static final String SERVICE = "backend-java";
}