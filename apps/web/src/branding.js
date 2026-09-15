export const DEFAULT_SITE_LOGO = '/statics/site/logo.png'

export function logoUrl(raw) {
  const value = String(raw || '').trim()
  return value || DEFAULT_SITE_LOGO
}

export function applySiteBrand(data = {}) {
  const name = String(data.site_name || '').trim()
  if (name) document.title = name
  const href = logoUrl(data.site_logo)
  let link = document.querySelector("link[rel='icon']")
  if (!link) {
    link = document.createElement('link')
    link.rel = 'icon'
    document.head.appendChild(link)
  }
  link.type = 'image/png'
  if (link.getAttribute('href') !== href) {
    link.setAttribute('href', href)
  }
}

export async function loadPublicBrand() {
  try {
    const res = await fetch('/site-brand.cgi', { credentials: 'include' })
    const data = await res.json()
    if (data.code === 0 && data.data) {
      applySiteBrand(data.data)
      return data.data
    }
  } catch {
    /* keep the default icon */
  }
  applySiteBrand({})
  return null
}
