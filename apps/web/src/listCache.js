import { getCgi } from './api'

const cache = new Map()
const inflight = new Map()

/** GET with in-memory TTL cache; concurrent callers share one request.
 *  Pass ttlMs=0 (or negative) to always bypass cache. */
export async function cachedGetCgi(path, params = {}, ttlMs = 30000) {
  const key = `${path}?${new URLSearchParams(params).toString()}`
  const now = Date.now()
  if (ttlMs > 0) {
    const hit = cache.get(key)
    if (hit && now - hit.at < ttlMs) return hit.data
  } else {
    cache.delete(key)
  }

  if (inflight.has(key)) return inflight.get(key)

  const p = getCgi(path, params)
    .then((res) => {
      cache.set(key, { data: res, at: Date.now() })
      inflight.delete(key)
      return res
    })
    .catch((err) => {
      inflight.delete(key)
      throw err
    })

  inflight.set(key, p)
  return p
}

export function invalidateListCache(pathPrefix) {
  for (const key of cache.keys()) {
    if (key.startsWith(pathPrefix)) cache.delete(key)
  }
  for (const key of inflight.keys()) {
    if (key.startsWith(pathPrefix)) inflight.delete(key)
  }
}
