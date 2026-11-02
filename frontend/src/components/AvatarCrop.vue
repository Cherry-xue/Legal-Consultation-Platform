<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { useAuth } from '../store'

const props = defineProps({ file: File, modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'saved'])
const canvas = ref(null), zoom = ref(1), busy = ref(false), error = ref(''), ready = ref(false)
let image = null, url = '', offset = { x: 0, y: 0 }, drag = null
const size = 320
function draw() {
  if (!canvas.value || !image) return
  const ctx = canvas.value.getContext('2d')
  const scale = Math.max(size / image.width, size / image.height) * zoom.value
  const w = image.width * scale, h = image.height * scale
  offset.x = Math.max(-(w - size) / 2, Math.min((w - size) / 2, offset.x))
  offset.y = Math.max(-(h - size) / 2, Math.min((h - size) / 2, offset.y))
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, size, size)
  ctx.drawImage(image, (size - w) / 2 + offset.x, (size - h) / 2 + offset.y, w, h)
}
async function load() {
  error.value = ''; ready.value = false; zoom.value = 1; offset = { x: 0, y: 0 }; image = null
  if (url) URL.revokeObjectURL(url)
  if (!props.file || !props.modelValue) return
  if (props.file.size > 5 * 1024 * 1024) { error.value = '请选择小于 5 MB 的图片。'; return }
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(props.file.type)) { error.value = '请选择 JPG、PNG 或 WebP 图片。'; return }
  url = URL.createObjectURL(props.file)
  const nextImage = new Image()
  const source = url
  nextImage.onload = async () => {
    if (source !== url || !props.modelValue) return
    if (nextImage.width * nextImage.height > 16_000_000) { error.value = '图片分辨率过大，请选择不超过 1600 万像素的图片。'; return }
    image = nextImage; ready.value = true; await nextTick(); draw()
  }
  nextImage.onerror = () => { error.value = '无法读取图片，请重新选择。' }
  nextImage.src = url
}
function start(event) {
  if (!image) return
  canvas.value.setPointerCapture(event.pointerId)
  drag = { x: event.clientX, y: event.clientY, initial: { ...offset } }
}
function move(event) {
  if (!drag) return
  const factor = size / canvas.value.getBoundingClientRect().width
  offset = { x: drag.initial.x + (event.clientX - drag.x) * factor,
    y: drag.initial.y + (event.clientY - drag.y) * factor }
  draw()
}
async function save() {
  if (!image || busy.value) return
  const userId = useAuth().user?.id
  if (!userId) return
  busy.value = true; error.value = ''
  try {
    const blob = await new Promise(resolve => canvas.value.toBlob(resolve, 'image/jpeg', 0.92))
    if (!blob) throw new Error('图片处理失败，请重新选择。')
    const form = new FormData(); form.append('file', blob, 'avatar.jpg')
    const user = await api('/auth/avatar', { method: 'POST', body: form })
    if (useAuth().user?.id !== userId) return
    emit('saved', user); emit('update:modelValue', false)
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
watch(() => [props.file, props.modelValue], load)
watch(zoom, draw)
watch(() => props.modelValue, value => { if (!value && url) { URL.revokeObjectURL(url); url = ''; image = null } })
onBeforeUnmount(() => { if (url) URL.revokeObjectURL(url) })
</script>

<template>
  <el-dialog :model-value="modelValue" title="裁剪头像" width="440px" @update:model-value="emit('update:modelValue', $event)" @opened="draw">
    <p class="dialog-note">拖动图片调整位置，缩放至合适大小。</p>
    <div class="crop-stage"><canvas ref="canvas" width="320" height="320" aria-label="头像裁剪区域" @pointerdown="start" @pointermove="move" @pointerup="drag = null" @pointercancel="drag = null"/><div class="crop-circle"></div></div>
    <label class="crop-zoom">缩放<input v-model.number="zoom" type="range" min="1" max="4" step="0.01" aria-label="头像缩放"></label>
    <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
    <div class="dialog-actions"><button class="secondary-button" :disabled="busy" @click="emit('update:modelValue', false)">取消</button><button class="primary-button" :disabled="busy || !ready || !!error" @click="save">{{ busy ? '正在保存…' : '保存头像' }}</button></div>
  </el-dialog>
</template>
