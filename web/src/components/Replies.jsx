export default function Replies({ replies }) {
  if (!replies.length) return <p className="empty">No replies detected yet. Run sync after you have replies.</p>
  return (
    <section>
      {replies.map((r) => (
        <div className="card col" key={r.id}>
          <div className="row-between">
            <strong>{r.company}</strong>
            <span className={`badge ${r.kind}`}>{r.kind}</span>
          </div>
          <div className="muted small">{r.from_addr} · {r.received_at?.slice(0, 16).replace('T', ' ')}</div>
          <p>{r.snippet}</p>
        </div>
      ))}
    </section>
  )
}
