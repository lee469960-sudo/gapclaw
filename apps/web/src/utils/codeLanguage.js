import { python } from '@codemirror/lang-python'
import { javascript } from '@codemirror/lang-javascript'
import { json } from '@codemirror/lang-json'
import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import { sql } from '@codemirror/lang-sql'
import { xml } from '@codemirror/lang-xml'
import { java } from '@codemirror/lang-java'
import { cpp } from '@codemirror/lang-cpp'
import { php } from '@codemirror/lang-php'
import { markdown } from '@codemirror/lang-markdown'
import { yaml } from '@codemirror/lang-yaml'
import { go } from '@codemirror/lang-go'
import { rust } from '@codemirror/lang-rust'

const EXT_LANG = {
  '.py': 'python',
  '.pyw': 'python',
  '.js': 'javascript',
  '.mjs': 'javascript',
  '.cjs': 'javascript',
  '.ts': 'typescript',
  '.jsx': 'jsx',
  '.tsx': 'tsx',
  '.vue': 'html',
  '.json': 'json',
  '.html': 'html',
  '.htm': 'html',
  '.css': 'css',
  '.scss': 'css',
  '.less': 'css',
  '.sql': 'sql',
  '.xml': 'xml',
  '.java': 'java',
  '.c': 'cpp',
  '.cpp': 'cpp',
  '.cc': 'cpp',
  '.h': 'cpp',
  '.hpp': 'cpp',
  '.cs': 'java',
  '.php': 'php',
  '.go': 'go',
  '.rs': 'rust',
  '.sh': 'shell',
  '.bash': 'shell',
  '.zsh': 'shell',
  '.ps1': 'shell',
  '.bat': 'shell',
  '.yaml': 'yaml',
  '.yml': 'yaml',
  '.toml': 'ini',
  '.ini': 'ini',
  '.cfg': 'ini',
  '.conf': 'ini',
  '.env': 'ini',
  '.rb': 'ruby',
  '.swift': 'swift',
  '.kt': 'kotlin',
  '.kts': 'kotlin',
  '.lua': 'lua',
  '.pl': 'perl',
  '.r': 'r',
  '.dockerfile': 'dockerfile',
}

const BASENAME_LANG = {
  dockerfile: 'dockerfile',
  makefile: 'makefile',
  'cmakelists.txt': 'cpp',
  gemfile: 'ruby',
  rakefile: 'ruby',
}

const CODE_EXTS = new Set(Object.keys(EXT_LANG))

export function isCodeFilename(name) {
  const lower = (name || '').toLowerCase()
  const base = lower.split('/').pop() || lower
  if (BASENAME_LANG[base]) return true
  return [...CODE_EXTS].some((ext) => lower.endsWith(ext))
}

export function languageIdForFilename(name) {
  const lower = (name || '').toLowerCase()
  const base = lower.split('/').pop() || lower
  if (BASENAME_LANG[base]) return BASENAME_LANG[base]
  for (const [ext, lang] of Object.entries(EXT_LANG)) {
    if (lower.endsWith(ext)) return lang
  }
  return 'plaintext'
}

export function languageLabel(langId) {
  const labels = {
    python: 'Python',
    javascript: 'JavaScript',
    typescript: 'TypeScript',
    jsx: 'JSX',
    tsx: 'TSX',
    json: 'JSON',
    html: 'HTML',
    css: 'CSS',
    sql: 'SQL',
    xml: 'XML',
    java: 'Java',
    cpp: 'C/C++',
    php: 'PHP',
    markdown: 'Markdown',
    yaml: 'YAML',
    go: 'Go',
    rust: 'Rust',
    shell: 'Shell',
    ini: 'Config',
    dockerfile: 'Dockerfile',
    makefile: 'Makefile',
    plaintext: 'Plain Text',
  }
  return labels[langId] || langId
}

export function getLanguageExtension(langId) {
  switch (langId) {
    case 'python':
      return python()
    case 'javascript':
      return javascript()
    case 'typescript':
      return javascript({ typescript: true })
    case 'jsx':
      return javascript({ jsx: true })
    case 'tsx':
      return javascript({ jsx: true, typescript: true })
    case 'json':
      return json()
    case 'html':
      return html()
    case 'css':
      return css()
    case 'sql':
      return sql()
    case 'xml':
      return xml()
    case 'java':
    case 'kotlin':
      return java()
    case 'cpp':
      return cpp()
    case 'php':
      return php()
    case 'markdown':
      return markdown()
    case 'yaml':
      return yaml()
    case 'go':
      return go()
    case 'rust':
      return rust()
    default:
      return []
  }
}
