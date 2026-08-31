package com.fantuan.copilot.dto.admin;

import java.util.List;

/** 模拟 OA 管理列表响应，列表上限由 Mock OA 和 Java 网关共同约束。 */
public record MockOaApprovalListResponse(List<MockOaApprovalView> items, int count) {
    public MockOaApprovalListResponse {
        items = items == null ? List.of() : List.copyOf(items);
    }
}
