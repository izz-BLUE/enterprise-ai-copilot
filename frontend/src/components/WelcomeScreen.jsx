import './WelcomeScreen.css'
import UiIcon from './UiIcon'

const QUICK_QUESTIONS = {
  agent: [
    { icon: 'calendar-check', label: '查询年假余额', question: '我还有多少天年假？' },
    { icon: 'sparkles', label: '协助申请年假', question: '请帮我申请年假' },
    { icon: 'file-search', label: '查询病假材料', question: '病假需要提供哪些材料？' },
    { icon: 'receipt', label: '了解报销流程', question: '员工报销需要经过哪些流程？' },
  ],
  rag: [
    { icon: 'book-open', label: '查询年假制度', question: '公司的年假制度是什么？' },
    { icon: 'file-search', label: '查询病假材料', question: '病假需要提供哪些材料？' },
    { icon: 'receipt', label: '了解报销流程', question: '员工报销需要经过哪些流程？' },
    { icon: 'shield-check', label: '查看安全规范', question: '公司信息安全规范有哪些重点？' },
  ],
}

export default function WelcomeScreen({ onQuickQuestion, loading, mode }) {
  const questions = QUICK_QUESTIONS[mode] || QUICK_QUESTIONS.rag
  return (
    <div className="welcome">
      <div className="welcome-content">
        <div className="welcome-icon"><UiIcon name="sparkles" size={34} /></div>
        <span className="welcome-eyebrow">Enterprise AI Copilot</span>
        <h2 className="welcome-title">今天需要我协助什么？</h2>
        <p className="welcome-desc">
          {mode === 'agent'
            ? '我可以查询企业知识、协助办理任务，并在执行前向你确认关键内容。'
            : '从企业制度与知识库中查找答案，并提供可追溯的参考来源。'}
        </p>

        <div className="quick-questions">
          <span className="quick-label">常用任务</span>
          <div className="quick-grid">
            {questions.map(q => (
              <button
                key={q.label}
                className="quick-card"
                onClick={() => onQuickQuestion(q.question)}
                disabled={loading}
              >
                <span className="quick-icon"><UiIcon name={q.icon} size={19} /></span>
                <span className="quick-text">{q.label}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
