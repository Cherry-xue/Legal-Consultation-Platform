import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as vue from 'vue'

async function screen(api) {
  const route = vue.reactive({ path: '/register' })
  const auth = { user: null, setUser(user) { this.user = user } }
  const router = { replace(path) { route.path = path } }
  const { descriptor } = parse(await readFile(new URL('../src/components/AuthScreen.vue', import.meta.url), 'utf8'))
  let script = compileScript(descriptor, { id: 'auth-lifecycle-test' }).content.replace(/\r\n/g, '\n')
  script = script.replace(/^import .* from ['"]([^'"]+)['"]$/gm, (line, path) => {
    if (path === 'vue') return line.replace(/^import (.*) from .*$/, 'const $1 = bindings.vue')
    if (path === 'vue-router') return 'const { useRoute, useRouter } = bindings'
    if (path === '../api') return 'const { api } = bindings'
    if (path === '../store') return 'const { useAuth } = bindings'
    throw new Error('Unexpected import')
  }).replace('export default', 'return')
  const component = new Function('bindings', script)({ vue, api, useRoute: () => route, useRouter: () => router, useAuth: () => auth })
  component.render = () => null
  const renderer = vue.createRenderer({ createElement: () => ({}), createComment: () => ({}), createText: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {} })
  const app = renderer.createApp(component), instance = app.mount({})
  return { state: instance.$.setupState, route, auth, close: () => app.unmount() }
}

test('registration shows recovery code once before entry, never storing it in auth state', async () => {
  const screenState = await screen(async () => ({ id: 'user', username: 'alice', recovery_code: 'one-time-test-code' }))
  const { state, auth } = screenState
  try {
    Object.assign(state.form, { username: 'alice', password: 'test-password', confirmation: 'test-password' })
    await state.submit()
    assert.equal(state.recoveryCode, 'one-time-test-code')
    assert.equal(state.form.password, '')
    assert.equal(auth.user, null)
    state.enterWorkspace()
    assert.equal(auth.user.id, 'user')
    assert.equal(auth.user.recovery_code, undefined)
    assert.equal(state.recoveryCode, '')
    assert.equal(state.pendingUser, null)
  } finally { screenState.close() }
})

test('forgot-password sends recovery proof and clears secrets without signing in', async () => {
  const requests = []
  const screenState = await screen(async (path, options) => { requests.push({ path, body: JSON.parse(options.body) }); return { message: 'Password reset' } })
  const { state, route, auth } = screenState
  try {
    route.path = '/forgot-password'; await vue.nextTick()
    Object.assign(state.form, { username: 'alice', password: 'new-password', confirmation: 'new-password', recovery_code: 'test-code' })
    await state.submit(); await vue.nextTick()
    assert.deepEqual(requests, [{ path: '/auth/password/reset', body: { username: 'alice', recovery_code: 'test-code', new_password: 'new-password' } }])
    assert.equal(route.path, '/login'); assert.equal(auth.user, null)
    assert.equal(state.form.password, ''); assert.equal(state.form.recovery_code, '')
    assert.equal(state.success, 'Password reset')
  } finally { screenState.close() }
})
