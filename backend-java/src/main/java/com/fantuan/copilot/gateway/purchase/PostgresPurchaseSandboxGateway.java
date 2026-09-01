package com.fantuan.copilot.gateway.purchase;

import com.fantuan.copilot.model.action.PurchaseRequest;
import com.fantuan.copilot.model.action.PurchaseRequestStatus;
import com.fantuan.copilot.repository.action.PurchaseRequestRepository;
import com.fantuan.copilot.service.action.BusinessActionProperties;
import org.springframework.stereotype.Component;

import java.time.YearMonth;
import java.time.format.DateTimeFormatter;

/** P4-3 最小采购 sandbox：确认后写入 purchase_request，不调用外部采购系统。 */
@Component
public class PostgresPurchaseSandboxGateway implements PurchaseExecutionGateway {
    private static final DateTimeFormatter REQUEST_MONTH = DateTimeFormatter.ofPattern("yyyyMM");

    private final PurchaseRequestRepository requests;
    private final BusinessActionProperties properties;

    public PostgresPurchaseSandboxGateway(PurchaseRequestRepository requests,
                                          BusinessActionProperties properties) {
        this.requests = requests;
        this.properties = properties;
    }

    @Override
    public PurchaseExecutionResult submit(PurchaseSubmission submission) {
        long number = requests.nextNumber();
        String requestId = "PUR-" + YearMonth.from(
                submission.submittedAt().atZone(properties.zoneId())).format(REQUEST_MONTH)
                + "-" + String.format("%06d", number);
        requests.save(new PurchaseRequest(
                requestId, submission.sourceActionId(), submission.ownerUserId(),
                submission.employeeId(), submission.itemName(), submission.requestedBudget(),
                submission.justification(), PurchaseRequestStatus.SUBMITTED,
                submission.submittedAt()));
        return new PurchaseExecutionResult(requestId, submission.submittedAt());
    }
}
