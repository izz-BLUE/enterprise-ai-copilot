package com.fantuan.copilot.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/**
 * 前端 → Java 的会话请求体。
 *
 * P0 阶段新增可选字段 conversationId：用于在同一 trusted user_id 下区分不同会话。
 * 它不是可信身份，只是客户端提供的 UUID/字符串分组；服务端若未收到则生成独立随机 UUID。
 *
 * 旧客户端完全可以不传此字段 —— 服务端会兜底生成，不会破坏现有调用。
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ChatRequest(
        @NotBlank(message = "message 不能为空")
        @Size(max = 2000, message = "message 长度不能超过 2000 字符")
        String message,

        /**
         * 可选。客户端提供的会话 ID。建议是 UUID；服务端只做长度与字符集校验，不解析其语义。
         * 为 null 或缺失时，服务端生成独立 UUID v4。
         */
        @Size(max = 64, message = "conversationId 长度不能超过 64 字符")
        @Pattern(regexp = "[A-Za-z0-9._\\-:]*",
                message = "conversationId 只能包含字母、数字、点、下划线、连字符、冒号")
        String conversationId
) {
    public ChatRequest(String message) {
        this(message, null);
    }
}
