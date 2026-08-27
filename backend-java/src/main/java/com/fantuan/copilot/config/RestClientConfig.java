package com.fantuan.copilot.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.apache.hc.client5.http.config.RequestConfig;
import org.apache.hc.client5.http.config.ConnectionConfig;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManagerBuilder;
import org.apache.hc.core5.util.Timeout;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

@Configuration
public class RestClientConfig {

    @Value("${python.agent.connect-timeout:3000}")
    private int connectTimeout;

    @Value("${python.agent.read-timeout:50000}")
    private int readTimeout;

    @Value("${python.agent.http-max-connections:6}")
    private int maxConnections;

    @Bean
    @Primary
    public RestTemplate restTemplate() {
        var connectionConfig = ConnectionConfig.custom()
                .setConnectTimeout(Timeout.ofMilliseconds(connectTimeout))
                .build();
        var connections = PoolingHttpClientConnectionManagerBuilder.create()
                .setMaxConnTotal(maxConnections)
                .setMaxConnPerRoute(maxConnections)
                .setDefaultConnectionConfig(connectionConfig)
                .build();
        RequestConfig requestConfig = RequestConfig.custom()
                .setResponseTimeout(Timeout.ofMilliseconds(readTimeout))
                .build();
        CloseableHttpClient client = HttpClients.custom()
                .setConnectionManager(connections)
                .setDefaultRequestConfig(requestConfig)
                .evictExpiredConnections()
                .build();
        HttpComponentsClientHttpRequestFactory factory =
                new HttpComponentsClientHttpRequestFactory(client);
        return new RestTemplate(factory);
    }
}
