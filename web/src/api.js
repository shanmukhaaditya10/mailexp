const BASE = ''

function qs(list) {
  return list ? `?list=${encodeURIComponent(list)}` : ''
}

async function req(path, opts = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  lists: () => req('/api/lists'),
  importList: (list) => req(`/api/import${qs(list)}`, { method: 'POST' }),
  leads: (list) => req('/api/leads' + qs(list)),
  lead: (id) => req(`/api/leads/${id}`),
  updateLead: (id, data) => req(`/api/leads/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  queue: (list) => req('/api/queue' + qs(list)),
  replies: (list) => req('/api/replies' + qs(list)),
  stats: (list) => req('/api/stats' + qs(list)),
  templates: () => req('/api/templates'),
  updateTemplate: (id, data) => req(`/api/templates/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  settings: () => req('/api/settings'),
  updateSettings: (data) => req('/api/settings', { method: 'PUT', body: JSON.stringify(data) }),
  sync: () => req('/api/sync', { method: 'POST' }),
  draft: (id) => req(`/api/leads/${id}/draft`, { method: 'POST' }),
  updateMessage: (id, data) => req(`/api/messages/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  sendMessage: (id) => req(`/api/messages/${id}/send`, { method: 'POST' }),
  sendQueue: (list, selection) => req('/api/queue/send' + qs(list), { method: 'POST', body: JSON.stringify(selection || {}) }),
  draftQueue: (list, selection) => req('/api/queue/drafts' + qs(list), { method: 'POST', body: JSON.stringify(selection || {}) }),
  sendJobs: () => req('/api/send-jobs'),
  cancelJob: (id) => req(`/api/send-jobs/${id}/cancel`, { method: 'POST' }),
  outbox: () => req('/api/outbox'),
  resolveMessage: (id, sent) => req(`/api/messages/${id}/resolve`, { method: 'POST', body: JSON.stringify({ sent }) }),
  reconcileMessage: (id) => req(`/api/messages/${id}/reconcile`, { method: 'POST' }),
  retryMessage: (id, confirm = false) => req(`/api/messages/${id}/retry`, { method: 'POST', body: JSON.stringify({ confirm }) }),
}
