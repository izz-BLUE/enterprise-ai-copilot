import { useEffect, useState } from 'react'
import './DemoIdentityPanel.css'

const SAFE_ERRORS = {
  DEMO_IDENTITY_DISABLED: '演示身份功能当前未启用。',
  DEMO_IDENTITY_REQUIRED: '请选择有效的演示身份。',
  DEMO_IDENTITY_INVALID: '请选择有效的演示身份。',
}

export default function DemoIdentityPanel({
  demoIdentity,
  onInitialIdentity,
  onIdentityChange,
  disabled,
}) {
  const [identities, setIdentities] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    const controller = new AbortController()
    const load = async () => {
      try {
        const response = await fetch('/api/demo/identities', {
          headers: { Accept: 'application/json' },
          cache: 'no-store',
          signal: controller.signal,
        })
        const data = await response.json()
        if (!response.ok) {
          setError(SAFE_ERRORS[data?.errorCode] || '无法加载演示身份。')
          return
        }
        const available = Array.isArray(data?.identities) ? data.identities : []
        setIdentities(available)
        if (available.length > 0) {
          onInitialIdentity(available[0])
        } else {
          setError('当前没有可用的演示身份。')
        }
      } catch (requestError) {
        if (requestError.name !== 'AbortError') {
          setError('无法加载演示身份。')
        }
      }
    }
    load()
    return () => controller.abort()
  }, [onInitialIdentity])

  const selectIdentity = event => {
    const next = identities.find(identity => identity.userId === event.target.value)
    if (next) onIdentityChange(next)
  }

  return (
    <section className="demo-identity-panel" aria-label="演示身份设置">
      <div className="demo-identity-heading">
        <label htmlFor="demo-identity-select">演示身份</label>
        {demoIdentity?.role && <span className="demo-role">{demoIdentity.role}</span>}
      </div>
      <select
        id="demo-identity-select"
        aria-label="演示身份"
        value={demoIdentity?.userId || ''}
        onChange={selectIdentity}
        disabled={disabled || identities.length === 0}
      >
        {identities.length === 0 && <option value="">请选择</option>}
        {identities.map(identity => (
          <option key={identity.userId} value={identity.userId}>
            {identity.displayName}
          </option>
        ))}
      </select>
      <p>演示身份仅用于展示数据隔离，不是真实登录。</p>
      {error && <p className="demo-identity-error" role="alert">{error}</p>}
    </section>
  )
}
