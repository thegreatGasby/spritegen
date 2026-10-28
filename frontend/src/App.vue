<script setup>
import { ref, computed, onMounted } from 'vue'
import * as api from './api'
import ControlPanel from './components/ControlPanel.vue'
import PreviewStage from './components/PreviewStage.vue'
import CharacterLibrary from './components/CharacterLibrary.vue'
import BatchResults from './components/BatchResults.vue'
import PartsResults from './components/PartsResults.vue'

const presets = ref({ styles: [], views: [], facings: [], actions: [], frames: [8], cells: ['64'], keys: [], rigs: [] })
const form = ref({
  prompt: '', style: 'pixel16', views: ['side'], actions: ['idle'],
  facing: 'right', mode: 'image', frames: 8, cell: '64', key: 'green',
  task: 'sprite', rig: 'humanoid',
})

const busy = ref(false)
const job = ref(null)
const result = ref(null)
const error = ref('')
const characters = ref([])
const activeCharacter = ref(null)
const gallery = ref([])

const stageResult = computed(() => (result.value?.kind === 'action' ? result.value : null))
const batchResult = computed(() => (result.value?.kind === 'batch' ? result.value : null))
const partsResult = computed(() => (result.value?.kind === 'parts' ? result.value : null))

function setError(e) { error.value = e?.message || String(e) }

async function refreshLists() {
  try { characters.value = await api.getCharacters() } catch { /* ignore */ }
  try { gallery.value = await api.getGallery() } catch { /* ignore */ }
}

function pollJob(id) {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const j = await api.getJob(id)
        job.value = j
        if (j.result && j.result.kind === 'batch') result.value = j.result  // progressive
        if (j.status === 'done') return resolve(j.result)
        if (j.status === 'error') {
          if (j.result) result.value = j.result   // e.g. the raw image, to preview
          return reject(new Error(j.error || 'generation failed'))
        }
        setTimeout(tick, 1500)
      } catch (e) { reject(e) }
    }
    tick()
  })
}

async function generateCharacter() {
  if (busy.value || !form.value.prompt) return
  busy.value = true; error.value = ''; result.value = null
  try {
    const { job_id } = await api.createCharacter({
      prompt: form.value.prompt, style: form.value.style,
      view: form.value.views[0], facing: form.value.facing, key_mode: form.value.key,
    })
    const res = await pollJob(job_id)
    result.value = res
    form.value.style = res.fields.style
    form.value.facing = res.fields.facing
    activeCharacter.value = { character_id: res.character_id, master: res.master, fields: res.fields }
    await refreshLists()
  } catch (e) { setError(e) } finally { busy.value = false; job.value = null }
}

async function generateBatch() {
  if (busy.value || !form.value.prompt) return
  if (!form.value.views.length || !form.value.actions.length) return
  busy.value = true; error.value = ''; result.value = null
  try {
    const { job_id } = await api.createBatch({
      prompt: form.value.prompt, views: form.value.views, actions: form.value.actions,
      style: form.value.style, facing: form.value.facing, mode: form.value.mode,
      frames: form.value.frames, cell: form.value.cell, key_mode: form.value.key,
    })
    result.value = await pollJob(job_id)
    await refreshLists()
  } catch (e) { setError(e) } finally { busy.value = false; job.value = null }
}

async function generateParts() {
  if (busy.value || !form.value.prompt) return
  busy.value = true; error.value = ''; result.value = null
  try {
    const { job_id } = await api.createParts({
      prompt: form.value.prompt, rig: form.value.rig, style: form.value.style,
      view: form.value.views[0], facing: form.value.facing, key_mode: form.value.key,
    })
    result.value = await pollJob(job_id)
    await refreshLists()
  } catch (e) { setError(e) } finally { busy.value = false; job.value = null }
}

async function pickCharacter(c) {
  activeCharacter.value = c
  form.value.style = c.fields.style
  form.value.views = [c.fields.view]
  form.value.facing = c.fields.facing
  result.value = { kind: 'character', master: c.master, fields: c.fields, character_id: c.character_id }
}

onMounted(async () => {
  try { presets.value = await api.getPresets() } catch (e) { setError(e) }
  refreshLists()
})
</script>

<template>
  <header>
    <h1>SpriteGen <small>· 本地精灵图工作台（Qwen 后端）</small></h1>
    <span class="tag" v-if="activeCharacter">角色：{{ activeCharacter.fields.subject.slice(0, 24) }}</span>
  </header>

  <main>
    <section class="left">
      <ControlPanel :presets="presets" :form="form" :busy="busy"
                    @generate-batch="generateBatch"
                    @generate-character="generateCharacter"
                    @generate-parts="generateParts" />
      <CharacterLibrary :characters="characters"
                        :active-id="activeCharacter?.character_id"
                        @pick="pickCharacter" />
    </section>

    <section class="right">
      <template v-if="batchResult">
        <BatchResults :result="batchResult" :job="job" :busy="busy" />
        <p v-if="error" class="err">{{ error }}</p>
      </template>

      <template v-else-if="partsResult">
        <PartsResults :result="partsResult" />
        <p v-if="error" class="err">{{ error }}</p>
      </template>

      <div v-else class="panel stage">
        <div v-if="busy" class="waiting">
          <div class="progress"><i :style="{ width: (job?.progress || 0) * 100 + '%' }"></i></div>
          <p>{{ job?.stage || '排队中' }}… 约 1–2 分钟</p>
        </div>

        <template v-else-if="stageResult">
          <PreviewStage :result="stageResult" />
          <div class="meta">
            <span class="tag">{{ stageResult.action }}</span>
            <span class="tag">{{ stageResult.mode === 'video' ? '视频' : '图片' }}</span>
            <span class="tag">{{ stageResult.frameCount }} 帧 · {{ stageResult.columns }}×{{ stageResult.rows }}</span>
            <span class="tag">{{ stageResult.frameWidth }} px</span>
            <span class="tag">{{ stageResult.style }}</span>
          </div>
          <div class="row" style="margin-top:10px">
            <a :href="stageResult.sheet" download><button>下载 sheet.png</button></a>
            <a :href="stageResult.preview" download><button>下载 preview.gif</button></a>
          </div>
          <p v-if="stageResult.qc && Object.keys(stageResult.qc).length" class="qc">
            越界检测：{{ Object.entries(stageResult.qc).map(([k, v]) => k + '=' + v).join(', ') }}
          </p>
        </template>

        <template v-else-if="result && result.kind === 'character'">
          <img class="master" :src="result.master" alt="master" />
          <p class="muted">角色 master 已生成。勾选视角×动作再点「批量生成」即可复用它。</p>
        </template>

        <div v-else class="empty">描述一个角色，选好视角/风格/动作，点生成。</div>

        <p v-if="error" class="err">{{ error }}</p>
      </div>

      <div v-if="gallery.length" class="panel gallery">
        <div class="lib-title">最近生成 <span class="tag">{{ gallery.length }}</span></div>
        <div class="grid">
          <figure v-for="g in gallery" :key="g.job_id">
            <img :src="g.sheet" alt="" />
            <figcaption>{{ g.kind === 'parts' ? '骨骼组件' : `${g.action} · ${g.frames}f` }}</figcaption>
          </figure>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
header {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 14px 20px; border-bottom: 1px solid var(--line);
}
main {
  display: grid; grid-template-columns: 360px 1fr; gap: 16px;
  padding: 16px 20px; align-items: start;
}
@media (max-width: 900px) { main { grid-template-columns: 1fr; } }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px; }
.stage { min-height: 360px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; }
.waiting { width: 100%; text-align: center; }
.waiting p { color: var(--muted); }
.meta { display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; }
.master { max-width: 320px; border-radius: 8px; image-rendering: pixelated; }
.empty, .muted { color: var(--muted); }
.err { color: var(--danger); }
.qc { color: var(--muted); font-size: 11px; }
a { text-decoration: none; }
.gallery { margin-top: 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; }
figure { margin: 0; background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 6px; }
.grid img { width: 100%; image-rendering: pixelated; }
figcaption { font-size: 11px; color: var(--muted); }
</style>
