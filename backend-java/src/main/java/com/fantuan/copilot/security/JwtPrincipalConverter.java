package com.fantuan.copilot.security;

import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.auth.AuthenticatedUser;
import org.springframework.core.convert.converter.Converter;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;

import java.util.List;

public class JwtPrincipalConverter implements Converter<Jwt, UsernamePasswordAuthenticationToken> {
    @Override
    public UsernamePasswordAuthenticationToken convert(Jwt jwt) {
        String roleClaim = jwt.getClaimAsString("role");
        AuthRole role;
        try {
            role = AuthRole.valueOf(roleClaim);
        } catch (RuntimeException exception) {
            throw new IllegalArgumentException("Invalid JWT role");
        }
        AuthenticatedUser user = new AuthenticatedUser(
                jwt.getSubject(),
                jwt.getClaimAsString("username"),
                jwt.getClaimAsString("employee_id"),
                jwt.getClaimAsString("display_name"),
                role,
                true);
        return UsernamePasswordAuthenticationToken.authenticated(
                user, null, List.of(new SimpleGrantedAuthority("ROLE_" + role.name())));
    }
}
