package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.ChatResponse;
import com.fantuan.copilot.gateway.python.PythonAgentBusyException;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.gateway.python.PythonAgentTransportException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
public class ChatController {

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);

    private final PythonAgentGateway pythonAgentGateway;

    public ChatController(PythonAgentGateway pythonAgentGateway) {
        this.pythonAgentGateway = pythonAgentGateway;
    }

    @PostMapping("/api/chat")
    public ResponseEntity<ChatResponse> chat(@Valid @RequestBody ChatRequest request,
                                             HttpServletRequest httpRequest) {
        String traceId = (String) httpRequest.getAttribute("traceId");
        log.info("[{}] 收到普通 RAG 请求: messageLength={}",
                traceId, request.message().length());

        try {
            ChatResponse response = pythonAgentGateway.post(
                    "/agent/chat", request, null, ChatResponse.class, traceId);
            return ResponseEntity.ok(response);
        } catch (PythonAgentBusyException exception) {
            return busy(traceId);
        } catch (PythonAgentTransportException exception) {
            log.error("[{}] Python 传输失败: status={}",
                    traceId, exception.responseStatus());
            return ResponseEntity.status(exception.responseStatus()).body(new ChatResponse(
                    "当前 AI 服务暂时不可用，请稍后重试。",
                    "unknown",
                    traceId,
                    false,
                    List.of()
            ));
        } catch (Exception e) {
            log.error("[{}] 调用 Python 发生未知异常", traceId, e);
            return ResponseEntity.status(HttpStatus.BAD_GATEWAY).body(new ChatResponse(
                    "当前 AI 服务暂时不可用，请稍后重试。",
                    "unknown",
                    traceId,
                    false,
                    List.of()
            ));
        }
    }

    private ResponseEntity<ChatResponse> busy(String traceId) {
        ChatResponse response = new ChatResponse(
                "当前请求较多，请稍后重试。",
                "unknown",
                traceId,
                false,
                List.of()
        );
        return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                .header(HttpHeaders.RETRY_AFTER, "1")
                .body(response);
    }
}
