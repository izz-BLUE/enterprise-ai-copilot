package com.fantuan.copilot.gateway.expense;

import com.fantuan.copilot.model.action.ExpenseClaim;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.util.Map;

/** HTTP adapter for the independent Mock OA service. */
@Component
public class MockOaExpenseApprovalGateway implements ExpenseApprovalGateway {
    public static final String PROVIDER = "MOCK_OA";

    private final RestTemplate restTemplate;
    private final boolean enabled;
    private final String baseUrl;

    public MockOaExpenseApprovalGateway(
            @Qualifier("mockOaRestTemplate") RestTemplate restTemplate,
            @Value("${external.approval.mock-oa.enabled:false}") boolean enabled,
            @Value("${external.approval.mock-oa.base-url:}") String baseUrl) {
        this.restTemplate = restTemplate;
        this.enabled = enabled;
        this.baseUrl = baseUrl == null ? "" : baseUrl.replaceAll("/+$", "");
    }

    @Override
    public ExternalApprovalSubmissionResult submit(ExpenseClaim claim) {
        if (!enabled || baseUrl.isBlank()) {
            throw new ExternalApprovalSubmissionException("Mock OA submission is disabled");
        }
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.set("Idempotency-Key", "expense:" + claim.expenseId());
        Map<String, Object> body = Map.of(
                "expenseId", claim.expenseId(),
                "employeeId", claim.employeeId(),
                "tripId", claim.tripId(),
                "costCenter", claim.costCenter(),
                "claimedAmount", claim.claimedAmount(),
                "reimbursableAmount", claim.reimbursableAmount());
        try {
            ExternalApprovalSubmissionResult response = restTemplate.postForObject(
                    baseUrl + "/api/expense-approvals", new HttpEntity<>(body, headers),
                    ExternalApprovalSubmissionResult.class);
            if (response == null || response.requestId() == null || response.requestId().isBlank()
                    || !response.isSupportedStatus()) {
                throw new ExternalApprovalSubmissionException("Mock OA returned invalid submission result");
            }
            return response;
        } catch (RestClientException exception) {
            throw new ExternalApprovalSubmissionException("Mock OA submission failed", exception);
        }
    }

    @Override
    public ExternalApprovalSubmissionResult getStatus(String externalRequestId) {
        if (!enabled || baseUrl.isBlank()) {
            throw new ExternalApprovalSubmissionException("Mock OA status query is disabled");
        }
        if (externalRequestId == null || externalRequestId.isBlank()) {
            throw new ExternalApprovalSubmissionException("Mock OA status query requires request id");
        }
        try {
            ExternalApprovalSubmissionResult response = restTemplate.getForObject(
                    baseUrl + "/api/expense-approvals/{requestId}",
                    ExternalApprovalSubmissionResult.class, externalRequestId);
            if (response == null || response.requestId() == null || response.requestId().isBlank()
                    || !externalRequestId.equals(response.requestId()) || !response.isSupportedStatus()) {
                throw new ExternalApprovalSubmissionException("Mock OA returned invalid status result");
            }
            return response;
        } catch (RestClientException exception) {
            throw new ExternalApprovalSubmissionException("Mock OA status query failed", exception);
        }
    }
}
