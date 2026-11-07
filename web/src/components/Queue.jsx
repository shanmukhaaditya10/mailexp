import { useEffect, useState } from 'react'
import { namesOf, companyUrl } from '../lib/format'

export default function Queue({ queue, templates, onCompose, onSendAll, onSendSelected, busy, settings }) {
  const [selected, setSelected] = useState(() => new Set())
  const [templateId, setTemplateId] = useState('')

  const ids = queue.map((l) => l.id)
  const selectedIds = ids.filter((id) => selected.has(id))
  const allSelected = queue.length > 0 && selectedIds.length === queue.length

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
    setSelected(allSelected ? new Set() : new Set(ids))
  }

  const tplId = templateId === '' ? null : Number(templateId)
  const working = !!busy

  return (
    <section>
      <div className="row-between">
        <p className="muted">Leads due for their next email (every {settings.step_gap_days || 2} days).
          {settings.dry_run === '1' && ' Dry-run is ON.'}</p>
        <button className="primary" disabled={working || !queue.length}
          onClick={onSendAll}>{busy === 'Queuing' ? 'Queuing…' : `Send all due (${queue.length})`}</button>
      </div>

      {templates.length > 0 && (
        <div className="queue-tools">
          <label className="check">
            Template for selected
            <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
              {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={allSelected} onChange={toggleAll} disabled={!queue.length} />
            Select all
          </label>
          <button className="ghost" disabled={working || !selectedIds.length}
            onClick={() => onSendSelected(selectedIds, tplId)}>
            {busy === 'Queuing' ? 'Queuing…' : `Send selected (${selectedIds.length})`}
          </button>
        </div>
      )}

      {!queue.length && <p className="empty">Nothing due right now.</p>}
      <div className="cards">
        {queue.map((l) => (
          <div className={`card ${selected.has(l.id) ? 'sel' : ''}`} key={l.id}>
            <div className="card-main">
              <input className="q-check" type="checkbox" checked={selected.has(l.id)}
                onChange={() => toggle(l.id)} aria-label={`Select ${l.company}`} />
              <div>
                <strong>{l.company}</strong>
                {companyUrl(l) && (
                  <div className="small"><a className="company-link" href={companyUrl(l)} target="_blank" rel="noreferrer">{l.domain || l.website} ↗</a></div>
                )}
                <div className="ctx">{l.context || '—'}</div>
                <div className="muted small">{namesOf(l)} · step {l.step}/5</div>
                <div className="muted small">{(l.founders || []).map((f) => f.email).filter(Boolean).join(', ') || 'no email'}</div>
              </div>
            </div>
            <button className="ghost" onClick={() => onCompose(l)}>Review & send</button>
          </div>
        ))}
      </div>
    </section>
  )
}
