/** User-facing assistant text. Prefer the envelope message when one was stored. */
export function assistantVisibleText(message) {
  const output = message?.meta && typeof message.meta === 'object' ? message.meta.output : null
  if (output && typeof output === 'object' && typeof output.message === 'string' && output.message.trim()) {
    return output.message
  }
  return typeof message?.content === 'string' ? message.content : ''
}

/**
 * While a run is still going, a history poll may refresh older rows but must
 * not replace an existing assistant bubble or insert a new in-progress one.
 * The first load (no current rows) still shows the saved history.
 */
export function messagesAfterPoll(running, current, incoming) {
  const next = Array.isArray(incoming) ? incoming : []
  if (!running) return next
  const prior = Array.isArray(current) ? current : []
  if (!prior.length) return next
  const known = new Set(
    prior
      .filter((item) => item && item.role === 'assistant' && item.id)
      .map((item) => String(item.id)),
  )
  const byId = new Map(
    prior.filter((item) => item && item.id).map((item) => [String(item.id), item]),
  )
  const kept = next
    .filter((item) => {
      if (!item || item.role !== 'assistant') return true
      return known.has(String(item.id))
    })
    .map((item) => {
      if (item.role !== 'assistant') return item
      const previous = byId.get(String(item.id))
      return previous ? { ...item, content: previous.content } : item
    })
  const extras = prior.filter(
    (item) => item
      && item.role === 'assistant'
      && String(item.id || '').startsWith('tmp-')
      && !kept.some((row) => String(row.id) === String(item.id)),
  )
  return [...kept, ...extras]
}
