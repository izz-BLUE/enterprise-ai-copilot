package com.fantuan.copilot.identity;

import com.fantuan.copilot.auth.AuthenticatedUser;
import com.fantuan.copilot.service.action.ActionException;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.AnonymousAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;

/**
 * Resolves the one trusted identity for the current request from the verified
 * Spring Security principal.
 */
@Service
public class IdentityContext {
    public VerifiedIdentity require(HttpServletRequest request) {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication != null && authentication.isAuthenticated()
                && !(authentication instanceof AnonymousAuthenticationToken)) {
            Object principal = authentication.getPrincipal();
            if (principal instanceof AuthenticatedUser user) {
                return VerifiedIdentity.from(user);
            }
        }
        throw authenticationRequired();
    }

    private ActionException authenticationRequired() {
        return new ActionException(HttpStatus.UNAUTHORIZED,
                "AUTHENTICATION_REQUIRED", "请先登录。", null, null);
    }
}
