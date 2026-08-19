package com.fantuan.copilot.identity;

import com.fantuan.copilot.auth.AuthenticatedUser;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.demo.DemoIdentityService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.AnonymousAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;

/**
 * Resolves the one trusted identity for the current request.
 * A verified Spring Security principal always wins over Demo fallback headers.
 */
@Service
public class IdentityContext {
    private final DemoIdentityService demoIdentityService;

    public IdentityContext(DemoIdentityService demoIdentityService) {
        this.demoIdentityService = demoIdentityService;
    }

    public VerifiedIdentity require(HttpServletRequest request) {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication != null && authentication.isAuthenticated()
                && !(authentication instanceof AnonymousAuthenticationToken)) {
            Object principal = authentication.getPrincipal();
            if (principal instanceof AuthenticatedUser user) {
                return VerifiedIdentity.from(user);
            }
            if (principal instanceof DemoIdentity identity) {
                return VerifiedIdentity.from(identity);
            }
        }

        String authorization = request.getHeader("Authorization");
        if (authorization != null && !authorization.isBlank()) {
            throw authenticationRequired();
        }

        String demoUserId = request.getHeader("X-Demo-User-Id");
        // In a real web request Spring Security rejects anonymous access first.
        // Calling the legacy service here preserves its exact error semantics for
        // standalone controller tests and the explicit compatibility path.
        return VerifiedIdentity.from(demoIdentityService.requireIdentity(demoUserId));
    }

    private ActionException authenticationRequired() {
        return new ActionException(HttpStatus.UNAUTHORIZED,
                "AUTHENTICATION_REQUIRED", "请先登录。", null, null);
    }
}
