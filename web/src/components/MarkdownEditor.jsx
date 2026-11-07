import { useMemo, useRef, useState } from 'react'
import { marked } from 'marked'

marked.setOptions({ breaks: true, gfm: true })

const ACTIONS = [
  { key: 'bold', icon: 'B', title: 'Bold', wrap: ['**', '**'], label: '**bold**' },
  { key: 'italic', icon: 'I', title: 'Italic', wrap: ['_', '_'], label: '_italic_' },
  { key: 'strike', icon: 'S', title: 'Strikethrough', wrap: ['~~', '~~'], label: '~~strike~~' },
  { key: 'code', icon: '</>', title: 'Inline code', wrap: ['`', '`'], label: '`code`' },
  { key: 'h2', icon: 'H', title: 'Heading', prefix: '## ', label: '## Heading' },
  { key: 'ul', icon: '•', title: 'Bullet list', prefix: '- ', label: '- item' },
  { key: 'ol', icon: '1.', title: 'Numbered list', prefix: '1. ', label: '1. item' },
  { key: 'quote', icon: '❝', title: 'Quote', prefix: '> ', label: '> quote' },
  { key: 'link', icon: '🔗', title: 'Link', wrap: ['[', '](url)'], label: '[text](url)' },
]

function applyAction(text, selStart, selEnd, action) {
  const before = text.slice(0, selStart)
  const selected = text.slice(selStart, selEnd)
  const after = text.slice(selEnd)

  if (action.wrap) {
    const [open, close] = action.wrap
    const inner = selected || action.label.replace(/[*_~`]/g, '')
    const next = before + open + inner + close + after
    return { text: next, selStart: selStart + open.length, selEnd: selStart + open.length + inner.length }
  }

  const lineStart = before.lastIndexOf('\n') + 1
  const lineEnd = after.indexOf('\n') === -1 ? text.length : selEnd + after.indexOf('\n')
  const line = text.slice(lineStart, lineEnd)
  const atLineStart = selStart === lineStart
  const already = line.startsWith(action.prefix)

  let next
  if (atLineStart && !already) {
    next = before + action.prefix + selected + after
    return { text: next, selStart: selStart + action.prefix.length, selEnd: selEnd + action.prefix.length }
  }
  const block = already ? line.slice(action.prefix.length) : action.prefix + line
  next = text.slice(0, lineStart) + block + text.slice(lineEnd)
  return { text: next, selStart: lineStart, selEnd: lineStart + block.length }
}

export default function MarkdownEditor({ value, onChange, rows = 14, placeholder }) {
  const ref = useRef(null)
  const [showPreview, setShowPreview] = useState(true)

  const html = useMemo(() => marked.parse(value || ''), [value])

  function run(action) {
    const el = ref.current
    if (!el) return
    const { selectionStart, selectionEnd } = el
    const { text, selStart, selEnd } = applyAction(value || '', selectionStart, selectionEnd, action)
    onChange(text)
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(selStart, selEnd)
    })
  }

  return (
    <div className="md-editor">
      <div className="md-toolbar">
        <div className="md-tools">
          {ACTIONS.map((a) => (
            <button key={a.key} type="button" className="md-tool" title={a.title} onClick={() => run(a)}>
              {a.icon}
            </button>
          ))}
        </div>
        <button type="button" className={`md-preview-toggle ${showPreview ? 'on' : ''}`}
          onClick={() => setShowPreview((v) => !v)}>
          {showPreview ? 'Hide preview' : 'Preview'}
        </button>
      </div>
      <div className={`md-body ${showPreview ? 'split' : ''}`}>
        <textarea ref={ref} rows={rows} value={value} placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)} />
        {showPreview && (
          <div className="md-preview" dangerouslySetInnerHTML={{ __html: html || '<p class="muted">Nothing to preview yet.</p>' }} />
        )}
      </div>
    </div>
  )
}
