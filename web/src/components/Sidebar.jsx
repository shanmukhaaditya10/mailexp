function prettyName(stem) {
  return stem.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function Sidebar({ lists, activeList, onSelect, onImport, busy }) {
  const total = lists.reduce((sum, l) => sum + (l.count || 0), 0)
  const rootLists = lists.filter((l) => !l.folder)
  const folders = {}
  for (const l of lists) {
    if (l.folder) (folders[l.folder] = folders[l.folder] || []).push(l)
  }

  const renderItem = (l) => (
    <div key={l.id} className={`side-row ${activeList === l.id ? 'active' : ''}`} title={l.file}>
      <button className="side-item" title={l.file} onClick={() => onSelect(l.id)}>
        <span className="side-name">{prettyName(l.name)}</span>
        <span className="side-count">{l.count || 0}</span>
      </button>
      <button className="side-import" title={`Import / refresh from ${l.file}`}
        disabled={!!busy} onClick={() => onImport(l.id)}>⟳</button>
    </div>
  )

  return (
    <aside className="sidebar">
      <div className="side-title">Lists</div>
      <button className={`side-item ${activeList === null ? 'active' : ''}`} onClick={() => onSelect(null)}>
        <span className="side-name">All lists</span>
        <span className="side-count">{total}</span>
      </button>
      {rootLists.map(renderItem)}
      {Object.entries(folders).map(([folder, items]) => {
        const folderCount = items.reduce((sum, l) => sum + (l.count || 0), 0)
        return (
          <div key={folder} className="side-folder">
            <div className="side-folder-title" title={folder}>
              <span className="side-name">{prettyName(folder)}</span>
              <span className="side-count">{folderCount}</span>
            </div>
            {items.map(renderItem)}
          </div>
        )
      })}
      {!lists.length && <p className="muted small">No .xlsx files found in the project folder.</p>}
    </aside>
  )
}
