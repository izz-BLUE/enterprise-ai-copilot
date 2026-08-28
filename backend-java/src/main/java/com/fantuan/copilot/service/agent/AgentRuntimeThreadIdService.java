package com.fantuan.copilot.service.agent;

import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Objects;

/**
 * 为 LangGraph 执行快照生成稳定、非业务语义的运行线程标识。
 *
 * 输入只能来自 Java 已验证身份和已解析会话；该标识不是身份、授权令牌、nonce
 * 或业务幂等键，不能替代 Java 业务数据库的权威记录。
 */
@Component
public class AgentRuntimeThreadIdService {
    private static final byte[] DOMAIN =
            "enterprise-ai-copilot:agent-runtime:v1".getBytes(StandardCharsets.UTF_8);

    public String generate(String trustedUserId, String resolvedConversationId) {
        return generate(trustedUserId, resolvedConversationId, null);
    }

    /** Task Runtime namespace: one checkpoint thread per trusted task. */
    public String generate(String trustedUserId, String resolvedConversationId,
                           String taskId) {
        Objects.requireNonNull(trustedUserId, "trustedUserId 不能为空");
        Objects.requireNonNull(resolvedConversationId, "resolvedConversationId 不能为空");
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(DOMAIN);
            digest.update((byte) 0);
            digest.update(trustedUserId.getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(resolvedConversationId.getBytes(StandardCharsets.UTF_8));
            if (taskId != null) {
                digest.update((byte) 0);
                digest.update(taskId.getBytes(StandardCharsets.UTF_8));
            }
            return "rt_" + HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 不可用", exception);
        }
    }
}
