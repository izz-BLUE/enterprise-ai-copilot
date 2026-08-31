package com.fantuan.copilot.concurrency;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 限制可以调用 Python AI 服务的 Java 请求数量。
 *
 * <p>信号量有意采用非公平模式：本 Demo 优先保证吞吐，并保持较短的排队截止时间，
 * 而不是维护长请求队列。</p>
 */
@Component
public class PythonAgentBulkhead {

    private static final Logger log = LoggerFactory.getLogger(PythonAgentBulkhead.class);

    private final int maxConcurrentRequests;
    private final long acquireTimeoutMs;
    private final Semaphore semaphore;
    private final AtomicInteger active = new AtomicInteger();
    private final AtomicLong rejected = new AtomicLong();

    public PythonAgentBulkhead(
            @Value("${python.agent.max-concurrent-requests:3}") int maxConcurrentRequests,
            @Value("${python.agent.acquire-timeout-ms:500}") long acquireTimeoutMs) {
        if (maxConcurrentRequests < 1) {
            throw new IllegalArgumentException("python.agent.max-concurrent-requests must be >= 1");
        }
        if (acquireTimeoutMs < 1) {
            throw new IllegalArgumentException("python.agent.acquire-timeout-ms must be >= 1");
        }

        this.maxConcurrentRequests = maxConcurrentRequests;
        this.acquireTimeoutMs = acquireTimeoutMs;
        this.semaphore = new Semaphore(maxConcurrentRequests);
    }

    /**
     * 返回可关闭的许可；短排队截止时间到期时返回 {@code null}。
     */
    public Permit tryAcquire(String traceId) {
        long started = System.nanoTime();
        final boolean acquired;
        try {
            acquired = semaphore.tryAcquire(acquireTimeoutMs, TimeUnit.MILLISECONDS);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            rejected.incrementAndGet();
            log.warn("[{}] 等待 Python 并发槽时被中断", traceId);
            return null;
        }

        if (!acquired) {
            rejected.incrementAndGet();
            log.warn("[{}] Python 并发已满: active={}, max={}, acquireTimeoutMs={}",
                    traceId, active.get(), maxConcurrentRequests, acquireTimeoutMs);
            return null;
        }

        int currentActive = active.incrementAndGet();
        double waitMs = (System.nanoTime() - started) / 1_000_000.0;
        log.info("[{}] 获得 Python 并发槽: active={}, max={}, queueWaitMs={}",
                traceId, currentActive, maxConcurrentRequests, Math.round(waitMs * 10.0) / 10.0);
        return new Permit(traceId);
    }

    public Map<String, Object> snapshot() {
        return Map.of(
                "maxConcurrent", maxConcurrentRequests,
                "active", active.get(),
                "available", semaphore.availablePermits(),
                "rejected", rejected.get(),
                "queueTimeoutMs", acquireTimeoutMs
        );
    }

    public final class Permit implements AutoCloseable {
        private final String traceId;
        private final AtomicBoolean closed = new AtomicBoolean();

        private Permit(String traceId) {
            this.traceId = traceId;
        }

        @Override
        public void close() {
            if (!closed.compareAndSet(false, true)) {
                return;
            }
            int currentActive = active.decrementAndGet();
            semaphore.release();
            log.info("[{}] 释放 Python 并发槽: active={}, max={}",
                    traceId, currentActive, maxConcurrentRequests);
        }
    }
}
