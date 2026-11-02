<script setup>
import { computed } from 'vue'
const props = defineProps({ code: { type: String, required: true } })
const paths = {
  contract: ['M5 3h14v18H5z', 'M8 8h8M8 12h8M8 16h8'],
  labor: ['M4 4h16v16H4z', 'M4 9h16M4 14h16M9 4v16M14 4v16'],
  family: ['m12 3 9 9-9 9-9-9z'],
  consumer: ['m12 3 9 9-9 9-9-9z', 'm12 7 5 5-5 5-5-5z'],
  procedure: ['m3 8 9-5 9 5H3z', 'M5 11v7M10 11v7M14 11v7M19 11v7M3 21h18'],
  collaboration: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18', 'M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10'],
}
const strokes = computed(() => paths[props.code] || paths.contract)
</script>

<template>
  <svg class="agent-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path v-for="(path, i) in strokes" :key="i" :d="path"/></svg>
</template>
