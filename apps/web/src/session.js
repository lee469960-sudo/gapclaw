import { getCgi } from './api'

const SHELL_KEY = 'gap_shell'
const ROUTES_KEY = 'gap_allowed_routes'
const ROLES_KEY = 'gap_roles'

let memoryShell = null
let inflight = null

function persistShell(data) {
  memoryShell = data
  sessionStorage.setItem(SHELL_KEY, JSON.stringify(data))
  sessionStorage.setItem(ROUTES_KEY, JSON.stringify((data.menus || []).map((m) => m.route)))
  sessionStorage.setItem(ROLES_KEY, JSON.stringify(data.roles || []))
}

/** Read cached shell data without network I/O. */
export function getShell() {
  if (memoryShell) return memoryShell
  try {
    const raw = sessionStorage.getItem(SHELL_KEY)
    if (raw) {
      memoryShell = JSON.parse(raw)
      return memoryShell
    }
  } catch {
    /* ignore */
  }
  return null
}

export function patchShellSite(site) {
  if (!site) return
  const shell = getShell()
  if (!shell) return
  persistShell({
    ...shell,
    site_name: site.site_name ?? shell.site_name,
    site_logo: site.site_logo ?? shell.site_logo,
    footer: site.footer ?? shell.footer,
  })
}

/** Fetch shell once; concurrent callers share the same in-flight request. */
export async function fetchShell(force = false) {
  if (!force) {
    const cached = getShell()
    if (cached) return cached
  }
  if (inflight) return inflight

  inflight = getCgi('/render.cgi')
    .then((res) => {
      const data = res.data || {}
      persistShell(data)
      inflight = null
      return data
    })
    .catch((err) => {
      inflight = null
      throw err
    })

  return inflight
}

export function clearShell() {
  memoryShell = null
  inflight = null
  sessionStorage.removeItem(SHELL_KEY)
  sessionStorage.removeItem(ROUTES_KEY)
  sessionStorage.removeItem(ROLES_KEY)
}

export function getCurrentUsername() {
  return getShell()?.username || ''
}
