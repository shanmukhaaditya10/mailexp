import { companyUrl } from '../lib/format'
import MarkdownEditor from './MarkdownEditor'

export default function Composer({ data, setData, onClose, onSave, onSend, onContextChange }) {
  const { lead, message } = data
  const setMsg = (k, v) => setData((c) => ({ ...c, message: { ...c.message, [k]: v } }))
  const to = (message.to_addrs || '').split(',').filter(Boolean)
  return (
    <div className="overlay" onClick={onClose}>
      <div className="composer" onClick={(e) => e.stopPropagation()}>
        <div className="row-between">
          <h2>
            {lead.company}{' '}<span className="muted small">step {message.step}/5</span>
          </h2>
          {companyUrl(lead) && (
            <div className="small"><a className="company-link" href={companyUrl(lead)} target="_blank" rel="noreferrer">{lead.domain || lead.website} ↗</a></div>
          )}
          <button className="ghost" onClick={onClose}>Close</button>
        </div>
        <div className="muted small">To: {to.join(', ') || 'no email found'}</div>

        <label>Company context (shows here so you can write a custom subject)
          <textarea rows={2} value={data.context} onChange={(e) => onContextChange(e.target.value)}
            placeholder="e.g. what the product does / why you like it" /></label>

        <label>Subject
          <input value={message.subject || ''} onChange={(e) => setMsg('subject', e.target.value)} /></label>

        <label>Body <span className="muted">· Markdown supported</span></label>
        <MarkdownEditor value={message.body || ''} onChange={(v) => setMsg('body', v)} rows={14}
          placeholder="Write your email in Markdown — **bold**, _italic_, [links](https://…), lists…" />

        <label className="check">
          <input type="checkbox" checked={!!message.attach_resume}
            onChange={(e) => setMsg('attach_resume', e.target.checked ? 1 : 0)} /> Attach resume
        </label>

        <div className="actions">
          <button className="ghost" onClick={() => onSave(data.context)}>Save draft</button>
          <button className="primary" onClick={onSend}>Send</button>
        </div>
      </div>
    </div>
  )
}
