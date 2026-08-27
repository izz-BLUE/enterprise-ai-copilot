package com.fantuan.copilot.gateway.python;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestTemplate;

import java.net.SocketTimeoutException;
import java.util.Map;

/**
 * Java → Python 的统一传输边界：负责基础 URL、可信 trace header 和并发许可。
 * 鉴权、业务权限、业务异常映射仍由上层 Controller/Service 负责。
 */
@Component
public class PythonAgentGateway {
    private static final Logger log = LoggerFactory.getLogger(PythonAgentGateway.class);

    private final RestTemplate restTemplate;
    private final PythonAgentBulkhead bulkhead;
    private final String baseUrl;

    public PythonAgentGateway(
            RestTemplate restTemplate,
            PythonAgentBulkhead bulkhead,
            @Value("${python.agent.base-url:http://localhost:8000}") String baseUrl) {
        this.restTemplate = restTemplate;
        this.bulkhead = bulkhead;
        this.baseUrl = baseUrl;
    }

    public <T> T post(String path, Object body, HttpHeaders additionalHeaders,
                      Class<T> responseType, String traceId) {
        PythonAgentBulkhead.Permit permit = bulkhead.tryAcquire(traceId);
        if (permit == null) {
            throw new PythonAgentBusyException();
        }

        try (permit) {
            HttpHeaders headers = new HttpHeaders();
            if (additionalHeaders != null) {
                headers.putAll(additionalHeaders);
            }
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set("X-Trace-Id", traceId);
            String url = baseUrl + path;
            log.info("[{}] 调用 Python: {}", traceId, url);
            T response;
            try {
                response = restTemplate.postForEntity(
                        url, new HttpEntity<>(body, headers), responseType).getBody();
            } catch (HttpStatusCodeException exception) {
                if (exception.getStatusCode().value() == 429) {
                    throw new PythonAgentBusyException();
                }
                if (exception.getStatusCode().value() == 409) {
                    throw new PythonAgentTransportException(
                            HttpStatus.CONFLICT,
                            "Python Agent recovery conflict",
                            exception);
                }
                throw new PythonAgentTransportException(
                        HttpStatus.BAD_GATEWAY,
                        "Python Agent returned HTTP " + exception.getStatusCode().value(),
                        exception);
            } catch (ResourceAccessException exception) {
                HttpStatus status = causedByTimeout(exception)
                        ? HttpStatus.GATEWAY_TIMEOUT
                        : HttpStatus.BAD_GATEWAY;
                throw new PythonAgentTransportException(
                        status, "Python Agent transport unavailable", exception);
            }
            if (response == null) {
                throw new PythonAgentTransportException(
                        HttpStatus.BAD_GATEWAY, "Python Agent returned an empty response", null);
            }
            log.info("[{}] Python 响应成功", traceId);
            return response;
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> health() {
        return probe("/agent/health", "health");
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> readiness() {
        return probe("/agent/ready", "readiness");
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> probe(String path, String probeName) {
        try {
            Map<String, Object> response = restTemplate.getForObject(
                    baseUrl + path, Map.class);
            if (response == null) {
                throw new PythonAgentTransportException(
                        HttpStatus.SERVICE_UNAVAILABLE,
                        "Python " + probeName + " response is empty", null);
            }
            return response;
        } catch (HttpStatusCodeException | ResourceAccessException exception) {
            throw new PythonAgentTransportException(
                    HttpStatus.SERVICE_UNAVAILABLE,
                    "Python Agent " + probeName + " probe failed", exception);
        }
    }

    private static boolean causedByTimeout(Throwable throwable) {
        Throwable current = throwable;
        while (current != null) {
            if (current instanceof SocketTimeoutException) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }
}
