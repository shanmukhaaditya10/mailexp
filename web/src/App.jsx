import { useEffect, useState } from 'react'
import { api } from './api'
import Stat from './components/Stat'
import JobToast from './components/JobToast'
import Sidebar from './components/Sidebar'
import Leads from './components/Leads'
import Queue from './components/Queue'
import Templates from './components/Templates'
import Replies from './components/Replies'
import Settings from './components/Settings'
import Composer from './components/Composer'
import './App.css'

export default function App() {
  const [tab, setTab] = useState('leads')
  const [lists, setLists] = useState([])
  const [activeList, setActiveList] = useState(null)
  const [leads, setLeads] = useState([])
  const [queue, setQueue] = useState([])
  const [templates, setTemplates] = useState([])
  const [replies, setReplies] = useState([])
  const [settings, setSettings] = useState({})
  const [stats, setStats] = useState({})
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [composer, setComposer] = useState(null)
  const [notice, setNotice] = useState('')
  const [jobs, setJobs] = useState([])

  function flash(msg) {
    setNotice(msg)
    setTimeout(() => setNotice(''), 5000)
  }

  async function refresh() {
    setError('')
    try {
      const [ls, l, q, t, r, s, st, j] = await Promise.all([
        api.lists(), api.leads(activeList), api.queue(activeList), api.templates(),
        api.replies(activeList), api.settings(), api.stats(activeList), api.sendJobs(),
      ])
      setLists(ls); setLeads(l); setQueue(q); setTemplates(t); setReplies(r)
      setSettings(s); setStats(st); setJobs(j)
    } catch (e) {
      setError(String(e.message || e))
    }
  }

  useEffect(() => { refresh() }, [activeList])
  useEffect(() => {
    const id = setInterval(refresh, 1200)
    return () => clearInterval(id)
  }, [activeList])

  async function run(label, fn) {
    setBusy(label); setError('')
    try { const out = await fn(); await refresh(); return out }
    catch (e) { setError(String(e.message || e)) }
    finally { setBusy('') }
  }

  async function awaitJobOutcome(jobId) {
    if (!jobId) return null
    for (let i = 0; i < 12; i++) {
      const jobs = await api.sendJobs().catch(() => null)
      const j = jobs && jobs.find((x) => x.id === jobId)
      if (j && j.status === 'done') return j
      await new Promise((r) => setTimeout(r, 500))
    }
    return null
  }

  function jobResultText(j) {
    const parts = Object.entries(j.summary || {}).map(([k, v]) => `${v} ${k}`)
    if (j.note) parts.push(j.note)
    return `Send ${j.cancelled ? 'cancelled' : 'finished'}: ${j.done}/${j.total}${parts.length ? ` — ${parts.join(', ')}` : ''}`
  }

  async function reportSend(out) {
    if (!out) return
    if (!out.job_id) { flash(out.note || 'Nothing to send.'); return }
    flash(`Send queued (${out.queued}) — processing…`)
    const done = await awaitJobOutcome(out.job_id)
    if (done) { flash(jobResultText(done)); refresh() }
  }

  async function importList(listId) {
    const out = await run(`Importing ${listId}`, () => api.importList(listId))
    if (out) flash(`Imported from ${out.file}: ${out.leads} new leads, ${out.founders} new founders.`)
  }

  async function toggleDry() {
    const next = settings.dry_run === '1' ? '0' : '1'
    await run('Switching', () => api.updateSettings({ dry_run: next }))
    flash(next === '0' ? 'LIVE mode ON — emails will actually send.' : 'Dry-run ON — emails will not send.')
  }

  async function openComposer(lead) {
    setBusy(`Preparing ${lead.company}`)
    setError('')
    try {
      const msg = await api.draft(lead.id)
      setComposer({ lead, message: msg, context: lead.context || '' })
    } catch (e) { setError(String(e.message || e)) }
    finally { setBusy('') }
  }

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <div className="mark">M</div>
          <div>
            <h1>mailexp</h1>
            <p className="sub">YC founder outreach · IMAP/SMTP</p>
          </div>
        </div>
        <div className="header-right">
          <div className="stats">
            <Stat label="Leads" value={stats.leads} />
            <Stat label="Founders" value={stats.founders} />
            <Stat label="Sent" value={stats.sent} />
            <Stat label="Replied" value={stats.replied} />
          </div>
          <button className="primary" disabled={!!busy} onClick={() => run('Sync', api.sync)}>
            {busy === 'Sync' ? 'Syncing…' : 'Sync mailbox'}
          </button>
          <button className={`mode ${settings.dry_run === '1' ? 'dryn' : 'live'}`} disabled={!!busy}
            title="Click to toggle between dry-run and live sending"
            onClick={toggleDry}>
            {settings.dry_run === '1' ? '● DRY RUN' : '● LIVE'}
          </button>
        </div>
      </header>

      <div className="layout">
        <Sidebar lists={lists} activeList={activeList} onSelect={setActiveList}
          onImport={importList} busy={busy} />

        <div className="content">
          <nav className="tabs">
            {[['leads', 'Leads'], ['queue', `Queue${queue.length ? ` (${queue.length})` : ''}`],
              ['templates', 'Templates'], ['replies', `Replies${replies.length ? ` (${replies.length})` : ''}`],
              ['settings', 'Settings']].map(([k, lbl]) => (
              <button key={k} className={tab === k ? 'tab active' : 'tab'} onClick={() => setTab(k)}>{lbl}</button>
            ))}
          </nav>

          {error && <div className="error">{error}</div>}
          {notice && <div className="notice">{notice}</div>}

          <main>
            {tab === 'leads' && <Leads leads={leads} templates={templates} onCompose={openComposer}
              onSendSelected={(leadIds, templateId) =>
                run('Queuing', () => api.sendQueue(activeList, { lead_ids: leadIds, template_id: templateId })).then(reportSend)}
              busy={busy} />}
            {tab === 'queue' && <Queue queue={queue} templates={templates} onCompose={openComposer}
              onSendAll={() => run('Queuing', () => api.sendQueue(activeList)).then(reportSend)}
              onSendSelected={(leadIds, templateId) =>
                run('Queuing', () => api.sendQueue(activeList, { lead_ids: leadIds, template_id: templateId })).then(reportSend)}
              busy={busy} settings={settings} />}
            {tab === 'templates' && <Templates templates={templates}
              onSave={(id, data) => run('Saving template', () => api.updateTemplate(id, data))} />}
            {tab === 'replies' && <Replies replies={replies} />}
            {tab === 'settings' && <Settings settings={settings}
              onSave={(data) => run('Saving settings', () => api.updateSettings(data))} />}
          </main>
        </div>
      </div>

      <div className="toaster">
        {busy && busy !== 'Sync' && <div className="toast">{busy}…</div>}
        {jobs.some(j => j.status === 'queued' || j.status === 'sending' || j.status === 'cancelling') && (
          <JobToast jobs={jobs.filter(j => j.status === 'queued' || j.status === 'sending' || j.status === 'cancelling')}
            onCancel={async (id) => { await api.cancelJob(id); refresh() }} />
        )}
      </div>

      {composer && <Composer data={composer} setData={setComposer}
        onClose={() => setComposer(null)}
        onContextChange={(v) => setComposer((c) => ({ ...c, context: v }))}
        onSave={async (ctx) => { await run('Saving', async () => {
          await api.updateMessage(composer.message.id, { subject: composer.message.subject, body: composer.message.body, attach_resume: composer.message.attach_resume ? 1 : 0 })
          await api.updateLead(composer.lead.id, { context: ctx })
        }) }}
        onSend={async () => {
          const { message, lead, context } = composer
          setComposer(null)
          setError('')
          try {
            await api.updateMessage(message.id, { subject: message.subject, body: message.body, attach_resume: message.attach_resume ? 1 : 0 })
            await api.updateLead(lead.id, { context })
            const job = await api.sendMessage(message.id)
            if (!job || !job.id) { flash('Nothing to send.') }
            else {
              flash('Send queued — processing…')
              const done = await awaitJobOutcome(job.id)
              if (done) flash(jobResultText(done))
            }
          } catch (e) {
            setError(String(e.message || e))
          }
          refresh()
        }} />}
    </div>
  )
}
