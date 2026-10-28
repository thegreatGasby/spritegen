<script setup>
defineProps({ characters: Array, activeId: String })
defineEmits(['pick'])
</script>

<template>
  <div v-if="characters.length" class="lib">
    <div class="lib-title">角色库 <span class="tag">{{ characters.length }}</span></div>
    <div class="lib-strip">
      <button v-for="c in characters" :key="c.character_id"
              class="card" :class="{ on: c.character_id === activeId }"
              :title="c.text || c.fields.subject"
              @click="$emit('pick', c)">
        <img :src="c.master" alt="" />
        <span>{{ c.fields.subject.slice(0, 18) }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.lib { margin-top: 14px; }
.lib-title { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.lib-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.card {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  padding: 6px; min-width: 84px; background: var(--panel);
}
.card.on { border-color: var(--accent); }
.card img { width: 64px; height: 64px; object-fit: contain; image-rendering: pixelated; }
.card span { font-size: 10px; color: var(--muted); max-width: 78px; overflow: hidden;
             text-overflow: ellipsis; white-space: nowrap; }
</style>
