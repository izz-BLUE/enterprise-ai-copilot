package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.BusinessActionType;

import java.util.List;
import java.util.Optional;
import java.util.function.Function;
import java.util.stream.Collectors;

import org.springframework.stereotype.Component;

/**
 * BusinessActionHandler Registry（V2 §十七）。
 *
 * 由 Spring 注入所有 BusinessActionHandler 实现（List），按 supports() 构建
 * Map<BusinessActionType, BusinessActionHandler>。BusinessActionService 通过
 * proposal.actionType() → handlerRegistry.handlerFor(actionType) 调度。
 */
@Component
public class BusinessActionHandlerRegistry {

    private final java.util.Map<BusinessActionType, BusinessActionHandler> handlers;

    public BusinessActionHandlerRegistry(List<BusinessActionHandler> handlerBeans) {
        this.handlers = handlerBeans.stream().collect(
                Collectors.toUnmodifiableMap(BusinessActionHandler::supports, Function.identity()));
    }

    public Optional<BusinessActionHandler> handlerFor(BusinessActionType actionType) {
        return Optional.ofNullable(handlers.get(actionType));
    }
}
