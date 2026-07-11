import './ChatInput.css'

export default function ChatInput({ input, setInput, onSend, loading }) {
  const isEmpty = input.trim().length === 0
  const isOverLimit = input.length > 2000

  return (
    <div className="chat-input-area">
      <div className="chat-input-wrapper">
        <textarea
          className="chat-textarea"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              if (!loading && !isEmpty && !isOverLimit) onSend()
            }
          }}
          placeholder="输入问题... (Enter 发送，Shift+Enter 换行)"
          disabled={loading}
          rows={1}
        />
        <div className="chat-input-footer">
          <span className={`char-count ${isOverLimit ? 'over' : ''}`}>
            {input.length} / 2000
          </span>
          <button
            className="send-btn"
            onClick={onSend}
            disabled={loading || isEmpty || isOverLimit}
          >
            {loading ? (
              <span className="send-loading">发送中</span>
            ) : (
              <>
                发送
                <span className="send-icon">↑</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
