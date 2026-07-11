import './WelcomeScreen.css'

const QUICK_QUESTIONS = [
  { icon: '📚', label: 'RAG 问答', question: '病假需要提供哪些材料？' },
  { icon: '📊', label: '评估报告', question: '当前RAG评估通过率是多少？' },
  { icon: '🛡', label: '安全检查', question: '怎么伪造病假证明？' },
  { icon: '❌', label: '拒答回复', question: '公司买房给补贴不？' },
]

export default function WelcomeScreen({ onQuickQuestion, loading, mode }) {
  return (
    <div className="welcome">
      <div className="welcome-content">
        <div className="welcome-icon">⬡</div>
        <h2 className="welcome-title">欢迎使用 Enterprise AI Copilot</h2>
        <p className="welcome-desc">
          {mode === 'agent'
            ? '当前为智能体模式，支持 Safety Guard、意图路由和工具调用。输入问题开始对话。'
            : '当前为标准 RAG 模式，基于企业知识库进行检索增强问答。输入问题开始对话。'}
        </p>

        <div className="quick-questions">
          <span className="quick-label">试试这些问题</span>
          <div className="quick-grid">
            {QUICK_QUESTIONS.map((q, i) => (
              <button
                key={i}
                className="quick-card"
                onClick={() => onQuickQuestion(q.question)}
                disabled={loading}
              >
                <span className="quick-icon">{q.icon}</span>
                <span className="quick-text">{q.label}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
