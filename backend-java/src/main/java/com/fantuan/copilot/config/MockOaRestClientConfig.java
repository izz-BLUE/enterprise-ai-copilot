package com.fantuan.copilot.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

/** 外部 Mock OA 边界的隔离超时策略。 */
@Configuration
public class MockOaRestClientConfig {
    @Bean("mockOaRestTemplate")
    public RestTemplate mockOaRestTemplate(
            @Value("${external.approval.mock-oa.connect-timeout:3000}") int connectTimeout,
            @Value("${external.approval.mock-oa.read-timeout:5000}") int readTimeout) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(connectTimeout);
        factory.setReadTimeout(readTimeout);
        return new RestTemplate(factory);
    }
}
