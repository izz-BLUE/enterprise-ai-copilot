package com.fantuan.copilot.service.agent;

import org.springframework.stereotype.Component;

import java.util.Objects;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 保护同一运行时线程的完整 Java Agent 生命周期。
 *
 * 当前单实例部署使用进程内集合；多实例场景需要未来引入分布式 lease/lock。
 */
@Component
public class AgentRuntimeThreadExecutionGuard {
    private final Set<String> activeThreadIds = ConcurrentHashMap.newKeySet();

    /** 原子尝试占用 thread；相同 thread 已在执行时立即返回 false。 */
    public boolean tryAcquire(String runtimeThreadId) {
        return activeThreadIds.add(Objects.requireNonNull(runtimeThreadId,
                "runtimeThreadId 不能为空"));
    }

    /** 释放 thread；调用方不得在其他生命周期重新占用后再次释放同一 key。 */
    public void release(String runtimeThreadId) {
        if (runtimeThreadId != null) {
            activeThreadIds.remove(runtimeThreadId);
        }
    }
}
