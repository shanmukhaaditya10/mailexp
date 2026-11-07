export function namesOf(lead) {
  const firsts = (lead.founders || []).map((f) => f.first_name).filter(Boolean)
  if (firsts.length <= 1) return firsts[0] || ''
  if (firsts.length === 2) return `${firsts[0]} and ${firsts[1]}`
  return firsts.slice(0, -1).join(', ') + ' and ' + firsts[firsts.length - 1]
}

export function companyUrl(lead) {
  if (lead.website) return lead.website
  if (lead.domain) return `https://${lead.domain}`
  return null
}

export const STATUS_LABEL = {
  not_contacted: 'Not contacted',
  contacted: 'Contacted',
  replied: 'Replied',
  bounced: 'Bounced',
  done: 'Done',
}
