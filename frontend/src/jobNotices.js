import { computed, ref, watch } from 'vue'

export function useJobNotices(jobs, owner, visible, storage) {
  const seen = ref(new Set())
  const key = () => `legal-job-notices:${owner.value}`
  function save() {
    if (!owner.value) return
    try { storage?.setItem(key(), JSON.stringify([...seen.value])) } catch { /* Storage can be disabled. */ }
  }
  function acknowledge() {
    const next = new Set(seen.value)
    for (const job of jobs.value) {
      if (['completed', 'failed'].includes(job.status)) next.add(`${job.id}:${job.status}`)
    }
    seen.value = next
    save()
  }
  function forget(id) {
    seen.value = new Set([...seen.value].filter(value => !value.startsWith(`${id}:`)))
    save()
  }
  watch(owner, () => {
    try {
      const value = JSON.parse(storage?.getItem(key()) || '[]')
      seen.value = new Set(Array.isArray(value) ? value.filter(x => typeof x === 'string') : [])
    } catch { seen.value = new Set() }
  }, { immediate: true, flush: 'sync' })
  watch([jobs, visible], () => {
    const active = new Set(jobs.value.filter(j => ['queued', 'running'].includes(j.status)).map(j => j.id))
    seen.value = new Set([...seen.value].filter(value => !active.has(value.split(':')[0])))
    if (visible.value) acknowledge()
    else save()
  }, { flush: 'sync' })
  const counts = computed(() => ({
    completed: jobs.value.filter(j => j.status === 'completed' && !seen.value.has(`${j.id}:completed`)).length,
    failed: jobs.value.filter(j => j.status === 'failed' && !seen.value.has(`${j.id}:failed`)).length,
    pending: jobs.value.filter(j => ['queued', 'running'].includes(j.status)).length,
  }))
  return { counts, acknowledge, forget }
}
