package com.fantuan.copilot.auth;

import com.fantuan.copilot.dto.auth.LoginRequest;
import com.fantuan.copilot.dto.auth.LoginResponse;
import com.fantuan.copilot.dto.auth.AuthUserResponse;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.authentication.DisabledException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.stereotype.Service;

@Service
public class AuthService {
    private final AuthenticationManager authenticationManager;
    private final AppUserRepository users;
    private final JwtTokenService tokens;

    public AuthService(AuthenticationManager authenticationManager,
                       AppUserRepository users,
                       JwtTokenService tokens) {
        this.authenticationManager = authenticationManager;
        this.users = users;
        this.tokens = tokens;
    }

    public LoginResponse login(LoginRequest request) {
        try {
            authenticationManager.authenticate(UsernamePasswordAuthenticationToken.unauthenticated(
                    request.username().trim(), request.password()));
        } catch (BadCredentialsException | DisabledException exception) {
            throw new AuthException(HttpStatus.UNAUTHORIZED, "AUTHENTICATION_FAILED",
                    "用户名或密码错误。");
        }

        AppUser user = users.findByUsername(request.username().trim())
                .filter(AppUser::enabled)
                .orElseThrow(() -> new AuthException(HttpStatus.UNAUTHORIZED,
                        "AUTHENTICATION_FAILED", "用户名或密码错误。"));
        JwtTokenService.IssuedToken token = tokens.issue(user);
        return new LoginResponse(token.value(), "Bearer", token.expiresIn(),
                AuthUserResponse.from(user));
    }
}
