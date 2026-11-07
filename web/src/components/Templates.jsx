import { useState } from 'react'
import MarkdownEditor from './MarkdownEditor'

function TemplateEditor({ t, onSave }) {
  const [subject, setSubject] = useState(t.subject_tpl || '')
  const [body, setBody] = useState(t.body_tpl || '')
  return (
    <div className="card col">
      <div className="row-between">
        <strong>{t.name}</strong>
        <button className="ghost" onClick={() => onSave(t.id, { subject_tpl: subject, body_tpl: body })}>Save</button>
      </div>
      {t.step === 0
        ? <label>Subject<input value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
        : <div className="muted small">Subject: auto <em>Re: &lt;original&gt;</em></div>}
      <label>Body</label>
      <MarkdownEditor value={body} onChange={setBody} rows={8} placeholder="Write the email body in Markdown…" />
    </div>
  )
}

export default function Templates({ templates, onSave }) {
  return (
    <section>
      <p className="muted">Edit once — used everywhere. Markdown supported (bold, links, lists…). Variables: <code>{'{{names}}'}</code>, <code>{'{{first_name}}'}</code>, <code>{'{{company}}'}</code>.</p>
      {templates.map((t) => <TemplateEditor key={`${t.id}:${t.subject_tpl}:${t.body_tpl}`} t={t} onSave={onSave} />)}
    </section>
  )
}
