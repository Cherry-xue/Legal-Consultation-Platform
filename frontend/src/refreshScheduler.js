// Schedule the next refresh after completion, and coalesce explicit refreshes
// received during a request into one follow-up. Slow responses cannot overlap.
export function createRefreshScheduler({ load, isActive, delay = 2000, schedule = setTimeout, cancel = clearTimeout }) {
  let enabled = false, timer, inFlight, repeat = false
  function clearTimer() { if (timer !== undefined) cancel(timer); timer = undefined }
  function plan() {
    clearTimer()
    if (enabled) timer = schedule(() => { timer = undefined; refresh().catch(() => {}) }, delay)
  }
  function refresh() {
    clearTimer()
    if (inFlight) { repeat = true; return inFlight }
    inFlight = Promise.resolve().then(async () => {
      do {
        repeat = false
        if (isActive()) await load()
      } while (repeat && isActive())
    }).finally(() => { inFlight = undefined; plan() })
    return inFlight
  }
  function wake() { if (enabled) refresh().catch(() => {}) }
  return {
    refresh, wake,
    start() { enabled = true; wake() },
    stop() { enabled = false; repeat = false; clearTimer() },
  }
}
