package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.PurchaseRequest;

import java.util.Optional;

public interface PurchaseRequestRepository {
    long nextNumber();

    void save(PurchaseRequest request);

    int countBySourceActionId(String sourceActionId);

    int size();

    Optional<PurchaseRequest> findByRequestId(String requestId);
}
