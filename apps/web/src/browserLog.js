/**
 * Browser console bridge → POST /api/console (pino-shaped events).
 * Keeps web.log useful for system-logs MCP.
 */
import pino from 'pino'

const QUEUE = []
const MAX_QUEUE = 40
const FLUSH_MS = 1500
const MIN_INTERVAL_MS = 800
let flushTimer = 0
let lastFlush = 0
let hooked = false

function enqueue(level, message, stack = '') {
  const entry = {
    level: String(level || 'error'),
    message: String(message || '').slice(0, 2000),
    stack: String(stack || '').slice(0, 3000),
    url: typeof location !== 'undefined' ? String(location.href || '') : '',
    ts: new Date().toISOString(),
  }
  QUEUE.push(entry)
  while (QUEUE.length > MAX_QUEUE) QUEUE.shift()
  scheduleFlush()
}

function scheduleFlush() {
  if (flushTimer) return
  const wait = Math.max(0, MIN_INTERVAL_MS - (Date.now() - lastFlush))
  flushTimer = window.setTimeout(() => {
    flushTimer = 0
    void flush()
  }, Math.max(wait, FLUSH_MS))
}

async function flush() {
  if (!QUEUE.length) return
  const batch = QUEUE.splice(0, 20)
  lastFlush = Date.now()
  try {
    await fetch('/api/console', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entries: batch }),
    })
  } catch {
    /* drop on network failure; avoid loops */
  }
}

export const browserLogger = pino({
  browser: {
    asObject: true,
    transmit: {
      send(level, logEvent) {
        const msg =
          (logEvent && logEvent.messages && logEvent.messages[0]) ||
          (logEvent && logEvent.msg) ||
          ''
        enqueue(level, msg)
      },
    },
  },
})

export function installBrowserLogBridge() {
  if (hooked || typeof window === 'undefined') return
  hooked = true

  const origError = console.error.bind(console)
  console.error = (...args) => {
    try {
      enqueue(
        'error',
        args.map((a) => (typeof a === 'string' ? a : safeStringify(a))).join(' '),
      )
    } catch {
      /* ignore */
    }
    origError(...args)
  }

  window.addEventListener('unhandledrejection', (ev) => {
    const reason = ev?.reason
    const msg =
      reason instanceof Error
        ? reason.message
        : typeof reason === 'string'
          ? reason
          : safeStringify(reason)
    const stack = reason instanceof Error ? reason.stack || '' : ''
    enqueue('error', `unhandledrejection: ${msg}`, stack)
  })

  window.addEventListener('error', (ev) => {
    enqueue(
      'error',
      `window.onerror: ${ev?.message || 'error'}`,
      (ev?.error && ev.error.stack) || '',
    )
  })
}

function safeStringify(v) {
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}
