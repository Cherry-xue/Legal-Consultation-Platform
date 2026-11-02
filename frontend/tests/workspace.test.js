import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRefreshScheduler } from '../src/refreshScheduler.js'
import { createAgentTasks } from '../src/agentTasks.js'

function clock() {
  const timers = new Map()
  let id = 0
  return {
    timers,
    schedule(callback, delay) { timers.set(++id, { callback, delay }); return id },
    cancel(id) { timers.delete(id) },
    fire() { const [id, timer] = timers.entries().next().value; timers.delete(id); timer.callback() },
  }
}
const flush = () => new Promise(resolve => setImmediate(resolve))

test('polling waits for completion and coalesces refreshes during a slow request', async () => {
  const time = clock()
  let release, calls = 0, concurrent = 0, peak = 0
  const sync = createRefreshScheduler({ ...time, isActive: () => true, load: async () => {
    calls++; concurrent++; peak = Math.max(peak, concurrent)
    if (calls === 1) await new Promise(resolve => { release = resolve })
    concurrent--
  } })
  sync.start()
  await flush()
  const first = sync.refresh(), second = sync.refresh()
  assert.equal(first, second)
  assert.equal(time.timers.size, 0)
  release()
  await first
  assert.equal(calls, 2)
  assert.equal(peak, 1)
  assert.equal(time.timers.size, 1)
  assert.equal([...time.timers.values()][0].delay, 2000)
  sync.stop()
  assert.equal(time.timers.size, 0)
})

test('polling continues after failure and wake immediately refreshes on returning', async () => {
  const time = clock()
  let calls = 0
  const sync = createRefreshScheduler({ ...time, isActive: () => true, load: async () => {
    if (++calls === 1) throw new Error('temporary outage')
  } })
  sync.start()
  await flush()
  assert.equal(time.timers.size, 1)
  time.fire()
  await flush()
  assert.equal(calls, 2)
  sync.wake()
  await flush()
  assert.equal(calls, 3)
  sync.stop()
  sync.wake()
  await flush()
  assert.equal(calls, 3)
})

test('a login wakes polling that started without an account; logout stops data loads', async () => {
  const time = clock()
  let loggedIn = false, calls = 0
  const sync = createRefreshScheduler({ ...time, isActive: () => loggedIn, load: async () => { calls++ } })
  sync.start()
  await flush()
  assert.equal(calls, 0)
  loggedIn = true
  sync.wake()
  await flush()
  assert.equal(calls, 1)
  loggedIn = false
  time.fire()
  await flush()
  assert.equal(calls, 1)
  sync.stop()
})

test('agents keep independent requests and stopping one leaves the other working', () => {
  const tasks = createAgentTasks()
  const contract = tasks.start('contract'), labor = tasks.start('labor')
  assert.equal(contract.signal.aborted, false)
  assert.throws(() => tasks.start('contract'), /处理中/)
  tasks.stop('labor')
  assert.equal(labor.signal.aborted, true)
  assert.equal(contract.signal.aborted, false)
  tasks.finish('labor', labor)
  assert.equal(tasks.controllers.contract, contract)
  const nextLabor = tasks.start('labor')
  tasks.finish('labor', labor)
  assert.equal(tasks.controllers.labor, nextLabor)
  tasks.stopAll()
  assert.equal(contract.signal.aborted, true)
  assert.equal(nextLabor.signal.aborted, true)
  assert.deepEqual(Object.keys(tasks.controllers), [])
})
