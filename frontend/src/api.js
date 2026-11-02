import { useAuth } from './store.js'

export async function api(path, options = {}) {
  const requestedUserId = useAuth().user?.id
  const headers = { ...options.headers }
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  const res = await fetch('/api' + path, { ...options, headers, credentials: 'same-origin' })
  if (!res.ok) {
    if (res.status === 401 && path !== '/auth/login' && useAuth().user?.id === requestedUserId) useAuth().clear()
    const error = await res.json().catch(() => ({}))
    const detail = Array.isArray(error.detail) ? error.detail.map(e => e.msg).join('；') : error.detail
    throw new Error(error.message || detail || `请求失败 (${res.status})`)
  }
  return res.json()
}

export async function chatStream(code, query, signal, onEvent, conversationId) {
  const requestedUserId = useAuth().user?.id
  const res = await fetch(`/api/agents/${code}/chat`, { method: 'POST', signal,
    credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, conversation_id: conversationId }) })
  if (!res.ok) {
    if (res.status === 401 && useAuth().user?.id === requestedUserId) useAuth().clear()
    const value = await res.json().catch(() => ({}))
    throw new Error(value.message || `请求失败 (${res.status})`)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = '', finished = false
  function consume() {
    buffer = buffer.replace(/\r\n/g, '\n')
    let pos
    while ((pos = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, pos)
      buffer = buffer.slice(pos + 2)
      let type = 'message', data = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) type = line.slice(6).trim()
        if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
      }
      if (data.length) {
        const value = JSON.parse(data.join('\n'))
        if (type === 'done') finished = true
        if (useAuth().user?.id === requestedUserId) onEvent(type, value)
      }
    }
  }
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      consume()
    }
    buffer += decoder.decode()
    consume()
    if (!finished) throw new Error('连接已中断，结果可能不完整，请重新运行。')
  } finally { reader.releaseLock() }
}
