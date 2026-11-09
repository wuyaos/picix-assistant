<script setup>
import { computed, onMounted, ref } from "vue";
import { api } from "../api";
const props = defineProps({ status: { type: Object, default: null } });
const emit = defineEmits(["refresh", "go-history"]);
const acceptCodes = ref([]);
const loading = ref(true);
const error = ref("");
const tasks = computed(() => (props.status?.tasks || []).filter((t) => acceptCodes.value.includes(t.code)));
function prog(t) {
  const p = t.progress || {};
  const cur = p.progress ?? t.progress ?? 0; const tgt = p.target ?? t.target ?? 0;
  return { cur, tgt, done: p.status === "COMPLETED" || !!p.finishedAt, pct: tgt > 0 ? Math.min(100, (cur / tgt) * 100) : 0, active: p.status === "ACTIVE" };
}
onMounted(async () => { try { acceptCodes.value = (await api("/config")).accept_task_codes || []; } catch (e) { error.value = e.message; } finally { loading.value = false; } });
</script>
<template>
  <section class="card tasks-card">
    <div class="section-title"><h3>今日任务</h3><button class="btn ghost small" @click="emit('go-history')">查看历史 →</button></div>
    <div v-if="error" class="alert err">{{ error }}</div>
    <div v-else-if="loading || !status" class="loading">正在加载任务…</div>
    <div v-else-if="!status.logged_in" class="empty">登录后查看今日任务。</div>
    <div v-else-if="!tasks.length" class="empty">登录后点击刷新解析任务。</div>
    <div v-else class="task-list">
      <div v-for="t in tasks" :key="t.code" class="task-item">
        <div class="task-head"><span class="task-name">{{ t.name || t.code }}</span><span class="muted task-num">{{ prog(t).cur }} / {{ prog(t).tgt }}</span><span class="badge" :class="prog(t).done ? 'ok' : prog(t).active ? 'warn' : 'muted'">{{ prog(t).done ? "已完成" : prog(t).active ? "进行中" : "已过期" }}</span></div>
        <div class="progress"><div :style="{ width: prog(t).pct + '%' }"></div></div>
      </div>
    </div>
  </section>
</template>
<style scoped>
.section-title { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.task-list { display: flex; flex-direction: column; gap: var(--space-3); }
.task-head { display: flex; align-items: center; gap: var(--space-3); min-width: 0; }
.task-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: var(--weight-semibold); }
.task-num { margin-left: auto; white-space: nowrap; }
.task-item .progress { margin-top: var(--space-2); }
</style>
