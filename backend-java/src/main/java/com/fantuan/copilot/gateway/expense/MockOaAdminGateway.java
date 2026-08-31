package com.fantuan.copilot.gateway.expense;

import com.fantuan.copilot.dto.admin.MockOaApprovalActionResponse;
import com.fantuan.copilot.dto.admin.MockOaApprovalListResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.util.UriComponentsBuilder;

import java.net.SocketTimeoutException;
import java.net.URI;
import java.util.Set;

/** 模拟 OA 管理操作的独立 HTTP 网关，和员工报销提交网关分离。 */
@Component
public class MockOaAdminGateway {
    private static final Logger log = LoggerFactory.getLogger(MockOaAdminGateway.class);
    private static final int MAX_LIST_LIMIT = 100;
    private static final Set<String> APPROVAL_STATUSES = Set.of("PENDING", "APPROVED", "REJECTED");

    private final RestTemplate restTemplate;
    private final boolean enabled;
    private final String baseUrl;

    public MockOaAdminGateway(
            @Qualifier("mockOaRestTemplate") RestTemplate restTemplate,
            @Value("${external.approval.mock-oa.enabled:false}") boolean enabled,
            @Value("${external.approval.mock-oa.base-url:}") String baseUrl) {
        this.restTemplate = restTemplate;
        this.enabled = enabled;
        this.baseUrl = baseUrl == null ? "" : baseUrl.replaceAll("/+$", "");
    }

    public MockOaApprovalListResponse list(String requestedStatus) {
        ensureAvailable();
        String normalizedStatus = normalizeStatus(requestedStatus);
        UriComponentsBuilder builder = UriComponentsBuilder
                .fromHttpUrl(baseUrl + "/api/admin/expense-approvals")
                .queryParam("limit", MAX_LIST_LIMIT);
        if (normalizedStatus != null) {
            builder.queryParam("status", normalizedStatus);
        }
        try {
            MockOaApprovalListResponse response = restTemplate.getForObject(
                    builder.build().encode().toUri(), MockOaApprovalListResponse.class);
            if (response == null || response.items() == null
                    || response.items().size() > MAX_LIST_LIMIT) {
                throw invalidResponse();
            }
            return response;
        } catch (MockOaAdminException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw mapDownstreamFailure("list", exception);
        }
    }

    public MockOaApprovalActionResponse decide(String requestId, String decision) {
        ensureAvailable();
        if (requestId == null || requestId.isBlank()) {
            throw new MockOaAdminException(HttpStatus.BAD_REQUEST.value(),
                    "MOCK_OA_INVALID_REQUEST", "审批请求编号不能为空。");
        }
        String normalizedDecision = decision == null ? "" : decision.trim().toUpperCase();
        if (!Set.of("APPROVED", "REJECTED").contains(normalizedDecision)) {
            throw new MockOaAdminException(HttpStatus.BAD_REQUEST.value(),
                    "MOCK_OA_INVALID_DECISION", "审批结果无效。");
        }
        URI uri = UriComponentsBuilder
                .fromHttpUrl(baseUrl + "/api/admin/expense-approvals")
                .pathSegment(requestId, normalizedDecision.equals("APPROVED") ? "approve" : "reject")
                .build().encode().toUri();
        try {
            MockOaApprovalActionResponse response = restTemplate.postForObject(
                    uri, null, MockOaApprovalActionResponse.class);
            if (response == null || response.requestId() == null || response.requestId().isBlank()
                    || !requestId.equals(response.requestId())
                    || !APPROVAL_STATUSES.contains(response.status())) {
                throw invalidResponse();
            }
            return response;
        } catch (MockOaAdminException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw mapDownstreamFailure(normalizedDecision.toLowerCase(), exception);
        }
    }

    private String normalizeStatus(String requestedStatus) {
        if (requestedStatus == null || requestedStatus.isBlank()
                || "ALL".equalsIgnoreCase(requestedStatus.trim())) {
            return null;
        }
        String normalized = requestedStatus.trim().toUpperCase();
        if (!APPROVAL_STATUSES.contains(normalized)) {
            throw new MockOaAdminException(HttpStatus.BAD_REQUEST.value(),
                    "MOCK_OA_INVALID_STATUS", "状态筛选值无效。");
        }
        return normalized;
    }

    private void ensureAvailable() {
        if (!enabled || baseUrl.isBlank()) {
            throw new MockOaAdminException(HttpStatus.SERVICE_UNAVAILABLE.value(),
                    "MOCK_OA_DISABLED", "模拟 OA 当前未启用。");
        }
    }

    private MockOaAdminException invalidResponse() {
        return new MockOaAdminException(HttpStatus.BAD_GATEWAY.value(),
                "MOCK_OA_INVALID_RESPONSE", "模拟 OA 返回的数据无效。");
    }

    private MockOaAdminException mapDownstreamFailure(String operation, RestClientException exception) {
        if (exception instanceof HttpStatusCodeException responseException) {
            int status = responseException.getStatusCode().value();
            if (status == HttpStatus.NOT_FOUND.value()) {
                return new MockOaAdminException(HttpStatus.NOT_FOUND.value(),
                        "MOCK_OA_APPROVAL_NOT_FOUND", "审批记录不存在。");
            }
            if (status == HttpStatus.CONFLICT.value()) {
                return new MockOaAdminException(HttpStatus.CONFLICT.value(),
                        "MOCK_OA_STATE_CONFLICT", "审批状态已发生冲突，请刷新列表后重试。");
            }
            if (status >= 500) {
                return new MockOaAdminException(HttpStatus.BAD_GATEWAY.value(),
                        "MOCK_OA_UNAVAILABLE", "模拟 OA 暂时不可用，请稍后重试。");
            }
            return new MockOaAdminException(HttpStatus.BAD_GATEWAY.value(),
                    "MOCK_OA_REQUEST_FAILED", "模拟 OA 请求未完成。");
        }
        if (exception instanceof ResourceAccessException accessException && isTimeout(accessException)) {
            log.warn("Mock OA 管理请求超时 operation={} errorType={}", operation,
                    typeName(accessException));
            return new MockOaAdminException(HttpStatus.SERVICE_UNAVAILABLE.value(),
                    "MOCK_OA_TIMEOUT", "模拟 OA 请求超时，结果未知，请刷新列表确认状态。");
        }
        log.warn("Mock OA 管理请求失败 operation={} errorType={}", operation, typeName(exception));
        return new MockOaAdminException(HttpStatus.BAD_GATEWAY.value(),
                "MOCK_OA_UNAVAILABLE", "模拟 OA 暂时不可用，请稍后重试。");
    }

    private boolean isTimeout(ResourceAccessException exception) {
        Throwable cause = exception;
        while (cause != null) {
            if (cause instanceof SocketTimeoutException) {
                return true;
            }
            cause = cause.getCause();
        }
        return exception.getMessage() != null
                && exception.getMessage().toLowerCase().contains("timed out");
    }

    private String typeName(Exception exception) {
        return exception.getClass().getSimpleName();
    }
}
