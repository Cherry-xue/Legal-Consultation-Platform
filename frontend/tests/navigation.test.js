import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as vue from 'vue'
import { createRefreshScheduler } from '../src/refreshScheduler.js'
import { createAgentTasks } from '../src/agentTasks.js'
import { useJobNotices } from '../src/jobNotices.js'

// Exercise the actual App setup and route watcher without a browser or live API.
test('navigation keeps in-flight answers, drafts and previews, and refreshes jobs on agent pages', async () => {
  const events = new Map(), calls = [], streams = new Map(), suggestions = []
  const saved = { window: globalThis.window, document: globalThis.document, sessionStorage: globalThis.sessionStorage }
  globalThis.window = { innerWidth: 1280, addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name) }
  globalThis.document = { hidden: false, addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name) }
  globalThis.sessionStorage = { removeItem() {} }
  const route = vue.reactive({ path: '/agent/contract', fullPath: '/agent/contract', params: { code: 'contract' }, query: {} })
  const auth = vue.reactive({ user: { id: 'alice' }, ready: true, setUser(user) { this.user = user }, clear() { this.user = null } })
  const router = { replace() {}, push() {} }
  const api = async (path, options) => {
    calls.push(path)
    if (path === '/agents') return [{ code: 'contract', name: '合同审查' }, { code: 'labor', name: '劳动争议' }]
    if (path === '/auth/me') return { id: 'alice' }
    if (path === '/rag/recommend-domain') return new Promise(resolve => suggestions.push({ resolve, signal: options.signal }))
    if (path === '/conversations') return { id: JSON.parse(options.body).agent_code + '-conversation' }
    return []
  }
  const chatStream = (code, query, signal, onEvent) => new Promise((resolve, reject) => {
    streams.set(code, { signal, onEvent, resolve })
    signal.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError')))
  })
  const source = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  let script = compileScript(descriptor, { id: 'navigation-test' }).content.replace(/\r\n/g, '\n')
  script = script.replace(/^import .* from ['"]([^'"]+)['"]\r?$/gm, (line, path) => {
    if (path === 'vue') return line.replace(/^import (.*) from .*$/, 'const $1 = bindings.vue')
    if (path === 'vue-router') return 'const { useRoute, useRouter } = bindings'
    if (path === 'element-plus') return 'const ElMessage = {success(){},warning(){}}, ElMessageBox = {}'
    if (path === './api') return 'const { api, chatStream } = bindings'
    if (path === './store') return 'const { useAuth } = bindings'
    if (path === './refreshScheduler.js') return 'const { createRefreshScheduler } = bindings'
    if (path === './agentTasks.js') return 'const { createAgentTasks } = bindings'
    if (path === './jobNotices.js') return 'const { useJobNotices } = bindings'
    return line.replace(/^import (\w+) from .*$/, 'const $1 = {}')
  }).replace('export default', 'return')
  const component = new Function('bindings', script)({ vue, useRoute: () => route, useRouter: () => router, useAuth: () => auth, api, chatStream, createRefreshScheduler, createAgentTasks, useJobNotices })
  component.render = () => null
  const renderer = vue.createRenderer({
    createElement: () => ({}), createComment: () => ({}), createText: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {},
  })
  const app = renderer.createApp(component)
  let instance
  const flush = () => new Promise(resolve => setImmediate(resolve))
  try {
    instance = app.mount({})
    await flush()
    const state = instance.$.setupState
    assert.ok(calls.includes('/rag/jobs'), 'job polling also runs while viewing an agent')
    const firstFile = state.chooseFile(new File(['合同'], 'contract.txt'))
    const secondFile = state.chooseFile(new File(['劳动'], 'labor.txt'))
    assert.equal(suggestions[0].signal.aborted, true)
    suggestions[1].resolve({ domain: 'labor', label: '劳动争议' })
    await secondFile
    assert.equal(state.recommendation.domain, 'labor')
    assert.equal(state.upload.domain, 'general', 'a suggestion must not change the selected domain until accepted')
    state.applyRecommendation()
    assert.equal(state.upload.domain, 'labor')
    suggestions[0].resolve({ domain: 'contract', label: '合同审查' })
    await firstFile
    assert.equal(state.recommendation.domain, 'labor', 'a late response for the old file must not overwrite the new one')
    state.upload.domain = 'family'
    await state.chooseFile(undefined)
    assert.equal(state.upload.domain, 'family', 'manual choice remains authoritative')
    assert.equal(state.recommendation, null)
    state.question = '合同问题'
    const contractRun = state.send()
    await flush()
    state.preview = { preview_id: 'retained-preview', chunks: [] }
    state.question = '合同草稿'
    Object.assign(route, { path: '/agent/labor', fullPath: '/agent/labor', params: { code: 'labor' } })
    await flush()
    assert.equal(streams.get('contract').signal.aborted, false)
    assert.equal(state.sending, false)
    assert.equal(state.question, '')
    assert.equal(state.preview.preview_id, 'retained-preview')
    state.question = '劳动问题'
    const laborRun = state.send()
    await flush()
    streams.get('contract').onEvent('result', { status: 'completed', claims: [{ text: '合同结果' }] })
    streams.get('contract').resolve()
    await contractRun
    assert.equal(state.sending, true, 'finishing contract must not stop labor')
    assert.equal(state.current[0].text, '劳动问题')
    Object.assign(route, { path: '/agent/contract', fullPath: '/agent/contract', params: { code: 'contract' } })
    await flush()
    assert.equal(state.question, '合同草稿')
    assert.equal(state.current[1].result.claims[0].text, '合同结果')
    assert.equal(streams.get('labor').signal.aborted, false)
    const before = calls.filter(path => path === '/rag/jobs').length
    events.get('focus')()
    await flush()
    assert.ok(calls.filter(path => path === '/rag/jobs').length > before)
    app.unmount()
    instance = null
    await laborRun
    assert.equal(streams.get('labor').signal.aborted, true)
    assert.equal(events.size, 0)
  } finally {
    if (instance) app.unmount()
    Object.assign(globalThis, saved)
  }
})
