import { test } from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, ref } from 'vue'
import { useJobNotices } from '../src/jobNotices.js'

function fixture(storage = new Map(), name = 'alice') {
  const scope = effectScope(), jobs = ref([]), owner = ref(name), visible = ref(false)
  const notices = scope.run(() => useJobNotices(jobs, owner, visible, {
    getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value),
  }))
  return { scope, jobs, owner, visible, ...notices }
}

test('terminal counters clear on viewing while pending remains until processing finishes', () => {
  const f = fixture()
  f.jobs.value = [{ id: 'a', status: 'completed' }, { id: 'b', status: 'failed' }, { id: 'c', status: 'queued' }, { id: 'd', status: 'running' }]
  assert.deepEqual(f.counts.value, { completed: 1, failed: 1, pending: 2 })
  f.visible.value = true
  assert.deepEqual(f.counts.value, { completed: 0, failed: 0, pending: 2 })
  f.visible.value = false
  f.jobs.value = [...f.jobs.value.slice(0, 2), { id: 'c', status: 'completed' }, { id: 'd', status: 'failed' }]
  assert.deepEqual(f.counts.value, { completed: 1, failed: 1, pending: 0 })
  f.scope.stop()
})

test('read receipts persist across remounts and are isolated by account', () => {
  const storage = new Map(), first = fixture(storage)
  const rows = [{ id: 'a', status: 'completed' }]
  first.jobs.value = rows
  first.visible.value = true
  first.scope.stop()
  const next = fixture(storage)
  next.jobs.value = rows
  assert.equal(next.counts.value.completed, 0)
  next.owner.value = 'bob'
  assert.equal(next.counts.value.completed, 1)
  next.scope.stop()
})

test('a retried task can notify again and updates viewed in the drawer stay acknowledged', () => {
  const f = fixture()
  f.jobs.value = [{ id: 'a', status: 'failed' }]
  f.visible.value = true
  f.visible.value = false
  f.forget('a')
  f.jobs.value = [{ id: 'a', status: 'queued' }]
  assert.equal(f.counts.value.pending, 1)
  f.jobs.value = [{ id: 'a', status: 'failed' }]
  assert.equal(f.counts.value.failed, 1)
  f.visible.value = true
  f.jobs.value = [{ id: 'a', status: 'failed' }, { id: 'b', status: 'completed' }]
  f.visible.value = false
  assert.deepEqual(f.counts.value, { completed: 0, failed: 0, pending: 0 })
  f.scope.stop()
})
