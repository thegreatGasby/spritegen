<script setup>
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import PreviewStage from './PreviewStage.vue'

const props = defineProps({
  result: Object,   // { kind:'batch', items:[], masters:[], total, done }
  job: Object,
  busy: Boolean,
})

const total = computed(() => props.result?.total || 0)
const done = computed(() => props.result?.done || 0)
const pct = computed(() => (total.value ? Math.round((done.value / total.value) * 100) : 0))

const preview = ref(null)   // the action item currently being played

function open(it) { if (it && !it.error && it.sheet) preview.value = it }
function close() { preview.value = null }
function onKey(e) { if (e.key === 'Escape') close() }

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="panel batch">
    <div class="head">
      <div class="lib-title">批量结果 <span class="tag">{{ done }}/{{ total }}</span></div>
      <div v-if="busy" class="stage-line">
        {{ job?.stage || '生成中' }} · {{ pct }}%
        <div class="progress"><i :style="{ width: pct + '%' }"></i></div>
      </div>
    </div>

    <div v-if="result.masters.length" class="masters">
      <div class="lib-title">视角 master（同视角动作共用）</div>
      <div class="masters-row">
        <figure v-for="m in result.masters" :key="m.view">
          <img :src="m.master" alt="" />
          <figcaption>{{ m.view }}</figcaption>
        </figure>
      </div>
    </div>

    <div class="grid">
      <figure v-for="(it, i) in result.items" :key="i"
              :class="{ clickable: !it.error }"
              @click="open(it)">
        <template v-if="it.error">
          <div class="cell-err">{{ it.view || '?' }} · {{ it.action || '?' }}<br>失败：{{ it.error }}</div>
        </template>
        <template v-else>
          <img :src="it.sheet" alt="" />
          <figcaption>
            <span class="tag">{{ it.view }} · {{ it.action }}</span>
            <span class="muted"> {{ it.frameCount }}f · {{ it.mode === 'video' ? '视频' : '图片' }}</span>
          </figcaption>
          <div class="dl" @click.stop>
            <button class="ghost" @click="open(it)">预览</button>
            <a :href="it.sheet" download><button class="ghost">sheet</button></a>
            <a :href="it.preview" download><button class="ghost">gif</button></a>
          </div>
        </template>
      </figure>
    </div>

    <p v-if="busy && done < total" class="hint">结果逐步出现；同一视角的动作复用同一张 master（视频模式才保证一致）。</p>

    <div v-if="preview" class="overlay" @click.self="close">
      <div class="viewer">
        <div class="viewer-head">
          <strong>{{ preview.view }} · {{ preview.action }}</strong>
          <span class="muted">{{ preview.frameCount }} 帧 · {{ preview.frameWidth }}px · {{ preview.mode === 'video' ? '视频' : '图片' }}</span>
          <button class="ghost close" @click="close">✕</button>
        </div>
        <PreviewStage :result="preview" />
        <div class="dl">
          <a :href="preview.sheet" download><button>下载 sheet.png</button></a>
          <a :href="preview.preview" download><button>下载 preview.gif</button></a>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.batch { display: flex; flex-direction: column; gap: 12px; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.stage-line { color: var(--muted); font-size: 12px; min-width: 200px; flex: 1; }
.progress { height: 6px; margin-top: 4px; background: var(--panel-2, #26262f); border-radius: 999px; overflow: hidden; }
.progress i { display: block; height: 100%; background: var(--accent, #e07); transition: width .3s; }
.masters-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 4px; }
.masters-row img { height: 72px; image-rendering: pixelated; border-radius: 6px; background: var(--panel-2); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
figure { margin: 0; background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 6px; }
figure.clickable { cursor: pointer; transition: border-color .15s; }
figure.clickable:hover { border-color: var(--accent, #e07); }
.grid img { width: 100%; image-rendering: pixelated; }
figcaption { font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
.cell-err { font-size: 11px; color: var(--danger); padding: 8px; }
.dl { display: flex; gap: 6px; margin-top: 6px; }
.dl button { font-size: 11px; padding: 2px 8px; }
a { text-decoration: none; }
.hint { color: var(--muted); font-size: 11px; margin: 0; }

.overlay {
  position: fixed; inset: 0; z-index: 50; display: flex;
  align-items: center; justify-content: center;
  background: rgba(0, 0, 0, .6); padding: 24px;
}
.viewer {
  display: flex; flex-direction: column; align-items: center; gap: 12px;
  background: var(--panel); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 16px; max-width: 90vw; max-height: 90vh;
}
.viewer-head { display: flex; align-items: center; gap: 10px; width: 100%; }
.viewer-head .close { margin-left: auto; }
</style>
