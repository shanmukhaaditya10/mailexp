import { useState } from 'react'

export default function Settings({ settings, onSave }) {
  const [form, setForm] = useState({ ...settings })
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })
  return (
    <section className="settings">
      <div className="card col">
        <label>Daily send cap<input value={form.daily_cap || ''} onChange={set('daily_cap')} /></label>
        <label>Send window (HH:MM-HH:MM)<input value={form.send_window || ''} onChange={set('send_window')} placeholder="09:00-18:00" /></label>
        <label>Min seconds between sends (1 = 1/sec, 0.5 = 2/sec)<input value={form.min_send_interval_seconds || ''} onChange={set('min_send_interval_seconds')} placeholder="2" /></label>
        <label>Follow-up gap (days)<input value={form.step_gap_days || ''} onChange={set('step_gap_days')} /></label>
        <label className="check"><input type="checkbox" checked={form.dry_run === '1'}
          onChange={(e) => setForm({ ...form, dry_run: e.target.checked ? '1' : '0' })} /> Dry-run (log instead of send)</label>
        <label>Resume path<input value={form.resume_path || ''} onChange={set('resume_path')} /></label>
        <label>Signature (optional)<textarea rows={3} value={form.signature || ''} onChange={set('signature')} /></label>
        <div className="row-between">
          <span className="muted small">{form._gmail_configured ? 'Gmail creds: .env present' : 'Gmail creds: add .env'}</span>
          <button className="primary" onClick={() => onSave({
            daily_cap: String(form.daily_cap || 40),
            send_window: form.send_window || '',
            min_send_interval_seconds: String(form.min_send_interval_seconds || 2),
            step_gap_days: String(form.step_gap_days || 2),
            dry_run: form.dry_run,
            resume_path: form.resume_path || '',
            signature: form.signature || '',
          })}>Save</button>
        </div>
      </div>
      <p className="muted small">Gmail address + app password live in <code>.env</code> (see <code>.env.example</code>).</p>
    </section>
  )
}
