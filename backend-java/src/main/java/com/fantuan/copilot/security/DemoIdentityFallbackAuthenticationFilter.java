package com.fantuan.copilot.security;

import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.demo.DemoIdentityService;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.HttpHeaders;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.util.matcher.AntPathRequestMatcher;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;

/**
 * Compatibility authentication only. It runs after Bearer authentication, so an
 * invalid Bearer token is rejected before this fallback can run.
 */
public class DemoIdentityFallbackAuthenticationFilter extends OncePerRequestFilter {
    private final DemoIdentityService identities;
    private final List<AntPathRequestMatcher> matchers = List.of(
            new AntPathRequestMatcher("/api/agent/langgraph/chat"),
            new AntPathRequestMatcher("/api/agent/actions/**"));

    public DemoIdentityFallbackAuthenticationFilter(DemoIdentityService identities) {
        this.identities = identities;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return matchers.stream().noneMatch(matcher -> matcher.matches(request));
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain)
            throws ServletException, IOException {
        if (SecurityContextHolder.getContext().getAuthentication() == null
                && identities.isEnabled()
                && !hasAuthorizationHeader(request)) {
            String presentedUserId = request.getHeader("X-Demo-User-Id");
            DemoIdentity identity = identities.find(presentedUserId).orElse(null);
            if (identity != null) {
                String authority = "ROLE_" + identity.role().name();
                SecurityContextHolder.getContext().setAuthentication(
                        UsernamePasswordAuthenticationToken.authenticated(
                                identity, null, List.of(new SimpleGrantedAuthority(authority))));
            }
        }
        filterChain.doFilter(request, response);
    }

    private boolean hasAuthorizationHeader(HttpServletRequest request) {
        String authorization = request.getHeader(HttpHeaders.AUTHORIZATION);
        return authorization != null && !authorization.isBlank();
    }
}
