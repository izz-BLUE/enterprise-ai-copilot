package com.fantuan.copilot.controller;

import com.fantuan.copilot.auth.AuthException;
import com.fantuan.copilot.auth.AuthProperties;
import com.fantuan.copilot.auth.AuthService;
import com.fantuan.copilot.auth.AuthenticatedUser;
import com.fantuan.copilot.dto.auth.AuthUserResponse;
import com.fantuan.copilot.dto.auth.LoginRequest;
import com.fantuan.copilot.dto.auth.LoginResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.http.ResponseCookie;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/auth")
public class AuthController {
    public static final String ACCESS_TOKEN_COOKIE = "copilot_access_token";

    private final AuthService authService;
    private final AuthProperties authProperties;

    public AuthController(AuthService authService, AuthProperties authProperties) {
        this.authService = authService;
        this.authProperties = authProperties;
    }

    @PostMapping("/login")
    public ResponseEntity<LoginResponse> login(@Valid @RequestBody LoginRequest request,
                                               HttpServletResponse response) {
        LoginResponse login = authService.login(request);
        response.addHeader("Set-Cookie", accessTokenCookie(login.accessToken(), login.expiresIn()).toString());
        return ResponseEntity.ok().cacheControl(org.springframework.http.CacheControl.noStore())
                .body(login);
    }

    @PostMapping("/logout")
    public ResponseEntity<Void> logout(HttpServletResponse response) {
        response.addHeader("Set-Cookie", accessTokenCookie("", 0).toString());
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/me")
    public ResponseEntity<AuthUserResponse> me(
            @AuthenticationPrincipal AuthenticatedUser authenticatedUser,
            HttpServletRequest request) {
        if (authenticatedUser == null) {
            throw new AuthException(org.springframework.http.HttpStatus.UNAUTHORIZED,
                    "AUTHENTICATION_REQUIRED", "请先登录。");
        }
        return ResponseEntity.ok().cacheControl(org.springframework.http.CacheControl.noStore())
                .body(AuthUserResponse.from(authenticatedUser));
    }

    private ResponseCookie accessTokenCookie(String value, long maxAgeSeconds) {
        return ResponseCookie.from(ACCESS_TOKEN_COOKIE, value)
                .httpOnly(true)
                .secure(authProperties.isCookieSecure())
                .sameSite("Strict")
                .path("/")
                .maxAge(maxAgeSeconds)
                .build();
    }
}
