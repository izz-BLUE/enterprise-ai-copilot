import { useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './ChatMessage.css'

const CATEGORY_LABELS = {
  'illegal_or_policy_violation': '违法违规 / 伪造材料',
  'policy_bypass': '绕过企业制度 / 规避审批',
  'cybersecurity_attack': '网络安全攻击 / 黑客行为',
  'audit_tampering': '删除审计 / 隐藏痕迹',
  'unauthorized_access': '越权访问 / 数据窃取',
  'access_control': 'Evaluation 权限受限',
}

const ROUTE_LABELS = {
  rag: { label: 'RAG 问答', cls: 'tag-blue' },
  eval: { label: '评估查询', cls: 'tag-purple' },
  refuse: { label: '安全拒答', cls: 'tag-red' },
}

function getStatusInfo(result) {
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

  return (
    <div className="msg-tags">
      <span className={`msg-tag ${status.cls}`}>{status.label}</span>
      {resultMode === 'agent' && route && (
        <span className={`msg-tag ${route.cls}`}>{route.label}</span>
      )}
      {resultMode === 'agent' && result?.safe !== undefined && (
        <span className={`msg-tag ${result.safe ? 'tag-green' : 'tag-red'}`}>
          {result.safe ? '安全通过' : '安全拦截'}
        </span>
      )}
      {resultMode === 'agent' && result?.category && result.category !== 'normal' && result.category !== 'error' && (
        <span className="msg-tag tag-orange">
          {CATEGORY_LABELS[result.category] || result.category}
        </span>
      )}
      {resultMode === 'rag' && result?.model && (
        <span className="msg-tag tag-blue">{result.model}</span>
      )}
    </div>
  )
}

export default function ChatMessage({ result, resultMode }) {
  const status = getStatusInfo(result)

  return (
    <div className="chat-message assistant">
      <div className="message-avatar">
        <span className="avatar-icon">⬡</span>
      </div>
      <div className="message-body">
        <div className="message-header">
          <span className="message-sender">AI Copilot</span>
          <MessageTags result={result} resultMode={resultMode} />
        </div>

        {status.type === 'access_denied' && (
          <div className="access-denied-banner">
            Evaluation 仅管理员可访问。请在左侧「管理员演示设置」中填入正确的 Admin Token。
          </div>
        )}

        {result?.reason && (
          <div className="message-reason">原因: {result.reason}</div>
        )}

        <div className="message-content markdown-body">
          <Markdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: (props) => (
                <a {...props} target="_blank" rel="noopener noreferrer" />
              ),
            }}
          >
            {result?.answer || '暂无回答内容'}
          </Markdown>
        </div>

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
        <span className="avatar-icon">U</span>
      </div>
    </div>
  )
}

export function LoadingMessage() {
  return (
    <div className="chat-message assistant">
      <div className="message-avatar">
        <span className="avatar-icon">⬡</span>
      </div>
      <div className="message-body">
        <div className="loading-indicator">
          <div className="loading-dots">
            <span /><span /><span />
          </div>
          <span className="loading-text">正在思考中...</span>
        </div>
      </div>
    </div>
  )
}
