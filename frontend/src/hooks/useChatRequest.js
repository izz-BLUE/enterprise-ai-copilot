import { useState } from 'react'
import { AuthExpiredError, RequestTimeoutError, authenticatedFetch } from '../services/authApi'
import { isTerminalActionStatus, MAX_MESSAGES } from '../services/chatHistoryStorage'
import { isRetryableServerError } from '../services/httpErrorPolicy'
import { initialActionUi, isSupportedPendingAction } from './useBusinessActionFlow'

const newMessageId = () => crypto.randomUUID()

export default function useChatRequest({
  mode,
  input,
  setInput,
  setMessages,
  accessToken,
  conversationIdRef,
  rememberConversationId,
  clearConversationId,
  registerActionSecret,
  onAuthenticationExpired,
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [failedQuestion, setFailedQuestion] = useState(null)

  const resetRequestState = () => {
    setError(null)
    setFailedQuestion(null)
  }

  const sendMessage = async questionOverride => {
    const question = typeof questionOverride === 'string'
      ? questionOverride.trim()
      : input.trim()
    if (!question || loading) return

    const requestMode = mode
    setLoading(true)
    resetRequestState()
    setInput('')
    setMessages(previous => [...previous, {
      id: newMessageId(), type: 'user', question,
    }].slice(-MAX_MESSAGES))

    const endpoint = requestMode === 'agent' ? '/api/agent/langgraph/chat' : '/api/chat'
    const headers = { 'Content-Type': 'application/json', Accept: 'application/json' }
    const requestBody = requestMode === 'agent'
      ? { message: question, conversationId: conversationIdRef.current || undefined }
      : { message: question }

    const appendAssistantMessage = responseData => {
      const messageId = newMessageId()
      let safeData = responseData
      let actionUi = null
      if (responseData?.pendingAction && typeof responseData.pendingAction === 'object') {
        const { confirmationNonce, ...publicPendingAction } = responseData.pendingAction
        safeData = { ...responseData, pendingAction: publicPendingAction }
        if (isSupportedPendingAction(publicPendingAction)) {
          actionUi = registerActionSecret(messageId, confirmationNonce)
            ? initialActionUi()
            : initialActionUi('error', '草稿确认信息不可用，请重新生成草稿。')
        }
      }
      setMessages(previous => [...previous, {
        id: messageId,
        type: 'assistant',
        question,
        result: { question, requestMode, ...safeData },
        resultMode: requestMode,
        actionUi,
      }].slice(-MAX_MESSAGES))
      const returnedStatus = safeData?.pendingAction?.status
      if (typeof returnedStatus === 'string' && isTerminalActionStatus(returnedStatus)) {
        clearConversationId()
      }
    }

    try {
      const response = await authenticatedFetch(endpoint, accessToken, {
        method: 'POST', headers, body: JSON.stringify(requestBody),
      })
      let data = null
      try {
        data = await response.json()
      } catch {
        if (response.ok) throw { type: 'parse_error' }
      }
      if (!response.ok) {
        if (data?.answer) {
          appendAssistantMessage({ httpStatus: response.status, ...data })
          return
        }
        throw { type: 'http_error', status: response.status }
      }
      if (!data.traceId) {
        data.traceId = response.headers.get('X-Trace-Id') || undefined
      }
      if (requestMode === 'agent') {
        const returnedConversationId = response.headers.get('X-Conversation-Id')
        if (returnedConversationId) rememberConversationId(returnedConversationId)
      }
      appendAssistantMessage(data)
    } catch (requestError) {
      if (requestError instanceof AuthExpiredError || requestError?.httpStatus === 401) {
        onAuthenticationExpired()
        return
      }
      if (requestError instanceof RequestTimeoutError) {
        setError('请求等待时间过长，已停止等待；可以重新发送。')
      } else if (requestError instanceof TypeError) {
        setError('无法连接到 Java 后端，请确认服务已启动。')
      } else if (requestError?.type === 'http_error') {
        setError(isRetryableServerError(requestError.status)
          ? 'AI 服务暂时不可用，请稍后重试。'
          : `服务返回错误（HTTP ${requestError.status}）`)
      } else if (requestError?.type === 'parse_error') {
        setError('服务响应格式异常，无法解析返回数据。')
      } else {
        setError('请求发生未知错误。')
      }
      setInput(current => current || question)
      setFailedQuestion(question)
    } finally {
      setLoading(false)
    }
  }

  return {
    loading,
    error,
    failedQuestion,
    sendMessage,
    resetRequestState,
    clearError: () => setError(null),
  }
}
