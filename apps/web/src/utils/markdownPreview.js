/**
 * Post-process marked HTML for chat Markdown preview:
 * - linkify workplace file paths (bare or backticked) into download chips
 * - wrap / optionally fold tables
 * - promote file links to .md-file-chip cards
 * - query FINAL: Markdown table preview card by default
 * - fenced code blocks: toolbar + IDE-like syntax highlight (theme via CSS)
 *
 * Shared parse contract with API ``app.services.markdown_renderer`` (standard
 * Markdown / GFM). Telegram maps the same Markdown via that module +
 * ``telegram_markdown`` (HTML; tables as monospace <pre>, not MarkdownV2).
 */

import { format as formatSql } from 'sql-formatter'
import hljs from 'highlight.js/lib/core'
import langSql from 'highlight.js/lib/languages/sql'
import langJson from 'highlight.js/lib/languages/json'
import langPython from 'highlight.js/lib/languages/python'
import langJavascript from 'highlight.js/lib/languages/javascript'
import langTypescript from 'highlight.js/lib/languages/typescript'
import langBash from 'highlight.js/lib/languages/bash'
import langXml from 'highlight.js/lib/languages/xml'
import langYaml from 'highlight.js/lib/languages/yaml'
import langMarkdown from 'highlight.js/lib/languages/markdown'
import langGo from 'highlight.js/lib/languages/go'
import langRust from 'highlight.js/lib/languages/rust'
import langJava from 'highlight.js/lib/languages/java'
import langCpp from 'highlight.js/lib/languages/cpp'
import langCss from 'highlight.js/lib/languages/css'
import langShell from 'highlight.js/lib/languages/shell'

hljs.registerLanguage('sql', langSql)
hljs.registerLanguage('json', langJson)
hljs.registerLanguage('python', langPython)
hljs.registerLanguage('javascript', langJavascript)
hljs.registerLanguage('typescript', langTypescript)
hljs.registerLanguage('bash', langBash)
hljs.registerLanguage('shell', langShell)
hljs.registerLanguage('xml', langXml)
hljs.registerLanguage('html', langXml)
hljs.registerLanguage('yaml', langYaml)
hljs.registerLanguage('markdown', langMarkdown)
hljs.registerLanguage('go', langGo)
hljs.registerLanguage('rust', langRust)
hljs.registerLanguage('java', langJava)
hljs.registerLanguage('cpp', langCpp)
hljs.registerLanguage('c', langCpp)
hljs.registerLanguage('css', langCss)

const FILE_EXT_RE = /\.(?:pdf|xlsx|xlsm|xls|csv|sql)$/i
const CODE_FILE_RE = /<code>([^<]*\.(?:pdf|xlsx|xlsm|xls|csv|sql))<\/code>/gi
const BARE_FILE_TOKEN_RE = /(`[^`\n]*`)|((?:[\p{L}\p{N}_.-]+\/)*[\p{L}\p{N}_.-]+\.(?:pdf|xlsx|xlsm|xls|csv|sql))/giu
const TABLE_RE = /<table[\s\S]*?<\/table>/gi
const ANCHOR_RE = /<a\b([^>]*)>([\s\S]*?)<\/a>/gi
const JSON_FENCE_RE = /```(?:json|JSON)?\s*\n([\s\S]*?)```/g
const CODE_BLOCK_RE = /<pre><code([^>]*)>([\s\S]*?)<\/code><\/pre>/gi
const SQL_LANGUAGES = new Set([
  'sql', 'mysql', 'mariadb', 'postgres', 'postgresql', 'sqlite', 'clickhouse',
  'tsql', 'transactsql', 'plsql', 'hive', 'bigquery', 'snowflake', 'spark',
])

/** Map fence / toolbar language ids to highlight.js language names. */
function hljsLanguage(language) {
  const lang = String(language || '').toLowerCase()
  if (!lang) return ''
  if (SQL_LANGUAGES.has(lang)) return 'sql'
  if (lang === 'jsonc' || lang === 'json5') return 'json'
  if (lang === 'js' || lang === 'jsx' || lang === 'mjs' || lang === 'cjs') return 'javascript'
  if (lang === 'ts' || lang === 'tsx') return 'typescript'
  if (lang === 'py') return 'python'
  if (lang === 'sh' || lang === 'zsh' || lang === 'shellsession') return 'bash'
  if (lang === 'yml') return 'yaml'
  if (lang === 'md' || lang === 'mkd') return 'markdown'
  if (lang === 'htm') return 'html'
  if (lang === 'c++' || lang === 'cxx' || lang === 'h' || lang === 'hpp') return 'cpp'
  if (hljs.getLanguage(lang)) return lang
  return ''
}

function escapeHtmlText(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/** Highlight source text to HTML spans; falls back to escaped plain text. */
export function highlightCodeToHtml(source, language) {
  const code = String(source || '')
  if (!code) return ''
  const lang = hljsLanguage(language)
  try {
    if (lang) {
      return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value
    }
    return hljs.highlightAuto(code).value
  } catch {
    return escapeHtmlText(code)
  }
}

/** Re-apply syntax highlight on a live <code> element (e.g. after format toggle). */
export function applyCodeHighlight(codeElement, language) {
  if (!codeElement) return
  const text = codeElement.textContent || ''
  const lang = language || codeElement.closest('.md-code-block')?.getAttribute('data-code-language') || ''
  codeElement.innerHTML = highlightCodeToHtml(text, lang)
  codeElement.classList.add('hljs')
  if (lang) {
    const mapped = hljsLanguage(lang) || lang
    codeElement.classList.add(`language-${mapped}`)
  }
}

function escapeAttr(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function escapeMdCell(val) {
  return String(val ?? '')
    .replace(/\|/g, '\\|')
    .replace(/\n/g, ' ')
    .trim()
}

function stripTags(html) {
  return String(html || '').replace(/<[^>]+>/g, '').trim()
}

function getAttr(attrs, name) {
  const re = new RegExp(`\\b${name}="([^"]*)"`, 'i')
  const m = re.exec(attrs || '')
  return m ? m[1] : ''
}

function decodeCodeSample(body) {
  return String(body || '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
}

function codeLanguage(attrs, body = '') {
  const cls = getAttr(attrs, 'class')
  const match = /(?:^|\s)language-([^\s]+)/i.exec(cls)
  if (match?.[1]) return match[1].toLowerCase()
  const sample = decodeCodeSample(body).trim()
  if (/^(?:(?:--[^\n]*|\/\*[\s\S]*?\*\/)\s*)*(?:WITH|SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b/i.test(sample)) {
    return 'sql'
  }
  if (/^[\[{]/.test(sample)) {
    try {
      JSON.parse(sample)
      return 'json'
    } catch {
      // Keep an unknown language when the block only resembles JSON.
    }
  }
  return ''
}

function canFormatCode(language) {
  return language === 'json' || language === 'jsonc' || SQL_LANGUAGES.has(language)
}

/** Hide model self-talk before the final line-level FINAL marker, including history. */
export function extractFinalDisplayContent(text) {
  const source = String(text || '')
  const marker = /^\s*FINAL\s*[:：]\s*/gim
  let last = null
  let match
  while ((match = marker.exec(source)) !== null) last = match
  return last ? source.slice(last.index + last[0].length).trim() : source
}

/** Format code shown in a Markdown code block. */
export function formatCodeSnippet(source, language) {
  const lang = String(language || '').toLowerCase()
  const code = String(source || '').trim()
  if (!code) return code
  if (lang === 'json' || lang === 'jsonc') {
    return JSON.stringify(JSON.parse(code), null, 2)
  }
  if (SQL_LANGUAGES.has(lang)) {
    const dialect = {
      postgres: 'postgresql',
      clickhouse: 'sql',
      tsql: 'transactsql',
    }[lang] || lang
    return formatSql(code, {
      language: dialect,
      keywordCase: 'upper',
      tabWidth: 2,
      linesBetweenQueries: 1,
    }).trim()
  }
  throw new Error('当前代码类型暂不支持格式化')
}

const originalCodeByBlock = new WeakMap()

/** Toggle a code block between its exact original text and formatted text. */
export function toggleCodeSnippet(block, codeElement) {
  if (!block || !codeElement) throw new Error('未找到代码块')
  const button = block.querySelector('[data-code-action="format"]')
  const language = block.getAttribute('data-code-language') || ''
  if (originalCodeByBlock.has(block)) {
    codeElement.textContent = originalCodeByBlock.get(block)
    originalCodeByBlock.delete(block)
    block.removeAttribute('data-code-formatted')
    if (button) {
      button.title = '格式化代码'
      button.setAttribute('aria-label', '格式化代码')
    }
    applyCodeHighlight(codeElement, language)
    return { formatted: false, code: codeElement.textContent || '' }
  }
  const original = codeElement.textContent || ''
  const formatted = formatCodeSnippet(
    original,
    language,
  )
  originalCodeByBlock.set(block, original)
  codeElement.textContent = formatted
  block.setAttribute('data-code-formatted', 'true')
  if (button) {
    button.title = '恢复原始代码'
    button.setAttribute('aria-label', '恢复原始代码')
  }
  applyCodeHighlight(codeElement, language)
  return { formatted: true, code: formatted }
}

/** Add a compact toolbar to fenced Markdown code blocks. */
export function enhanceCodeBlocks(html) {
  return String(html || '').replace(CODE_BLOCK_RE, (full, attrs, body) => {
    const language = codeLanguage(attrs, body)
    const label = language ? language.toUpperCase() : 'CODE'
    const plain = decodeCodeSample(body)
    const highlighted = highlightCodeToHtml(plain, language)
    const langClass = language
      ? `language-${escapeAttr(hljsLanguage(language) || language)}`
      : ''
    const formatButton = canFormatCode(language)
      ? (
        `<button type="button" class="md-code-action" data-code-action="format" ` +
        `title="格式化代码" aria-label="格式化代码"><span aria-hidden="true">↹</span></button>`
      )
      : ''
    return (
      `<div class="md-code-block" data-code-language="${escapeAttr(language)}">` +
      `<div class="md-code-toolbar">` +
      `<span class="md-code-language">${escapeAttr(label)}</span>` +
      `<span class="md-code-actions">${formatButton}` +
      `<button type="button" class="md-code-action" data-code-action="copy" ` +
      `title="复制代码" aria-label="复制代码"><span aria-hidden="true">⧉</span></button>` +
      `</span></div>` +
      `<pre><code class="hljs ${langClass}">${highlighted}</code></pre>` +
      `</div>`
    )
  })
}

function setAttr(attrs, name, value) {
  const re = new RegExp(`\\b${name}="[^"]*"`, 'i')
  const pair = `${name}="${value}"`
  if (re.test(attrs || '')) return String(attrs).replace(re, pair)
  return `${String(attrs || '').trim()} ${pair}`.trim()
}

function hasDeliverableAttachment(html) {
  const s = String(html || '')
  if (/class="[^"]*wp-download[^"]*"/i.test(s)) return true
  if (/href="[^"]*\.(?:pdf|xlsx|xlsm|xls|csv|sql)"/i.test(s)) return true
  if (/>(?:📥\s*)?[^<]*\.(?:pdf|xlsx|xlsm|xls|csv|sql)</i.test(s)) return true
  if (/<code>[^<]*\.(?:pdf|xlsx|xlsm|xls|csv|sql)<\/code>/i.test(s)) return true
  return false
}

function tableDataRows(tableHtml) {
  const rows = String(tableHtml || '').match(/<tr[\s\S]*?<\/tr>/gi) || []
  const hasTh = /<th[\s\S]*?<\/th>/i.test(tableHtml)
  return Math.max(0, rows.length - (hasTh ? 1 : 0))
}

function wrapTableOnly(tableHtml) {
  return `<div class="md-table-wrap">${tableHtml}</div>`
}

function extractRowList(data) {
  if (Array.isArray(data) && data.length && typeof data[0] === 'object' && data[0]) {
    return data
  }
  if (data && typeof data === 'object') {
    for (const key of ['data', 'items', 'records', 'result', 'rows']) {
      const val = data[key]
      if (Array.isArray(val) && val.length && typeof val[0] === 'object' && val[0]) {
        return val
      }
    }
  }
  return null
}

function rowsToMarkdownTable(rows, maxRows = 30) {
  if (!rows?.length) return ''
  const cols = Object.keys(rows[0]).slice(0, 10)
  if (!cols.length) return ''
  const header = `| ${cols.map(escapeMdCell).join(' | ')} |`
  const sep = `| ${cols.map(() => '---').join(' | ')} |`
  const body = rows.slice(0, maxRows).map((row) =>
    `| ${cols.map((c) => escapeMdCell(row[c])).join(' | ')} |`,
  )
  let md = [header, sep, ...body].join('\n')
  if (rows.length > maxRows) {
    md += `\n\n*共 ${rows.length} 条，展示前 ${maxRows} 条*`
  }
  return md
}

/** Wrap bare file paths in backticks so linkifyWorkplaceFiles turns them into download chips. */
export function linkifyBareFilePaths(text) {
  return String(text || '').replace(BARE_FILE_TOKEN_RE, (full, code, bare) => {
    if (code) return code
    return `\`${bare}\``
  })
}

/** Convert JSON row-array fences into GFM tables before marked.parse */
export function prepareMarkdownForPreview(text) {
  const src = String(text || '')
  if (!src.trim()) return src
  const withTables = src.replace(JSON_FENCE_RE, (full, body) => {
    try {
      const data = JSON.parse(String(body || '').trim())
      const rows = extractRowList(data)
      if (!rows) return full
      const table = rowsToMarkdownTable(rows, 30)
      if (!table) return full
      return `### 查询结果\n\n${table}`
    } catch {
      return full
    }
  })
  return linkifyBareFilePaths(withTables)
}

export function linkifyWorkplaceFiles(html) {
  return String(html || '').replace(CODE_FILE_RE, (_m, rawPath) => {
    const path = String(rawPath || '').trim().replace(/^\/+/, '')
    if (!path || path.includes('..') || /[<>"']/.test(path)) {
      return `<code>${escapeAttr(rawPath)}</code>`
    }
    const name = path.split('/').pop() || path
    if (/\.sql$/i.test(path)) {
      return sqlInlineBlock(path, name)
    }
    return (
      `<a href="#" class="wp-download md-file-chip" data-wp-path="${escapeAttr(path)}" ` +
      `title="下载 ${escapeAttr(name)}">` +
      `<span class="md-file-chip-icon" aria-hidden="true">${fileIconFor(name)}</span>` +
      `<span class="md-file-chip-name">${escapeAttr(name)}</span>` +
      `</a>`
    )
  })
}

/** Render a .sql file path as an inline code box (fetched + filled by the host page). */
function sqlInlineBlock(path, name) {
  return (
    `<details class="md-sql-inline" open data-wp-path="${escapeAttr(path)}" data-sql-loaded="false">` +
    `<summary class="md-sql-inline-summary">` +
    `<span class="md-file-chip-icon" aria-hidden="true">SQL</span>` +
    `<code class="md-sql-inline-name">${escapeHtmlText(name)}</code>` +
    `</summary>` +
    `<div class="md-sql-inline-body md-code-block" data-code-language="sql">` +
    `<pre class="md-sql-inline-pre"><code class="hljs language-sql md-sql-inline-code">加载 SQL…</code></pre>` +
    `</div>` +
    `<a href="#" class="wp-download md-file-chip md-sql-inline-download" ` +
    `data-wp-path="${escapeAttr(path)}" title="下载 ${escapeAttr(name)}">下载</a>` +
    `</details>`
  )
}

function fileIconFor(name) {
  const lower = String(name || '').toLowerCase()
  if (lower.endsWith('.pdf')) return '📄'
  if (/\.(xlsx|xlsm|xls|csv)$/.test(lower)) return '📊'
  if (lower.endsWith('.sql')) return 'SQL'
  return '📥'
}

function wrapFileChip(attrs, innerHtml) {
  const text = stripTags(innerHtml)
  const href = getAttr(attrs, 'href')
  const path = getAttr(attrs, 'data-wp-path')
  const fromHref = decodeURIComponent((href.split('/').pop() || '').split('?')[0] || '')
  const name =
    (path && path.split('/').pop()) ||
    (FILE_EXT_RE.test(fromHref) ? fromHref : '') ||
    (FILE_EXT_RE.test(text) ? text.replace(/^[📥📄📊]\s*/, '') : '') ||
    text.replace(/^[📥📄📊]\s*/, '') ||
    'download'

  const isWp = /wp-download/i.test(attrs) || !!path
  let cls = getAttr(attrs, 'class')
  const parts = new Set(cls.split(/\s+/).filter(Boolean))
  parts.add('md-file-chip')
  if (isWp) parts.add('wp-download')
  cls = [...parts].join(' ')

  let next = setAttr(attrs, 'class', cls)
  if (!getAttr(next, 'title')) {
    next = setAttr(next, 'title', `下载 ${name}`)
  }

  const icon = fileIconFor(name)
  return (
    `<a ${next}>` +
    `<span class="md-file-chip-icon" aria-hidden="true">${icon}</span>` +
    `<span class="md-file-chip-name">${escapeAttr(name)}</span>` +
    `</a>`
  )
}

export function chipifyFileLinks(html) {
  return String(html || '').replace(ANCHOR_RE, (full, attrs, inner) => {
    if (/\bmd-file-chip\b/.test(attrs)) return full
    const text = stripTags(inner)
    const href = getAttr(attrs, 'href')
    const path = getAttr(attrs, 'data-wp-path')
    const looksLikeFile =
      /wp-download/i.test(attrs) ||
      FILE_EXT_RE.test(path) ||
      FILE_EXT_RE.test(href) ||
      FILE_EXT_RE.test(text)
    if (!looksLikeFile) return full
    return wrapFileChip(attrs, inner)
  })
}

/** Wrap tables; fold only when the message has a file attachment and rows > 8. */
export function wrapAndFoldTables(html) {
  const src = String(html || '')
  if (!/<table[\s\S]*?<\/table>/i.test(src)) return src
  if (/\bmd-query-result\b/.test(src)) return src
  const hasAttach = hasDeliverableAttachment(src)
  return src.replace(TABLE_RE, (table) => {
    const wrapped = wrapTableOnly(table)
    const rows = tableDataRows(table)
    if (hasAttach && rows > 8) {
      return (
        `<details class="md-table-fold">` +
        `<summary>查看明细表格</summary>` +
        wrapped +
        `</details>`
      )
    }
    return wrapped
  })
}

/** Query result: keep tables expanded in a preview card */
export function focusQueryResultHtml(html) {
  const src = String(html || '')
  if (/\bmd-query-result\b/.test(src)) return src
  if (!/查询结果/.test(src) && !/<table[\s\S]*?<\/table>/i.test(src)) return src
  const wrapped = wrapAndFoldTables(src)
  return `<div class="md-query-result">${wrapped}</div>`
}

/** Full pipeline after marked.parse */
export function enhanceMarkdownHtml(html) {
  let out = linkifyWorkplaceFiles(html)
  out = chipifyFileLinks(out)
  if (/查询结果/.test(out) || /<table[\s\S]*?<\/table>/i.test(out)) {
    out = focusQueryResultHtml(out)
  } else {
    out = wrapAndFoldTables(out)
  }
  out = enhanceCodeBlocks(out)
  return out
}
