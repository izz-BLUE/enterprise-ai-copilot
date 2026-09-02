import { useRef } from 'react'
import {
  BusinessActionApiError,
  cancelBusinessAction,
  confirmBusinessAction,
} from '../services/businessActionApi'
import { AuthExpiredError } from '../services/authApi'
import {
  AUTHORITATIVE_TERMINAL_STATUSES,
  RETRYABLE_ACTION_ERRORS,
  phaseForTerminalStatus,
} from '../domain/actionState'
import { MAX_MESSAGES } from '../services/chatHistoryStorage'
import { isRetryableServerError } from '../services/httpErrorPolicy'

export function isSupportedPendingAction(action) {
  // V2 §二十五：受控业务动作白名单（年假 + 报销）
  return ['ANNUAL_LEAVE_REQUEST', 'EXPENSE_CLAIM'].includes(action?.type)
    && action?.confirmationRequired === true
}

export function initialActionUi(phase = 'pending', error = null) {
  return {
    phase,
    execution: null,
    error,
    retryDecision: null,
  }
}

export default function useBusinessActionFlow({
  messages,
  setMessages,
  accessToken,
  clearConversationId,
  onLogout,
}) {
  const actionSecretsRef = useRef(new Map())
  const idempotencyKeysRef = useRef(new Map())
  const actionLocksRef = useRef(new Set())
  const materializedNextActionIdsRef = useRef(new Set())

  const actionBusy = messages.some(message =>
    message.actionUi?.phase === 'confirming' || message.actionUi?.phase === 'cancelling')

  const hasActiveAction = () => actionBusy || actionLocksRef.current.size > 0

  const resetActionRuntime = () => {
    actionSecretsRef.current.clear()
    idempotencyKeysRef.current.clear()
    actionLocksRef.current.clear()
    materializedNextActionIdsRef.current.clear()
  }

  const registerActionSecret = (messageId, confirmationNonce) => {
    if (typeof confirmationNonce !== 'string' || confirmationNonce.length === 0) return false
    actionSecretsRef.current.set(messageId, { confirmationNonce })
    return true
  }

  const updateActionUi = (messageId, nextUi) => {
    setMessages(prev => prev.map(message => {
      if (message.id !== messageId) return message
      return {
        ...message,
        actionUi: typeof nextUi === 'function' ? nextUi(message.actionUi) : nextUi,
      }
    }))
  }

  const syncPendingActionStatus = (messageId, status) => {
    if (typeof status !== 'string' || status.length === 0) return
    setMessages(prev => prev.map(message => {
      if (message.id !== messageId) return message
      if (!message.result?.pendingAction || typeof message.result.pendingAction !== 'object') {
        return message
      }
      return {
        ...message,
        result: {
          ...message.result,
          pendingAction: { ...message.result.pendingAction, status },
        },
      }
    }))
  }

  const materializeNextPendingAction = (sourceMessage, nextPendingAction) => {
    if (!nextPendingAction || typeof nextPendingAction !== 'object') return false

    const actionId = nextPendingAction.actionId
    if (typeof actionId !== 'string' || actionId.length === 0) return false

    const alreadyVisible = messages.some(message =>
      message.result?.pendingAction?.actionId === actionId)
    if (alreadyVisible || materializedNextActionIdsRef.current.has(actionId)) return true

    const { confirmationNonce, ...publicNextPendingAction } = nextPendingAction
    const nextMessageId = crypto.randomUUID()
    const registered = isSupportedPendingAction(publicNextPendingAction)
      && registerActionSecret(nextMessageId, confirmationNonce)
    const sourceResult = sourceMessage?.result && typeof sourceMessage.result === 'object'
      ? { ...sourceMessage.result }
      : {}

    materializedNextActionIdsRef.current.add(actionId)
    setMessages(previous => [...previous, {
      id: nextMessageId,
      type: 'assistant',
      question: sourceMessage?.question || '',
      result: {
        ...sourceResult,
        pendingAction: publicNextPendingAction,
      },
      resultMode: sourceMessage?.resultMode || 'agent',
      actionUi: registered
        ? initialActionUi('pending')
        : initialActionUi('error', '草稿确认信息不可用，请重新生成草稿。'),
    }].slice(-MAX_MESSAGES))

    return registered
  }

  const executionForUi = execution => {
    if (!execution || typeof execution !== 'object') return execution
    const safeExecution = { ...execution }
    delete safeExecution.nextPendingAction
    return safeExecution
  }

  const applyActionTerminal = (messageId, terminalStatus, opts = {}) => {
    const phase = phaseForTerminalStatus(terminalStatus)
    if (!phase) return
    setMessages(prev => prev.map(message => {
      if (message.id !== messageId) return message
      const safeResult = message.result && typeof message.result === 'object'
        ? { ...message.result }
        : null
      const nextActionUi = {
        phase,
        execution: null,
        error: typeof opts.error === 'string' ? opts.error : null,
        retryDecision: null,
      }
      if (!safeResult?.pendingAction || typeof safeResult.pendingAction !== 'object') {
        return { ...message, actionUi: nextActionUi }
      }
      return {
        ...message,
        result: {
          ...safeResult,
          pendingAction: { ...safeResult.pendingAction, status: terminalStatus },
        },
        actionUi: nextActionUi,
      }
    }))
    clearConversationId()
  }

  const handleActionDecision = async (messageId, decision) => {
    if (actionLocksRef.current.has(messageId)) return

    const message = messages.find(item => item.id === messageId)
    const action = message?.result?.pendingAction
    const secret = actionSecretsRef.current.get(messageId)
    if (!isSupportedPendingAction(action) || !secret?.confirmationNonce) {
      updateActionUi(messageId, initialActionUi('error', '草稿确认信息不可用，请重新生成草稿。'))
      actionSecretsRef.current.delete(messageId)
      idempotencyKeysRef.current.delete(messageId)
      return
    }

    actionLocksRef.current.add(messageId)
    let idempotencyKey = idempotencyKeysRef.current.get(messageId)
    if (decision === 'confirm' && !idempotencyKey) {
      idempotencyKey = crypto.randomUUID()
      idempotencyKeysRef.current.set(messageId, idempotencyKey)
    }

    updateActionUi(messageId, {
      phase: decision === 'confirm' ? 'confirming' : 'cancelling',
      execution: null,
      error: null,
      retryDecision: null,
    })

    try {
      const execution = decision === 'confirm'
        ? await confirmBusinessAction({
            actionId: action.actionId,
            confirmationNonce: secret.confirmationNonce,
            idempotencyKey,
            accessToken,
          })
        : await cancelBusinessAction({
            actionId: action.actionId,
            confirmationNonce: secret.confirmationNonce,
            accessToken,
          })

      const nextPendingActionMaterialized = materializeNextPendingAction(
        message,
        execution?.nextPendingAction,
      )

      updateActionUi(messageId, {
        phase: decision === 'confirm' ? 'succeeded' : 'cancelled',
        execution: executionForUi(execution),
        error: null,
        retryDecision: null,
      })
      const executedStatus = typeof execution?.status === 'string' ? execution.status : null
      if (decision === 'confirm' && executedStatus === 'SUCCEEDED') {
        syncPendingActionStatus(messageId, 'SUCCEEDED')
      } else if (decision === 'cancel' && executedStatus === 'CANCELLED') {
        syncPendingActionStatus(messageId, 'CANCELLED')
      }
      actionSecretsRef.current.delete(messageId)
      idempotencyKeysRef.current.delete(messageId)
      if (!nextPendingActionMaterialized) clearConversationId()
    } catch (requestError) {
      if (requestError instanceof AuthExpiredError || requestError?.httpStatus === 401) {
        resetActionRuntime()
        clearConversationId()
        onLogout()
        return
      }
      const safeError = requestError instanceof BusinessActionApiError
        ? requestError
        : new BusinessActionApiError({ message: '业务操作未完成，请稍后重试。' })
      const retryable = RETRYABLE_ACTION_ERRORS.has(safeError.errorCode)
        || isRetryableServerError(safeError.httpStatus)
      const errorCodeStatus = safeError.errorCode === 'ACTION_EXPIRED' ? 'EXPIRED' : null
      const responseStatus = typeof safeError.status === 'string'
        && AUTHORITATIVE_TERMINAL_STATUSES.has(safeError.status)
        ? safeError.status
        : null
      const terminalStatus = responseStatus || errorCodeStatus

      if (terminalStatus) {
        applyActionTerminal(messageId, terminalStatus, { error: safeError.message })
        actionSecretsRef.current.delete(messageId)
        idempotencyKeysRef.current.delete(messageId)
      } else {
        updateActionUi(messageId, {
          phase: 'error',
          execution: null,
          error: safeError.message,
          retryDecision: retryable ? decision : null,
        })
        if (!retryable) {
          actionSecretsRef.current.delete(messageId)
          idempotencyKeysRef.current.delete(messageId)
        }
      }
    } finally {
      actionLocksRef.current.delete(messageId)
    }
  }

  const handleActionExpire = messageId => {
    if (actionLocksRef.current.has(messageId)) return
    updateActionUi(messageId, current => {
      if (!current || !['pending', 'error'].includes(current.phase)) return current
      return initialActionUi('expired')
    })
    actionSecretsRef.current.delete(messageId)
    idempotencyKeysRef.current.delete(messageId)
  }

  return {
    actionBusy,
    hasActiveAction,
    resetActionRuntime,
    registerActionSecret,
    handleActionConfirm: messageId => handleActionDecision(messageId, 'confirm'),
    handleActionCancel: messageId => handleActionDecision(messageId, 'cancel'),
    handleActionExpire,
  }
}
