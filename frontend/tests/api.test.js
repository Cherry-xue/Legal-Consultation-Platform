import { afterEach, beforeEach, test } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { api, chatStream } from '../src/api.js'
import { useAuth } from '../src/store.js'

const originalFetch = globalThis.fetch
beforeEach(() => { setActivePinia(createPinia()); useAuth().setUser({ id: 'alice' }) })
afterEach(() => { globalThis.fetch = originalFetch })

test('a late unauthorized response cannot log out the next account', async () => {
  let finish
  globalThis.fetch = () => new Promise(resolve => { finish = resolve })
  const pending = api('/auth/me')
  useAuth().setUser({ id: 'bob' })
  finish(new Response(JSON.stringify({ message: 'expired' }), { status: 401 }))
  await assert.rejects(pending, /expired/)
  assert.equal(useAuth().user.id, 'bob')
})

test('an expired current session is cleared', async () => {
  globalThis.fetch = async () => new Response('{}', { status: 401 })
  await assert.rejects(api('/auth/me'))
  assert.equal(useAuth().user, null)
})

test('requests use cookie credentials without a manual authorization header', async () => {
  let observed
  globalThis.fetch = async (_, options) => { observed = options; return new Response('{}') }
  await api('/memories', { method: 'POST', body: JSON.stringify({ content: 'test' }) })
  assert.equal(observed.credentials, 'same-origin')
  assert.equal(observed.headers.Authorization, undefined)
})

test('SSE handles split UTF-8 bytes and delivers a complete final event', async () => {
  const bytes = new TextEncoder().encode('event: result\ndata: {"text":"引用原文"}\n\nevent: done\ndata: {"status":"completed"}\n\n')
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c) {
    for (let i = 0; i < bytes.length; i += 2) c.enqueue(bytes.slice(i, i + 2))
    c.close()
  } }))
  const received = []
  await chatStream('contract', 'test', undefined, (name, value) => received.push([name, value]), 'conversation')
  assert.equal(received[0][1].text, '引用原文')
  assert.equal(received[1][0], 'done')
})

test('an old account stream cannot publish results to the next account', async () => {
  let finish
  globalThis.fetch = () => new Promise(resolve => { finish = resolve })
  const received = []
  const pending = chatStream('contract', 'test', undefined, (...args) => received.push(args), 'old-conversation')
  useAuth().setUser({ id: 'bob' })
  finish(new Response('event: result\ndata: {"private":"alice"}\n\nevent: done\ndata: {"status":"completed"}\n\n'))
  await pending
  assert.deepEqual(received, [])
  assert.equal(useAuth().user.id, 'bob')
})
