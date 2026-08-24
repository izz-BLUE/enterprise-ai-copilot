package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.action.ActionDecisionRequest;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.identity.IdentityContext;
import com.fantuan.copilot.service.action.BusinessActionService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/agent/actions")
public class BusinessActionController {
    private final BusinessActionService service;
    private final IdentityContext identityContext;

    @Autowired
    public BusinessActionController(BusinessActionService service,
                                    IdentityContext identityContext) {
        this.service = service;
        this.identityContext = identityContext;
    }

    @PostMapping("/{actionId}/confirm")
    public ResponseEntity<ActionExecutionResponse> confirm(
            @PathVariable String actionId,
            @RequestHeader(value = "X-Admin-Token", required = false) String adminToken,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
            @Valid @RequestBody ActionDecisionRequest request,
            HttpServletRequest servletRequest) {
        String traceId = (String) servletRequest.getAttribute("traceId");
        var identity = identityContext.require(servletRequest).asDemoIdentity();
        return noStore(service.confirm(actionId, request.confirmationNonce(), idempotencyKey,
                adminToken, traceId, identity));
    }

    @PostMapping("/{actionId}/cancel")
    public ResponseEntity<ActionExecutionResponse> cancel(
            @PathVariable String actionId,
            @RequestHeader(value = "X-Admin-Token", required = false) String adminToken,
            @Valid @RequestBody ActionDecisionRequest request,
            HttpServletRequest servletRequest) {
        String traceId = (String) servletRequest.getAttribute("traceId");
        var identity = identityContext.require(servletRequest).asDemoIdentity();
        return noStore(service.cancel(actionId, request.confirmationNonce(), adminToken, traceId,
                identity));
    }

    private ResponseEntity<ActionExecutionResponse> noStore(ActionExecutionResponse response) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(response);
    }
}
