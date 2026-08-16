import axios from 'axios'
import { ElMessage } from 'element-plus'

const api = axios.create({
  withCredentials: true,
  baseURL: import.meta.env.VITE_API_BASE || '',
})

api.interceptors.response.use(
  (res) => {
    if (res.status === 304) {
      return {
        code: 0,
        msg: 'not modified',
        data: { notModified: true },
        etag: res.headers?.etag || res.headers?.ETag || '',
      }
    }
    if (res.config.responseType === 'blob') return res
    const data = res.data
    if (data && typeof data.code === 'number' && data.code !== 0) {
      ElMessage.error(data.msg || '请求失败')
      return Promise.reject(data)
    }
    if (data && typeof data === 'object') {
      const etag = res.headers?.etag || res.headers?.ETag
      if (etag) data.etag = etag
    }
    return data
  },
  (err) => {
    if (err.response?.status === 401) {
      window.location.href = '/login'
    }
    ElMessage.error(err.response?.data?.detail || err.message || '网络错误')
    return Promise.reject(err)
  }
)

export async function postCgi(path, body = {}) {
  return api.post(path, body)
}

export async function getCgi(path, params = {}, options = {}) {
  const { headers, validateStatus, ...rest } = options || {}
  return api.get(path, {
    params,
    headers,
    validateStatus,
    ...rest,
  })
}

/** check_status with If-None-Match; 304 → notModified without toast */
export async function getCheckStatus(path, params = {}, etag = '') {
  const headers = {}
  if (etag) headers['If-None-Match'] = etag
  return api.get(path, {
    params,
    headers,
    validateStatus: (s) => (s >= 200 && s < 300) || s === 304,
  })
}

export default api
