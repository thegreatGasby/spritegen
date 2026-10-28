<script setup>
defineProps({
  result: Object,   // { kind:'parts', count, rig, view, style, sheet, material, parts:[] }
})
</script>

<template>
  <div class="panel parts">
    <div class="head">
      <div class="title">
        <template v-if="result.count">拆件结果 <span class="tag">{{ result.count }} 件</span></template>
        <template v-else>模型生成图（未成功拆分）</template>
        <span class="muted">{{ result.rig }} · {{ result.view }} · {{ result.style }}</span>
      </div>
      <div class="dl">
        <a v-if="result.sheet" :href="result.sheet" download><button>下载 parts_sheet.png</button></a>
        <a v-if="result.material" :href="result.material" download><button class="ghost">下载原始生成图</button></a>
      </div>
    </div>

    <template v-if="result.sheet">
      <div class="title">静息姿态（各部件按原位置拼回，透明底）</div>
      <img class="rest" :src="result.sheet" alt="rest pose" />

      <div class="title">拆分部件 · 每块一张透明 PNG</div>
      <div class="grid">
        <figure v-for="p in result.parts" :key="p.name">
          <img :src="p.url" alt="" />
          <figcaption>
            <span class="tag">{{ p.name }}</span>
            <span class="muted">{{ p.bbox[2] }}×{{ p.bbox[3] }}</span>
          </figcaption>
          <div class="dl">
            <a :href="p.url" download><button class="ghost">下载</button></a>
          </div>
        </figure>
      </div>

      <p class="hint">
        offset 是各部件中心相对静息姿态中心的偏移，单位＝静息姿态高度（+x 右、+y 下）——
        换到你的绑定工具里按它对齐即可。切割线在关节处，相邻部件不重叠，接缝需要你自己补一点重合。
      </p>
    </template>

    <!-- always preview the model's raw output, success or failure -->
    <template v-if="result.material">
      <div class="title">模型原始生成图</div>
      <img class="material" :src="result.material" alt="raw generated sheet" />
    </template>
  </div>
</template>

<style scoped>
.parts { display: flex; flex-direction: column; gap: 10px; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.title { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 8px; }
.muted { font-size: 11px; color: var(--muted); }
.rest { max-width: 100%; max-height: 340px; object-fit: contain; align-self: flex-start; }
/* the raw sheet still carries the model's key-colour background — no checker */
.material { max-width: 100%; max-height: 420px; object-fit: contain; align-self: flex-start; border-radius: 6px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
figure { margin: 0; background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 6px; }
.grid img { width: 100%; }
figcaption { font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
.dl { display: flex; gap: 6px; margin-top: 6px; }
.dl button { font-size: 11px; padding: 2px 8px; }
a { text-decoration: none; }
.hint { color: var(--muted); font-size: 11px; margin: 0; }
/* transparency checker behind every cut-out, so holes are obvious */
.rest, .grid img {
  background-image:
    linear-gradient(45deg, #2a2a33 25%, transparent 25%),
    linear-gradient(-45deg, #2a2a33 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #2a2a33 75%),
    linear-gradient(-45deg, transparent 75%, #2a2a33 75%);
  background-size: 12px 12px;
  background-position: 0 0, 0 6px, 6px -6px, -6px 0;
  background-color: #1b1b22;
  border-radius: 6px;
}
</style>
