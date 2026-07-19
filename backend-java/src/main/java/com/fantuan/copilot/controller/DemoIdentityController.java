package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.demo.DemoIdentityListResponse;
import com.fantuan.copilot.dto.demo.DemoIdentitySummary;
import com.fantuan.copilot.service.demo.DemoIdentityService;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/demo/identities")
public class DemoIdentityController {
    private final DemoIdentityService identities;

    public DemoIdentityController(DemoIdentityService identities) {
        this.identities = identities;
    }

    @GetMapping
    public ResponseEntity<DemoIdentityListResponse> list() {
        var response = new DemoIdentityListResponse(identities.listEnabled().stream()
                .map(DemoIdentitySummary::from)
                .toList());
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(response);
    }
}
