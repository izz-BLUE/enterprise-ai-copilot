package com.fantuan.copilot.controller;

import com.fantuan.copilot.service.demo.DemoIdentityProperties;
import com.fantuan.copilot.service.demo.DemoIdentityService;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.containsString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class DemoIdentityControllerTest {
    @Test
    void enabledEndpointReturnsOnlyPublicIdentityFieldsWithoutCaching() throws Exception {
        MockMvc mvc = mvc(true);
        mvc.perform(get("/api/demo/identities").requestAttr("traceId", "identity-trace"))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.identities.length()").value(3))
                .andExpect(jsonPath("$.identities[0].userId").value("DEMO-001"))
                .andExpect(jsonPath("$.identities[0].displayName").value("Demo User"))
                .andExpect(jsonPath("$.identities[0].role").value("EMPLOYEE"))
                .andExpect(content().string(not(containsString("employeeId"))))
                .andExpect(content().string(not(containsString("balance"))))
                .andExpect(content().string(not(containsString("nonce"))));
    }

    @Test
    void disabledEndpointReturnsSafe503() throws Exception {
        mvc(false).perform(get("/api/demo/identities").requestAttr("traceId", "identity-trace"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.errorCode").value("DEMO_IDENTITY_DISABLED"));
    }

    private MockMvc mvc(boolean enabled) {
        DemoIdentityProperties properties = new DemoIdentityProperties();
        properties.setEnabled(enabled);
        DemoIdentityService service = new DemoIdentityService(properties);
        return MockMvcBuilders.standaloneSetup(new DemoIdentityController(service))
                .setControllerAdvice(new BusinessActionExceptionHandler())
                .build();
    }
}
