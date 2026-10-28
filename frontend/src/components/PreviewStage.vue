<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'

const props = defineProps({ result: Object })

const canvas = ref(null)
let img = null
let timer = null
let idx = 0

const CHK = 8

function draw() {
  const c = canvas.value
  if (!c || !img || !props.result) return
  const r = props.result
  const w = r.frameWidth, h = r.frameHeight
  const scale = Math.max(1, Math.floor(280 / h))
  c.width = w * scale
  c.height = h * scale
  const ctx = c.getContext('2d')
  ctx.imageSmoothingEnabled = false
  for (let y = 0; y < c.height; y += CHK) {
    for (let x = 0; x < c.width; x += CHK) {
      ctx.fillStyle = ((x / CHK + y / CHK) % 2) ? '#26262f' : '#1e1e26'
      ctx.fillRect(x, y, CHK, CHK)
    }
  }
  const col = idx % r.columns
  const row = Math.floor(idx / r.columns)
  ctx.drawImage(img, col * w, row * h, w, h, 0, 0, c.width, c.height)
}

function stop() {
  if (timer) clearInterval(timer)
  timer = null
}

function start() {
  stop()
  const r = props.result
  if (!r) return
  img = new Image()
  img.onload = () => {
    idx = 0
    draw()
    const fps = Math.min(Math.max(r.fps || 8, 1), 30)
    timer = setInterval(() => {
      idx = (idx + 1) % r.frameCount
      draw()
    }, 1000 / fps)
  }
  img.src = r.sheet
}

watch(() => props.result, start, { immediate: true })
onBeforeUnmount(stop)
</script>

<template>
  <canvas ref="canvas" class="stage-canvas"></canvas>
</template>

<style scoped>
.stage-canvas {
  image-rendering: pixelated;
  border-radius: 8px;
  max-width: 100%;
}
</style>
