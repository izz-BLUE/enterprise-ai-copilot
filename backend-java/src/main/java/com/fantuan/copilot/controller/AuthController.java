package com.fantuan.copilot.controller;

import com.fantuan.copilot.auth.AuthException;
import com.fantuan.copilot.auth.AuthService;
import com.fantuan.copilot.auth.AuthenticatedUser;
import com.fantuan.copilot.dto.auth.AuthUserResponse;
import com.fantuan.copilot.dto.auth.LoginRequest;
import com.fantuan.copilot.dto.auth.LoginResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/auth")
public class AuthController {
    private final AuthService authService;

    public AuthController(AuthService authService) {
        this.authService = authService;
    }

    @PostMapping("/login")
    public ResponseEntity<LoginResponse> login(@Valid @RequestBody LoginRequest request) {
        return ResponseEntity.ok().cacheControl(org.springframework.http.CacheControl.noStore())
                .body(authService.login(request));
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
}
