import { useEffect, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import PendingActionCard from './PendingActionCard'
import UiIcon from './UiIcon'
import { isSupportedPendingAction } from '../hooks/useBusinessActionFlow'
import './ChatMessage.css'

const CATEGORY_LABELS = {
  'illegal_or_policy_violation': '违法违规 / 伪造材料',
  'policy_bypass': '绕过企业制度 / 规避审批',
  'cybersecurity_attack': '网络安全攻击 / 黑客行为',
  'audit_tampering': '删除审计 / 隐藏痕迹',
  'unauthorized_access': '越权访问 / 数据窃取',
  'access_control': 'Evaluation 权限受限',
  'overloaded': '服务繁忙',
  'business_action': '受控业务动作',
}

const ROUTE_LABELS = {
  rag: { label: 'RAG 问答', cls: 'tag-blue' },
  eval: { label: '评估查询', cls: 'tag-purple' },
  refuse: { label: '安全拒答', cls: 'tag-red' },
  busy: { label: '并发保护', cls: 'tag-orange' },
  action: { label: '业务动作', cls: 'tag-purple' },
  agent: { label: '智能体任务', cls: 'tag-blue' },
}

function getStatusInfo(result) {
  if (result?.httpStatus === 429 || result?.route === 'busy' || result?.category === 'overloaded') {
    return { type: 'busy', label: '服务繁忙', cls: 'tag-orange' }
  }
  if (result?.route === 'refuse' && result?.category === 'access_control') {
    return { type: 'access_denied', label: '权限受限', cls: 'tag-orange' }
  }
  if (result?.route === 'refuse' && result?.safe === false) {
    return { type: 'safety_refuse', label: '安全拒答', cls: 'tag-red' }
  }
  if (result?.route === 'error' || result?.success === false) {
    return { type: 'error', label: '请求错误', cls: 'tag-red' }
  }
  if (result?.success === true) {
    return { type: 'success', label: '请求成功', cls: 'tag-green' }
  }
  return { type: 'error', label: '未知状态', cls: 'tag-gray' }
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      window.prompt('请手动复制:', text)
    }
  }

  return (
    <button className="msg-copy-btn" onClick={handleCopy} title="复制回答">
      {copied ? '✓ 已复制' : '复制'}
    </button>
  )
}

function MessageTags({ result, resultMode }) {
  const status = getStatusInfo(result)
  const route = ROUTE_LABELS[result?.route]
  const category = result?.category
  const showCategory = resultMode === 'agent'
    && category
    && !['normal', 'error', 'business_action'].includes(category)
    && CATEGORY_LABELS[category] !== route?.label

  return (
    <div className="msg-tags">
      <span className={`msg-tag ${status.cls}`}>{status.label}</span>
      {route && route.label !== status.label && (
        <span className={`msg-tag ${route.cls}`}>{route.label}</span>
      )}
      {resultMode === 'agent' && result?.safe === false && (
        <span className="msg-tag tag-red">安全拦截</span>
      )}
      {showCategory && (
        <span className="msg-tag tag-orange">
          {CATEGORY_LABELS[category] || category}
        </span>
      )}
    </div>
  )
}

export default function ChatMessage({
  result,
  resultMode,
  actionUi,
  onActionConfirm,
  onActionCancel,
  onActionExpire,
}) {
  const status = getStatusInfo(result)
  const pendingAction = result?.pendingAction
  const supportedAction = isSupportedPendingAction(pendingAction)

  return (
    <div className="chat-message assistant">
      <div className="message-avatar">
        <span className="avatar-icon"><UiIcon name="sparkles" size={16} /></span>
      </div>
      <div className="message-body">
        <div className="message-header">
          <span className="message-sender">AI Copilot</span>
          <MessageTags result={result} resultMode={resultMode} />
        </div>

        {status.type === 'access_denied' && (
          <div className="access-denied-banner">
            Evaluation 仅管理员可访问，请使用 ADMIN 身份登录。
          </div>
        )}

        {result?.reason && (
          <div className="message-reason">原因: {result.reason}</div>
        )}

        <div className="message-content markdown-body">
          <Markdown
            remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
            components={{
              a: (props) => (
                <a {...props} target="_blank" rel="noopener noreferrer" />
              ),
            }}
          >
            {result?.answer || '暂无回答内容'}
          </Markdown>
        </div>

        {pendingAction && supportedAction && actionUi && (
          <PendingActionCard
            action={pendingAction}
            actionUi={actionUi}
            onConfirm={onActionConfirm}
            onCancel={onActionCancel}
            onExpire={onActionExpire}
          />
        )}

        {pendingAction && !supportedAction && (
          <div className="unsupported-action" role="alert">
            不支持此操作类型，未提供任何执行按钮。
          </div>
        )}

        <div className="message-actions">
          <CopyButton text={result?.answer || ''} />
        </div>
      </div>
    </div>
  )
}

export function UserMessage({ question }) {
  return (
    <div className="chat-message user">
      <div className="message-body">
        <div className="message-content">{question}</div>
      </div>
      <div className="message-avatar user-avatar">
        <span className="avatar-icon">我</span>
      </div>
    </div>
  )
}

export function LoadingMessage() {
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  useEffect(() => {
    const started = Date.now()
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - started) / 1000))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [])
  const stage = elapsedSeconds < 8
    ? '正在准备请求'
    : elapsedSeconds < 22
      ? '正在检索与分析'
      : elapsedSeconds < 40
        ? '正在生成回答'
        : '即将到达请求超时，请稍候'
  return (
    <div className="chat-message assistant">
      <div className="message-avatar">
        <span className="avatar-icon"><UiIcon name="sparkles" size={16} /></span>
      </div>
      <div className="message-body">
        <div className="loading-indicator">
          <div className="loading-dots">
            <span /><span /><span />
          </div>
          <span className="loading-text">{stage} · {elapsedSeconds} 秒</span>
        </div>
      </div>
    </div>
  )
}
