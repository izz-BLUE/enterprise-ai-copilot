package com.fantuan.copilot.auth;

import java.util.Optional;

public interface AppUserRepository {
    Optional<AppUser> findByUsername(String username);

    Optional<AppUser> findByUserId(String userId);

    void insert(AppUser user);

    void updatePasswordHash(String userId, String passwordHash);
}
