export default function JobToast({ jobs, onCancel }) {
  return (
    <div className="job-toasts">
      {jobs.map((j) => {
        const pct = j.total ? Math.round((j.done / j.total) * 100) : 0
        const cancelling = j.cancelled || j.status === 'cancelling'
        return (
          <div className="job-toast" key={j.id}>
            <div className="job-head">
              <strong>{j.dry_run ? 'Dry-run send' : 'Sending emails'}</strong>
              <span className="muted small">{j.done}/{j.total}</span>
            </div>
            <div className="job-bar"><div className="job-fill" style={{ width: `${pct}%` }} /></div>
            <div className="job-actions">
              <span className="muted small">
                {cancelling ? 'Cancelling…' : j.dry_run ? 'logging, not sending' : 'sending in background…'}
              </span>
              <button className="ghost sm" disabled={cancelling} onClick={() => onCancel(j.id)}>Cancel</button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
