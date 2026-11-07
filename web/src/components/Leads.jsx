import { useEffect, useState } from 'react'
import { namesOf, companyUrl, STATUS_LABEL } from '../lib/format'

export default function Leads({ leads, templates = [], onCompose, onSendSelected, busy }) {
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState(() => new Set())
  const [templateId, setTemplateId] = useState('')

  const filtered = leads.filter((l) =>
    (l.company + ' ' + (l.domain || '') + ' ' + namesOf(l)).toLowerCase().includes(q.toLowerCase()))
  const ids = filtered.map((l) => l.id)
  const selectedCount = selected.size
  const allSelected = filtered.length > 0 && ids.every((id) => selected.has(id))

  useEffect(() => {
    if (!templates.length) return
    if (templateId && templates.some((t) => String(t.id) === String(templateId))) return
    setTemplateId(String(templates[0].id))
  }, [templates, templateId])

  function toggle(id) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAll() {
    setSelected((prev) => {
      const next = new Set(prev)
      if (allSelected) ids.forEach((id) => next.delete(id))
      else ids.forEach((id) => next.add(id))
      return next
    })
  }

  const tplId = templateId === '' ? null : Number(templateId)
  const working = !!busy
  const selectedIds = [...selected]

  return (
    <section>
      <input className="search" placeholder="Search company or founder…" value={q} onChange={(e) => setQ(e.target.value)} />

      {templates.length > 0 && (
        <div className="queue-tools">
          <label className="check">
            Template for selected
            <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
              {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={allSelected} onChange={toggleAll} disabled={!filtered.length} />
            Select all
          </label>
          <button className="ghost" disabled={working || !selectedCount}
            onClick={() => onSendSelected(selectedIds, tplId)}>
            {busy === 'Queuing' ? 'Queuing…' : `Send selected (${selectedCount})`}
          </button>
        </div>
      )}

      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Company</th><th>Founders</th><th>Context</th><th>Email(s)</th><th>Status</th><th>Step</th><th>Next due</th><th></th></tr>
          </thead>
          <tbody>
            {filtered.map((l) => (
              <tr key={l.id} className={selected.has(l.id) ? 'sel' : ''}>
                <td>
                  <label className="lead-cell">
                    <input className="q-check" type="checkbox" checked={selected.has(l.id)}
                      onChange={() => toggle(l.id)} aria-label={`Select ${l.company}`} />
                    <span>
                      <strong>{l.company}</strong>
                      <div className="small">
                        {companyUrl(l)
                          ? <a className="company-link" href={companyUrl(l)} target="_blank" rel="noreferrer">{l.domain || l.website} ↗</a>
                          : <span className="muted">{l.domain || '—'}</span>}
                      </div>
                    </span>
                  </label>
                </td>
                <td>{namesOf(l)}</td>
                <td className="context" title={l.context || ''}>{l.context || '—'}</td>
                <td className="muted small">{l.founders.map((f) => f.email).filter(Boolean).join(', ')}</td>
                <td><span className={`badge ${l.status}`}>{STATUS_LABEL[l.status] || l.status}</span></td>
                <td>{l.step}/5</td>
                <td className="muted">{l.next_due_at ? l.next_due_at.slice(0, 10) : '—'}</td>
                <td><button className="ghost" onClick={() => onCompose(l)}>Email</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
