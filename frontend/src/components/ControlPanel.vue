<script setup>
import { computed } from 'vue'
const props = defineProps({
  presets: Object,
  form: Object,
  busy: Boolean,
})
const emit = defineEmits(['generate-batch', 'generate-character', 'generate-parts'])

const task = computed(() => props.form.task || 'sprite')
const combos = computed(() =>
  (props.form.views?.length || 0) * (props.form.actions?.length || 0))

function toggle(list, id) {
  const i = list.indexOf(id)
  if (i >= 0) { if (list.length > 1) list.splice(i, 1) }   // keep >= 1 selected
  else list.push(id)
}

function rigView(id) {
  const r = (props.presets.rigs || []).find(x => x.id === id)
  return r ? r.view : 'front'
}

// Parts are rigged in ONE view, and the rig picks it (front — a side view
// occludes the arms), so switching into parts mode repins the view selection.
function setTask(t) {
  props.form.task = t
  if (t === 'parts') props.form.views = [rigView(props.form.rig)]
}

function setRig(id) {
  props.form.rig = id
  props.form.views = [rigView(id)]
}

function onKey(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter')
    emit(task.value === 'parts' ? 'generate-parts' : 'generate-batch')
}
</script>

<template>
  <div class="panel">
    <label>描述（角色 + 动作，中文/英文均可）</label>
    <textarea v-model="form.prompt" @keydown="onKey"
              :placeholder="task === 'parts'
                ? '例如：一位持剑的骑士，深色像素风'
                : '例如：一位持剑的骑士，深色像素风，横版朝右'"></textarea>

    <div style="margin-top:10px">
      <label>产出</label>
      <div class="chips">
        <button type="button" class="chip" :class="{ on: task === 'sprite' }"
                @click="setTask('sprite')">精灵表（动作帧）</button>
        <button type="button" class="chip" :class="{ on: task === 'parts' }"
                @click="setTask('parts')">骨骼组件（拆件）</button>
      </div>
    </div>

    <template v-if="task === 'sprite'">
      <div style="margin-top:10px">
        <label>视角（可多选）</label>
        <div class="chips">
          <button v-for="v in presets.views" :key="v.id" type="button"
                  class="chip" :class="{ on: form.views.includes(v.id) }"
                  @click="toggle(form.views, v.id)">{{ v.zh }}</button>
        </div>
      </div>

      <div style="margin-top:10px">
        <label>动作（可多选）</label>
        <div class="chips">
          <button v-for="a in presets.actions" :key="a.id" type="button"
                  class="chip" :class="{ on: form.actions.includes(a.id) }"
                  @click="toggle(form.actions, a.id)">{{ a.zh }}</button>
        </div>
      </div>
    </template>

    <template v-else>
      <div style="margin-top:10px">
        <label>骨架</label>
        <select :value="form.rig" @change="setRig($event.target.value)">
          <option v-for="r in presets.rigs" :key="r.id" :value="r.id">{{ r.zh }}</option>
        </select>
      </div>
      <div style="margin-top:10px">
        <label>视角（拆件只用单个视角，建议正面）</label>
        <select :value="form.views[0]" @change="form.views = [$event.target.value]">
          <option v-for="v in presets.views" :key="v.id" :value="v.id">{{ v.zh }}</option>
        </select>
      </div>
    </template>

    <div style="margin-top:10px">
      <label>背景键色（选与角色主色相反的那个）</label>
      <div class="chips">
        <button v-for="k in presets.keys" :key="k.id" type="button"
                class="chip" :class="{ on: form.key === k.id }"
                @click="form.key = k.id">{{ k.zh }}</button>
      </div>
    </div>

    <div class="row" style="margin-top:10px">
      <div>
        <label>朝向</label>
        <select v-model="form.facing">
          <option v-for="f in presets.facings" :key="f.id" :value="f.id">{{ f.zh }}</option>
        </select>
      </div>
      <div>
        <label>风格</label>
        <select v-model="form.style">
          <option v-for="s in presets.styles" :key="s.id" :value="s.id">
            {{ s.zh }} · {{ s.family }}
          </option>
        </select>
      </div>
    </div>

    <div v-if="task === 'sprite'" class="row" style="margin-top:10px">
      <div>
        <label>模式</label>
        <select v-model="form.mode">
          <option value="video">视频（动作稳）</option>
          <option value="image">图片（快）</option>
        </select>
      </div>
      <div>
        <label>帧数</label>
        <select v-model.number="form.frames">
          <option v-for="n in presets.frames" :key="n" :value="n">{{ n }} 帧</option>
        </select>
      </div>
      <div>
        <label>格子</label>
        <select v-model="form.cell">
          <option v-for="c in presets.cells" :key="c" :value="String(c)">
            {{ c === 'HD' ? 'HD' : c + ' px' }}
          </option>
        </select>
      </div>
    </div>

    <div class="row" style="margin-top:14px">
      <template v-if="task === 'sprite'">
        <button class="ghost" :disabled="busy || !form.prompt" @click="emit('generate-character')">
          只生成角色
        </button>
        <button class="primary" :disabled="busy || !form.prompt || combos === 0"
                @click="emit('generate-batch')">
          批量生成 ×{{ combos }} · Ctrl ↵
        </button>
      </template>
      <button v-else class="primary" :disabled="busy || !form.prompt"
              @click="emit('generate-parts')">
        生成骨骼组件 · Ctrl ↵
      </button>
    </div>
    <p class="hint">
      <template v-if="task === 'parts'">
        一次出全部拆件：角色 A-pose 正面站立、各部件互不相连，切分后每块一张透明 PNG，
        另出一张按原位置拼回的静息姿态图。背景键色同上，绿色系角色请选蓝底。
      </template>
      <template v-else>
        勾选多个视角×动作，一次生成。同一视角的动作复用同一张 master（视频模式才保证一致）。
        背景键色：角色若偏绿就用蓝底、偏蓝就用绿底，避免键色把服装吃掉（破洞）。
      </template>
    </p>
  </div>
</template>

<style scoped>
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.chip {
  padding: 4px 10px; border: 1px solid var(--line); border-radius: 999px;
  background: var(--panel-2, transparent); color: var(--fg, inherit);
  font-size: 12px; cursor: pointer;
}
.chip.on { border-color: var(--accent, #e07); background: var(--accent, #e07); color: #fff; }
.hint { color: var(--muted); font-size: 11px; margin: 8px 0 0; }
</style>
