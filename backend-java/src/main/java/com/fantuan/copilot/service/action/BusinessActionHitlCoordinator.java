package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.InternalAgentChatRequest;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.dto.action.HitlResumePayload;
import com.fantuan.copilot.dto.action.HitlWaitMarker;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.agent.AgentMemoryCoordinator;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import com.fantuan.copilot.model.task.TaskExecution;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.model.task.TaskType;
import com.fantuan.copilot.service.task.TaskRuntimeException;
import com.fantuan.copilot.service.task.TaskRuntimeService;
import org.springframework.beans.factory.annotation.Autowired;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.util.Optional;

/**
 * 协调 Java 的动作权威与 Python checkpoint continuation。
 * BusinessActionService 仍是状态转换和副作用的唯一 owner；本类只负责可信路由、
 * guard 所有权，以及 Java 事务提交后的尽力而为图 reconciliation。
 */
@Service
public class BusinessActionHitlCoordinator {
    private static final Logger log = LoggerFactory.getLogger(BusinessActionHitlCoordinator.class);
    private static final String CANCELLED_MESSAGE = "申请草稿已取消。";
    private static final String EXPIRED_MESSAGE = "该申请草稿已过期，请重新生成。";
    private static final String REJECTED_MESSAGE = "申请未能完成，已安全拒绝。";

    private final BusinessActionService actionService;
    private final PendingActionRepository actions;
    private final PythonAgentGateway pythonAgentGateway;
    private final AgentRuntimeThreadIdService threadIdService;
    private final AgentRuntimeThreadExecutionGuard threadGuard;
    private final AdminAccessService adminAccessService;
    private final ExpenseExternalApprovalCoordinator externalApprovalCoordinator;
    private final ExpenseConfirmRevalidationService expenseRevalidation;
    private final TaskRuntimeService taskRuntimeService;
    private final AgentMemoryCoordinator memoryCoordinator;

    @Autowired
    public BusinessActionHitlCoordinator(BusinessActionService actionService,
                                         PendingActionRepository actions,
                                         PythonAgentGateway pythonAgentGateway,
                                         AgentRuntimeThreadIdService threadIdService,
                                         AgentRuntimeThreadExecutionGuard threadGuard,
                                         AdminAccessService adminAccessService,
                                         ExpenseExternalApprovalCoordinator externalApprovalCoordinator,
                                         ExpenseConfirmRevalidationService expenseRevalidation,
                                         TaskRuntimeService taskRuntimeService,
                                         AiTaskMemoryService memoryService) {
        this.actionService = actionService;
        this.actions = actions;
        this.pythonAgentGateway = pythonAgentGateway;
        this.threadIdService = threadIdService;
        this.threadGuard = threadGuard;
        this.adminAccessService = adminAccessService;
        this.externalApprovalCoordinator = externalApprovalCoordinator;
        this.expenseRevalidation = expenseRevalidation;
        this.taskRuntimeService = taskRuntimeService;
        this.memoryCoordinator = memoryService == null ? null : new AgentMemoryCoordinator(memoryService);
    }

    /** 兼容不执行报销重新校验的聚焦测试的构造方法。 */
    public BusinessActionHitlCoordinator(BusinessActionService actionService,
                                         PendingActionRepository actions,
                                         PythonAgentGateway pythonAgentGateway,
                                         AgentRuntimeThreadIdService threadIdService,
                                         AgentRuntimeThreadExecutionGuard threadGuard,
                                         AdminAccessService adminAccessService,
                                         ExpenseExternalApprovalCoordinator externalApprovalCoordinator) {
        this(actionService, actions, pythonAgentGateway, threadIdService, threadGuard,
                adminAccessService, externalApprovalCoordinator, null, null, null);
    }

    /** 兼容执行报销重新校验的测试的构造方法。 */
    public BusinessActionHitlCoordinator(BusinessActionService actionService,
                                         PendingActionRepository actions,
                                         PythonAgentGateway pythonAgentGateway,
                                         AgentRuntimeThreadIdService threadIdService,
                                         AgentRuntimeThreadExecutionGuard threadGuard,
                                         AdminAccessService adminAccessService,
                                         ExpenseExternalApprovalCoordinator externalApprovalCoordinator,
                                         ExpenseConfirmRevalidationService expenseRevalidation) {
        this(actionService, actions, pythonAgentGateway, threadIdService, threadGuard,
                adminAccessService, externalApprovalCoordinator, expenseRevalidation,
                null, null);
    }

    /**
     * 在普通 Chat 到达 Python 前收口已过期的审批。
     * 调用方必须已经拥有解析后的（user、conversation）线程对应的 Java
     * runtime-thread guard；本方法不会围绕 resume 和后续 Chat 调用获取或释放该 guard。
     */
    public boolean reconcileExpiredBeforeChat(String traceId, String presentedToken,
                                              VerifiedIdentity identity,
                                              String conversationId) {
        if (identity == null || identity.userId() == null || identity.userId().isBlank()
                || conversationId == null || conversationId.isBlank()) {
            return true;
        }
        Optional<PendingAction> expired = actionService.reconcileExpiredForChat(
                identity.userId(), conversationId, traceId);
        if (expired == null || expired.isEmpty()) {
            return true;
        }
        PendingAction action = expired.get();
        if (action.hitlWaitId() == null || action.agentExecutionId() == null
                || action.ownerUserId() == null || action.conversationId() == null) {
            return true;
        }
        TaskExecution task = taskRuntimeService == null ? null
                : taskRuntimeService.findByActionId(action.actionId()).orElse(null);
        if (task != null) {
            // 过期处理和 TaskExecution 终态化由 BusinessActionService 一起提交。
            // Python 只负责尽力而为的 checkpoint 清理，不能阻断下一任务。
            tryResume(action, identity, presentedToken, traceId);
            startNextTask(task, identity, presentedToken, traceId);
            return true;
        }
        return tryResume(action, identity, presentedToken, traceId);
    }

    /** Python 已将 interrupt 持久化到 checkpoint 后注册 wait。 */
    public PendingActionView registerWait(BusinessActionProposal proposal,
                                          HitlWaitMarker wait,
                                          String originTraceId,
                                          String presentedToken,
                                          VerifiedIdentity identity,
                                          String conversationId) {
        return registerWait(proposal, wait, originTraceId, presentedToken, identity,
                conversationId, null);
    }

    public PendingActionView registerWait(BusinessActionProposal proposal,
                                          HitlWaitMarker wait,
                                          String originTraceId,
                                          String presentedToken,
                                          VerifiedIdentity identity,
                                          String conversationId,
                                          String taskId) {
        validateWaitAndProposal(proposal, wait);
        try {
            PendingActionView view = actionService.createHitlPending(
                    proposal, originTraceId, presentedToken, identity, conversationId,
                    wait.executionId(), wait.waitId());

            // HTTP 响应丢失前，Java commit 可能已经成功。
            // 收口该终态记录，不创建第二个 action。
            Optional<PendingAction> terminal = actions.findByHitlWaitId(wait.waitId());
            if (terminal != null && terminal.isPresent() && isTerminal(terminal.get().status())) {
                tryResume(terminal.get(), identity, presentedToken, originTraceId);
            }
            return view;
        } catch (ActionException exception) {
            if (isDeterministicRegistrationRejection(exception)) {
                // 此路径不存在 PendingAction。只关闭 Java 已有的 ACTIVE task memory，
                // 并拒绝持久化 wait；绝不为了关联而伪造 action 记录。
                // Memory 是 Graph 终态化之前由 Java 负责的生命周期前置步骤。
                // 如果该步骤失败，保持 checkpoint 等待，以便重试按 Memory -> Graph 顺序执行。
                actionService.abandonMemoryAfterHitlRejection(
                        identity, conversationId);
                PendingActionView successorPendingAction = null;
                if (taskId != null && taskRuntimeService != null) {
                    taskRuntimeService.markTerminal(taskId, TaskExecutionStatus.FAILED);
                    tryResumeRejected(wait, identity, conversationId,
                            presentedToken, originTraceId, taskId);
                    successorPendingAction = startNextTask(
                            taskRuntimeService.findByTaskId(taskId).orElse(null),
                            identity, presentedToken, originTraceId);
                } else {
                    tryResumeRejected(wait, identity, conversationId,
                            presentedToken, originTraceId);
                    throw exception;
                }
                throw new TaskRuntimeRegistrationRejectionException(
                        exception, successorPendingAction);
            }
            throw exception;
        }
    }

    public ActionExecutionResponse confirm(String actionId, String confirmationNonce,
                                           String idempotencyKey, String presentedToken,
                                           String traceId, VerifiedIdentity identity) {
        PendingAction routing = resolveRouting(actionId, presentedToken, identity);
        String guardKey = guardKey(routing, identity);
        acquireOrBusy(guardKey);
        boolean guardReleased = false;
        try {
            try {
                // guard 之前读取的记录只用于识别 runtime thread。
                // 所有确认决策使用的 status，都必须在线程获得 guard 后重新读取。
                routing = refreshRouting(actionId, identity);
                revalidateExpenseOutsideTransaction(routing, actionId, confirmationNonce,
                        presentedToken, traceId, identity);
                ActionExecutionResponse response = actionService.confirm(
                        actionId, confirmationNonce, idempotencyKey, presentedToken,
                        traceId, identity);
                ReconcileResult reconciliation = reconcileAfterCommittedAction(
                        actionId, routing, response, identity, presentedToken, traceId, guardKey);
                guardReleased = reconciliation.guardReleased();
                return reconciliation.nextPendingAction() == null ? response
                        : response.withNextPendingAction(reconciliation.nextPendingAction());
            } catch (ActionExpiredAfterUpdateException exception) {
                reconcileTerminal(actionId, routing, identity, presentedToken, traceId,
                        HitlResumePayload.HitlDecision.EXPIRED, ActionStatus.EXPIRED,
                        EXPIRED_MESSAGE, null);
                throw exception;
            } catch (ActionStaleException exception) {
                reconcileTerminal(actionId, routing, identity, presentedToken, traceId,
                        HitlResumePayload.HitlDecision.REJECTED, ActionStatus.FAILED,
                        REJECTED_MESSAGE, null);
                throw exception;
            } catch (ActionException exception) {
                if ("ACTION_EXPIRED".equals(exception.errorCode())) {
                    reconcileTerminal(actionId, routing, identity, presentedToken, traceId,
                            HitlResumePayload.HitlDecision.EXPIRED, ActionStatus.EXPIRED,
                            EXPIRED_MESSAGE, null);
                } else if (routing.status() == ActionStatus.FAILED
                        && "ACTION_STATE_CONFLICT".equals(exception.errorCode())) {
                    // 第一次 rejection resume 不可用期间，stale commit 可能已经成功。
                    // 重复 confirm 只重试该确定性 continuation；Java 终态仍是权威状态。
                    reconcileTerminal(actionId, routing, identity, presentedToken, traceId,
                            HitlResumePayload.HitlDecision.REJECTED, ActionStatus.FAILED,
                            REJECTED_MESSAGE, null);
                }
                throw exception;
            }
        } finally {
            if (!guardReleased) {
                threadGuard.release(guardKey);
            }
        }
    }

    private void revalidateExpenseOutsideTransaction(PendingAction routing, String actionId,
                                                      String confirmationNonce,
                                                      String presentedToken, String traceId,
                                                      VerifiedIdentity identity) {
        if (expenseRevalidation == null
                || routing.actionType() != BusinessActionType.EXPENSE_CLAIM
                || routing.status() != ActionStatus.PENDING_CONFIRMATION) {
            return;
        }
        String staleCode = expenseRevalidation.revalidate(routing, traceId);
        if (staleCode != null) {
            // service 在独立的短事务中锁定并重新检查 action，并在提交后抛出
            // 抛出 ActionStaleException。
            actionService.failStaleConfirmation(actionId, confirmationNonce,
                    presentedToken, traceId, identity, staleCode);
            // 兼容 mock service：stale 结果绝不能继续进入普通执行路径。
            throw new ActionStaleException(actionId);
        }
    }

    private PendingAction refreshRouting(String actionId, VerifiedIdentity identity) {
        PendingAction action = actions.find(actionId).orElseThrow(() -> new ActionException(
                HttpStatus.NOT_FOUND, "ACTION_NOT_FOUND", "未找到申请草稿。", null, null));
        if (action.ownerUserId() != null
                && !action.ownerUserId().equals(identity.userId())) {
            throw new ActionException(HttpStatus.NOT_FOUND, "ACTION_NOT_FOUND",
                    "未找到申请草稿。", null, null);
        }
        return action;
    }

    public ActionExecutionResponse cancel(String actionId, String confirmationNonce,
                                          String presentedToken, String traceId,
                                          VerifiedIdentity identity) {
        PendingAction routing = resolveRouting(actionId, presentedToken, identity);
        String guardKey = guardKey(routing, identity);
        acquireOrBusy(guardKey);
        boolean guardReleased = false;
        try {
            try {
                ActionExecutionResponse response = actionService.cancel(
                        actionId, confirmationNonce, presentedToken, traceId,
                        identity);
                ReconcileResult reconciliation = reconcileAfterCommittedAction(
                        actionId, routing, response, identity, presentedToken, traceId, guardKey);
                guardReleased = reconciliation.guardReleased();
                return reconciliation.nextPendingAction() == null ? response
                        : response.withNextPendingAction(reconciliation.nextPendingAction());
            } catch (ActionExpiredAfterUpdateException exception) {
                reconcileTerminal(actionId, routing, identity, presentedToken, traceId,
                        HitlResumePayload.HitlDecision.EXPIRED, ActionStatus.EXPIRED,
                        EXPIRED_MESSAGE, null);
                throw exception;
            } catch (ActionException exception) {
                if ("ACTION_EXPIRED".equals(exception.errorCode())) {
                    reconcileTerminal(actionId, routing, identity, presentedToken, traceId,
                            HitlResumePayload.HitlDecision.EXPIRED, ActionStatus.EXPIRED,
                            EXPIRED_MESSAGE, null);
                }
                throw exception;
            }
        } finally {
            if (!guardReleased) {
                threadGuard.release(guardKey);
            }
        }
    }

    private void validateWaitAndProposal(BusinessActionProposal proposal, HitlWaitMarker wait) {
        if (proposal == null || wait == null || !wait.structurallyValid()
                || (proposal.actionType() != null && proposal.actionType() != wait.actionType())) {
            throw new ActionException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST",
                    "HITL wait 或业务 Proposal 无效。", null, null);
        }
    }

    private PendingAction resolveRouting(String actionId, String presentedToken,
                                         VerifiedIdentity identity) {
        if (identity == null || identity.userId() == null || identity.userId().isBlank()) {
            throw new ActionException(HttpStatus.FORBIDDEN, "IDENTITY_REQUIRED",
                    "当前身份不可用。", null, null);
        }
        actionService.authorizeForAction(presentedToken, identity);
        PendingAction action = actions.find(actionId).orElseThrow(() -> new ActionException(
                HttpStatus.NOT_FOUND, "ACTION_NOT_FOUND", "未找到申请草稿。", null, null));
        if (action.ownerUserId() != null
                && !action.ownerUserId().equals(identity.userId())) {
            throw new ActionException(HttpStatus.NOT_FOUND, "ACTION_NOT_FOUND",
                    "未找到申请草稿。", null, null);
        }
        return action;
    }

    private String guardKey(PendingAction action, VerifiedIdentity identity) {
        if (action.ownerUserId() != null && action.conversationId() != null) {
            // owner 已在上方根据当前 VerifiedIdentity 完成检查。
            return threadIdService.generate(identity.userId(), action.conversationId());
        }
        // Legacy 记录没有不可变的 conversation 关联。它们仍使用这个 singleton guard，
        // 但不会与 Chat thread 冲突。
        return "legacy-action:" + action.actionId();
    }

    private void acquireOrBusy(String guardKey) {
        if (!threadGuard.tryAcquire(guardKey)) {
            throw new ActionException(HttpStatus.TOO_MANY_REQUESTS, "ACTION_THREAD_BUSY",
                    "当前会话正在处理中，请稍后重试。", null, null);
        }
    }

    private ReconcileResult reconcileAfterCommittedAction(String actionId, PendingAction routing,
                                                          ActionExecutionResponse response,
                                                          VerifiedIdentity identity,
                                                          String presentedToken,
                                                          String traceId,
                                                          String guardKey) {
        PendingAction action = actions.find(actionId).orElse(routing);
        TaskExecution task = taskRuntimeService == null ? null
                : taskRuntimeService.findByActionId(actionId).orElse(null);
        if (task != null) {
            return reconcileTaskRuntimeAction(task, action, response, identity,
                    presentedToken, traceId, guardKey);
        }
        if (response.status() == ActionStatus.SUCCEEDED) {
            if (action.actionType() == com.fantuan.copilot.model.action.BusinessActionType.EXPENSE_CLAIM) {
                return new ReconcileResult(reconcileConfirmedExpense(action, response, identity,
                        presentedToken, traceId, guardKey), null);
            }
            reconcileTerminal(actionId, action, identity, presentedToken, traceId,
                    HitlResumePayload.HitlDecision.CONFIRMED, ActionStatus.SUCCEEDED,
                    response.message(), response.requestId());
        } else if (response.status() == ActionStatus.CANCELLED) {
            reconcileTerminal(actionId, action, identity, presentedToken, traceId,
                    HitlResumePayload.HitlDecision.CANCELLED, ActionStatus.CANCELLED,
                    response.message(), null);
        }
        return new ReconcileResult(false, null);
    }

    private ReconcileResult reconcileTaskRuntimeAction(TaskExecution task,
                                                       PendingAction action,
                                                       ActionExecutionResponse response,
                                                       VerifiedIdentity identity,
                                                       String presentedToken,
                                                       String traceId,
                                                       String guardKey) {
        if (task == null || action == null || response == null) {
            return new ReconcileResult(false, null);
        }
        if (task.status().isTerminal()) {
            // 进入该 continuation 前，Java 事务可能已经提交当前任务的终态。
            // 该状态不构成停止队列的理由：还要确定性地收口下一可运行任务。
            // WAITING_EXTERNAL 有意由下方 Expense 分支处理，以便先关闭其任务专属的
            // Python 图。
            return new ReconcileResult(false, startNextTask(task, identity,
                    presentedToken, traceId));
        }

        if (response.status() == ActionStatus.SUCCEEDED
                && action.actionType() == BusinessActionType.EXPENSE_CLAIM) {
            try {
                PythonAgentResponse resumed = postTaskRuntimeResume(action, identity,
                        presentedToken, traceId, new HitlResumePayload(
                                1, action.hitlWaitId(), action.agentExecutionId(),
                                HitlResumePayload.HitlDecision.CONFIRMED, action.actionId(),
                                action.actionType(), ActionStatus.SUCCEEDED, response.requestId(),
                                canonicalMessage(action, ActionStatus.SUCCEEDED, response.message())));
                if (!isSuccessful(resumed) || resumed.externalWait() != null) {
                    throw new TaskRuntimeException(
                            "TASK_RUNTIME Expense HITL resume 不得进入 WAITING_EXTERNAL。 ");
                }
            } catch (RuntimeException exception) {
                log.warn("[{}] TASK_RUNTIME_GRAPH_CLEANUP_PENDING actionIdPrefix={} errorType={}",
                        traceId, BusinessActionService.auditRef(action.actionId()),
                        exception.getClass().getSimpleName());
            }
            try {
                // Java 事务已经将该任务置为 WAITING_EXTERNAL。
                // 外部 handoff 和下一任务启动独立于 Python checkpoint 清理。
                if (!externalApprovalCoordinator.registerTaskRuntimeAndDispatch(
                        action, response, traceId)) {
                    log.warn("[{}] TASK_RUNTIME_EXTERNAL_HANDOFF_PENDING actionIdPrefix={}",
                            traceId, BusinessActionService.auditRef(action.actionId()));
                }
            } catch (RuntimeException exception) {
                log.warn("[{}] TASK_RUNTIME_EXTERNAL_HANDOFF_PENDING actionIdPrefix={} errorType={}",
                        traceId, BusinessActionService.auditRef(action.actionId()),
                        exception.getClass().getSimpleName());
            }
            return new ReconcileResult(false, startNextTask(task, identity,
                    presentedToken, traceId));
        }

        ActionStatus terminalActionStatus = response.status();
        HitlResumePayload.HitlDecision decision = terminalActionStatus == ActionStatus.CANCELLED
                ? HitlResumePayload.HitlDecision.CANCELLED
                : terminalActionStatus == ActionStatus.SUCCEEDED
                ? HitlResumePayload.HitlDecision.CONFIRMED : null;
        if (decision == null) {
            return new ReconcileResult(false, null);
        }
        try {
            PythonAgentResponse resumed = postTaskRuntimeResume(action, identity,
                    presentedToken, traceId, new HitlResumePayload(
                            1, action.hitlWaitId(), action.agentExecutionId(), decision,
                            action.actionId(), action.actionType(), terminalActionStatus,
                            response.requestId(), canonicalMessage(action, terminalActionStatus,
                                    response.message())));
            if (!isSuccessful(resumed) || resumed.externalWait() != null) {
                throw new TaskRuntimeException("Task Runtime HITL resume 未正常结束。 ");
            }
        } catch (RuntimeException exception) {
            log.warn("[{}] TASK_RUNTIME_GRAPH_CLEANUP_PENDING actionIdPrefix={} errorType={}",
                    traceId, BusinessActionService.auditRef(action.actionId()),
                    exception.getClass().getSimpleName());
        }
        // Java 事务已经将 action 和 task 置为终态。
        return new ReconcileResult(false, startNextTask(task, identity,
                presentedToken, traceId));
    }

    private PendingActionView startNextTask(TaskExecution current,
                                             VerifiedIdentity identity,
                                             String presentedToken,
                                             String traceId) {
        if (current == null || taskRuntimeService == null) {
            return null;
        }
        Optional<TaskExecution> next = taskRuntimeService.startNextRunnable(current.taskGroupId());
        if (next.isEmpty()) {
            return null;
        }
        TaskExecution task = next.get();
        try {
            PythonAgentResponse response = postTaskRuntimeChat(task, identity,
                    presentedToken, traceId);
            if (response == null || response.externalWait() != null) {
                throw new TaskRuntimeException("Task Runtime 新任务返回了不允许的 external wait。 ");
            }
            if (response.hitlWait() != null && response.actionProposal() == null) {
                throw new TaskRuntimeException("Task Runtime HITL wait 缺少业务 Proposal。 ");
            }
            if (response.hitlWait() != null && !response.hitlWait().structurallyValid()) {
                throw new TaskRuntimeException("Task Runtime HITL wait 上下文无效。 ");
            }
            if (response.actionProposal() != null) {
                if (!taskRuntimeService.matchesTaskType(task,
                        taskType(response.actionProposal().actionType()))
                        || response.hitlWait() == null) {
                    throw new TaskRuntimeException("Task Runtime Proposal 与任务关联不匹配。 ");
                }
                PendingActionView pending = registerWait(response.actionProposal(),
                        response.hitlWait(), traceId, presentedToken, identity,
                        task.conversationId(), task.taskId());
                if (pending == null || !taskRuntimeService.markWaitingUser(
                        task.taskId(), pending.actionId())) {
                    throw new TaskRuntimeException("Task Runtime 下一任务 PendingAction 关联失败。 ");
                }
                if (memoryCoordinator != null) {
                    memoryCoordinator.persistForNextTask(response.memoryProposal(), identity.userId(),
                            task.conversationId(), traceId);
                }
                return pending;
            }
            if (response.missingFields() != null && !response.missingFields().isEmpty()) {
                if (!taskRuntimeService.markWaitingClarification(task.taskId())) {
                    throw new TaskRuntimeException("Task Runtime clarification 状态冲突。 ");
                }
                if (memoryCoordinator != null) {
                    memoryCoordinator.persistForNextTask(response.memoryProposal(), identity.userId(),
                            task.conversationId(), traceId);
                }
                return null;
            }
            if (!taskRuntimeService.markTerminal(task.taskId(), TaskExecutionStatus.FAILED)) {
                throw new TaskRuntimeException("Task Runtime 下一任务未产生可执行结果。 ");
            }
            if (memoryCoordinator != null) {
                memoryCoordinator.abandon(identity.userId(), task.conversationId(), traceId);
            }
            return null;
        } catch (TaskRuntimeRegistrationRejectionException exception) {
            // successor 已经被确定性地置为终态；重新入队会把业务拒绝变成启动
            // 失败，并可能在下一次 Chat 中产生重复 Proposal。
            return exception.successorPendingAction();
        } catch (RuntimeException exception) {
            // 之前的 Java 业务事实已经是权威状态。启动失败时保留该 task 为 PENDING，
            // 使下一次受 guard 保护的 Chat 可以在同一个任务专属线程上确定性重试。
            taskRuntimeService.requeueAfterLaunchFailure(task.taskId());
            log.warn("[{}] TASK_RUNTIME_NEXT_TASK_FAILED taskIdPrefix={} errorType={}",
                    traceId, BusinessActionService.auditRef(task.taskId()),
                    exception.getClass().getSimpleName());
            return null;
        }
    }

    /** 任务在没有 PendingAction 的情况下结束后，继续 Task Runtime 分组。 */
    public PendingActionView startNextTaskAfterTerminal(TaskExecution current,
                                                        VerifiedIdentity identity,
                                                        String presentedToken,
                                                        String traceId) {
        return startNextTask(current, identity, presentedToken, traceId);
    }

    private PythonAgentResponse postTaskRuntimeChat(TaskExecution task,
                                                    VerifiedIdentity identity,
                                                    String presentedToken,
                                                    String traceId) {
        String runtimeThreadId = threadIdService.generate(identity.userId(),
                task.conversationId(), task.taskId());
        HttpHeaders headers = taskRuntimeHeaders(identity, task.conversationId(),
                runtimeThreadId, task.taskId(), presentedToken);
        return pythonAgentGateway.post("/agent/langgraph/chat",
                new InternalAgentChatRequest(task.taskText(), null, task.taskId(),
                        task.clarificationContext()), headers,
                PythonAgentResponse.class, traceId);
    }

    private PythonAgentResponse postTaskRuntimeResume(PendingAction action,
                                                       VerifiedIdentity identity,
                                                       String presentedToken,
                                                       String traceId,
                                                       HitlResumePayload payload) {
        String runtimeThreadId = threadIdService.generate(action.ownerUserId(),
                action.conversationId(),
                taskRuntimeService.findByActionId(action.actionId())
                        .map(TaskExecution::taskId).orElseThrow(
                                () -> new TaskRuntimeException("Task Runtime 关联任务不存在。")));
        HttpHeaders headers = taskRuntimeHeaders(identity, action.conversationId(),
                runtimeThreadId,
                taskRuntimeService.findByActionId(action.actionId())
                        .map(TaskExecution::taskId).orElseThrow(), presentedToken);
        return pythonAgentGateway.post("/agent/langgraph/hitl/resume", payload, headers,
                PythonAgentResponse.class, traceId);
    }

    private HttpHeaders taskRuntimeHeaders(VerifiedIdentity identity,
                                           String conversationId,
                                           String runtimeThreadId,
                                           String taskId,
                                           String presentedToken) {
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Agent-Thread-Id", runtimeThreadId);
        headers.set("X-Agent-Execution-Mode", "TASK_RUNTIME");
        headers.set("X-Agent-Task-Id", taskId);
        headers.set("X-Employee-Id", identity.employeeId());
        headers.set("X-Conversation-Id", conversationId);
        headers.set("X-Allow-Eval", Boolean.toString(adminAccessService.isAdminIdentity(identity)));
        headers.set("X-Allow-Business-Actions",
                Boolean.toString(actionService.isAllowed(presentedToken, identity)));
        headers.set("X-Business-Date", actionService.businessDate().toString());
        return headers;
    }

    private TaskType taskType(BusinessActionType actionType) {
        return switch (actionType) {
            case ANNUAL_LEAVE_REQUEST -> TaskType.LEAVE_REQUEST;
            case EXPENSE_CLAIM -> TaskType.EXPENSE_CLAIM;
        };
    }

    private boolean isSuccessful(PythonAgentResponse response) {
        return response != null && !Boolean.FALSE.equals(response.success());
    }

    private record ReconcileResult(boolean guardReleased,
                                   PendingActionView nextPendingAction) {
    }

    private boolean reconcileConfirmedExpense(PendingAction action, ActionExecutionResponse response,
                                              VerifiedIdentity identity, String presentedToken,
                                              String traceId, String guardKey) {
        if (action.hitlWaitId() == null || action.agentExecutionId() == null
                || action.ownerUserId() == null || action.conversationId() == null) {
            return false;
        }
        HitlResumePayload payload = new HitlResumePayload(1, action.hitlWaitId(),
                action.agentExecutionId(), HitlResumePayload.HitlDecision.CONFIRMED,
                action.actionId(), action.actionType(), ActionStatus.SUCCEEDED,
                response.requestId(), canonicalMessage(action, ActionStatus.SUCCEEDED, response.message()));
        boolean guardReleased = false;
        try {
            PythonAgentResponse pythonResponse = postResume(action.ownerUserId(), identity,
                    action.conversationId(), presentedToken, traceId, payload);
            // HITL resume 已返回，图现在持久化等待 OA。
            // 在 external resume coordinator 自行获取 guard 前，将同一 runtime thread
            // 边界交给它。
            threadGuard.release(guardKey);
            guardReleased = true;
            externalApprovalCoordinator.registerExternalWaitAndDispatch(action, response,
                    pythonResponse.externalWait(), traceId);
        } catch (RuntimeException exception) {
            log.warn("[{}] HITL_CONTINUATION_PENDING actionIdPrefix={} errorType={}", traceId,
                    BusinessActionService.auditRef(action.actionId()), exception.getClass().getSimpleName());
        }
        return guardReleased;
    }

    private void reconcileTerminal(String actionId, PendingAction routing,
                                   VerifiedIdentity identity, String presentedToken,
                                   String traceId, HitlResumePayload.HitlDecision decision,
                                   ActionStatus status, String message, String requestId) {
        PendingAction action = actions.find(actionId).orElse(routing);
        reconcileTerminal(action, identity, presentedToken, traceId,
                decision, status, message, requestId);
    }

    private void reconcileTerminal(PendingAction action, VerifiedIdentity identity,
                                   String presentedToken, String traceId,
                                   HitlResumePayload.HitlDecision decision,
                                   ActionStatus status, String message, String requestId) {
        if (action == null || action.hitlWaitId() == null
                || action.agentExecutionId() == null
                || action.ownerUserId() == null || action.conversationId() == null) {
            return;
        }
        HitlResumePayload payload = new HitlResumePayload(
                1, action.hitlWaitId(), action.agentExecutionId(), decision,
                action.actionId(), action.actionType(), status, requestId,
                canonicalMessage(action, status, message));
        TaskExecution task = taskRuntimeService == null ? null
                : taskRuntimeService.findByActionId(action.actionId()).orElse(null);
        if (task != null) {
            tryResume(action, identity, presentedToken, traceId, payload);
            // action service 已在同一事务中提交对应的 TaskExecution 终态。
            // resume 失败不能阻断确定性的下一任务推进。
            startNextTask(task, identity, presentedToken, traceId);
            return;
        }
        tryResume(action, identity, presentedToken, traceId, payload);
    }

    private void tryResumeRejected(HitlWaitMarker wait, VerifiedIdentity identity,
                                   String conversationId, String presentedToken,
                                   String traceId) {
        tryResumeRejected(wait, identity, conversationId, presentedToken, traceId, null);
    }

    private void tryResumeRejected(HitlWaitMarker wait, VerifiedIdentity identity,
                                   String conversationId, String presentedToken,
                                   String traceId, String taskId) {
        if (identity == null || identity.userId() == null || identity.employeeId() == null
                || conversationId == null || conversationId.isBlank()) {
            return;
        }
        HitlResumePayload payload = new HitlResumePayload(
                1, wait.waitId(), wait.executionId(), HitlResumePayload.HitlDecision.REJECTED,
                null, wait.actionType(), ActionStatus.FAILED, null, REJECTED_MESSAGE);
        try {
            if (taskId == null) {
                postResume(identity.userId(), identity, conversationId,
                        presentedToken, traceId, payload);
            } else {
                postTaskRuntimeWaitResume(identity.userId(), identity, conversationId,
                        taskId, presentedToken, traceId, payload);
            }
        } catch (RuntimeException exception) {
            log.warn("[{}] HITL_REJECTION_CONTINUATION_PENDING waitIdPrefix={} errorType={}",
                    traceId, BusinessActionService.auditRef(wait.waitId()),
                    exception.getClass().getSimpleName());
        }
    }

    private PythonAgentResponse postTaskRuntimeWaitResume(String ownerUserId,
                                                          VerifiedIdentity identity,
                                                          String conversationId,
                                                          String taskId,
                                                          String presentedToken,
                                                          String traceId,
                                                          HitlResumePayload payload) {
        String runtimeThreadId = threadIdService.generate(ownerUserId, conversationId, taskId);
        HttpHeaders headers = taskRuntimeHeaders(identity, conversationId,
                runtimeThreadId, taskId, presentedToken);
        return pythonAgentGateway.post("/agent/langgraph/hitl/resume", payload, headers,
                PythonAgentResponse.class, traceId);
    }

    private boolean tryResume(PendingAction action, VerifiedIdentity identity,
                              String presentedToken, String traceId) {
        HitlResumePayload.HitlDecision decision = switch (action.status()) {
            case SUCCEEDED -> HitlResumePayload.HitlDecision.CONFIRMED;
            case CANCELLED -> HitlResumePayload.HitlDecision.CANCELLED;
            case EXPIRED -> HitlResumePayload.HitlDecision.EXPIRED;
            case FAILED -> HitlResumePayload.HitlDecision.REJECTED;
            default -> null;
        };
        if (decision == null) {
            return true;
        }
        ActionStatus status = action.status();
        return tryResume(action, identity, presentedToken, traceId,
                new HitlResumePayload(1, action.hitlWaitId(), action.agentExecutionId(),
                        decision, action.actionId(), action.actionType(), status,
                        action.requestId(), canonicalMessage(action, status, null)));
    }

    private boolean tryResume(PendingAction action, VerifiedIdentity identity,
                               String presentedToken, String traceId,
                               HitlResumePayload payload) {
        try {
            PythonAgentResponse response = postResume(action.ownerUserId(), identity,
                    action.conversationId(), presentedToken, traceId, payload);
            return isSuccessful(response) && response.externalWait() == null;
        } catch (RuntimeException exception) {
            log.warn("[{}] HITL_CONTINUATION_PENDING actionIdPrefix={} errorType={}",
                    traceId, BusinessActionService.auditRef(action.actionId()),
                    exception.getClass().getSimpleName());
            return false;
        }
    }

    private PythonAgentResponse postResume(String ownerUserId, VerifiedIdentity identity,
                                           String conversationId,
                                           String presentedToken, String traceId,
                                           HitlResumePayload payload) {
        TaskExecution task = taskRuntimeService == null || payload.actionId() == null ? null
                : taskRuntimeService.findByActionId(payload.actionId()).orElse(null);
        String runtimeThreadId = task == null
                ? threadIdService.generate(ownerUserId, conversationId)
                : threadIdService.generate(ownerUserId, conversationId, task.taskId());
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Agent-Thread-Id", runtimeThreadId);
        headers.set("X-Employee-Id", identity.employeeId());
        headers.set("X-Allow-Eval", Boolean.toString(adminAccessService.isAdminIdentity(identity)));
        headers.set("X-Allow-Business-Actions",
                Boolean.toString(actionService.isAllowed(presentedToken, identity)));
        LocalDate businessDate = actionService.businessDate();
        headers.set("X-Business-Date", businessDate.toString());
        if (task != null) {
            headers.set("X-Agent-Execution-Mode", "TASK_RUNTIME");
            headers.set("X-Agent-Task-Id", task.taskId());
        }
        headers.set("X-Conversation-Id", conversationId);
        return pythonAgentGateway.post("/agent/langgraph/hitl/resume", payload, headers,
                PythonAgentResponse.class, traceId);
    }

    private static String canonicalMessage(PendingAction action, ActionStatus status,
                                           String fallback) {
        return switch (status) {
            case SUCCEEDED -> boundedMessage(firstNonBlank(action.executionMessage(), fallback));
            case CANCELLED -> boundedMessage(firstNonBlank(action.executionMessage(), CANCELLED_MESSAGE));
            case EXPIRED -> EXPIRED_MESSAGE;
            case FAILED -> REJECTED_MESSAGE;
            default -> boundedMessage(fallback);
        };
    }

    private static String firstNonBlank(String preferred, String fallback) {
        return preferred == null || preferred.isBlank() ? fallback : preferred;
    }

    private static boolean isTerminal(ActionStatus status) {
        return status == ActionStatus.SUCCEEDED || status == ActionStatus.CANCELLED
                || status == ActionStatus.EXPIRED || status == ActionStatus.FAILED;
    }

    /**
     * 只有明确且确定性的 Proposal 校验失败才可以关闭持久化 wait。
     * 新业务错误码必须经过审查后有意加入；仅凭 HTTP status 永远不足以完成分类。
     */
    private static boolean isDeterministicRegistrationRejection(ActionException exception) {
        if (exception == null || exception.errorCode() == null) {
            return false;
        }
        return switch (exception.errorCode()) {
            case "BUSINESS_RULE_VIOLATION",
                    "EXPENSE_ITEMS_REQUIRED",
                    "EXPENSE_AMOUNT_INVALID",
                    "EXPENSE_INVOICES_REQUIRED" -> true;
            default -> false;
        };
    }

    private static String boundedMessage(String message) {
        String value = message == null || message.isBlank() ? REJECTED_MESSAGE : message;
        return value.length() <= 255 ? value : value.substring(0, 255);
    }
}
