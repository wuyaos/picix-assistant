<script setup>
import { computed, onMounted, ref } from "vue";
import { api, STATUS_LABEL } from "../api";

const runs = ref([]);
const loading = ref(true);
const error = ref("");

async function load() {
  loading.value = true;
  try {
    runs.value = await api("/runs?limit=50");
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

function statusBadge(s) {
  return s === "success" ? "ok" : s === "partial" ? "warn" : s === "failed" ? "err" : "muted";
}

function dayLabel(sec) {
  if (!sec) return "未知日期";
  const d = new Date(sec * 1000);
  const now = new Date();
  const startOf = (dt) => new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()).getTime();
  const diffDays = Math.round((startOf(now) - startOf(d)) / 86400000);
  if (diffDays === 0) return "今天";
  if (diffDays === 1) return "昨天";
  return `${d.getMonth() + 1}-${d.getDate()}`;
}

function timeOfDay(sec) {
  if (!sec) return "-";
  return new Date(sec * 1000).toLocaleTimeString("zh-CN", { hour12: false });
}

const groups = computed(() => {
  const out = [];
  let cur = null;
  for (const r of runs.value) {
    const label = dayLabel(r.finished_at);
    if (!cur || cur.label !== label) {
      cur = { label, items: [] };
      out.push(cur);
    }
    cur.items.push(r);
  }
  return out;
});

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="card">
    <div class="row" style="justify-content: space-between">
      <h3 style="margin: 0">执行历史</h3>
      <button class="btn ghost small" @click="load">刷新</button>
    </div>
    <div v-if="loading" class="loading">加载中…</div>
    <div v-else-if="error" class="alert err">{{ error }}</div>
    <div v-else-if="!runs.length" class="empty">暂无历史</div>
    <div v-else class="history-groups">
      <section v-for="g in groups" :key="g.label" class="history-group">
        <h4 class="history-day">{{ g.label }}</h4>
        <div class="run-list">
          <article v-for="r in g.items" :key="r.id" class="run-item">
            <div class="run-head">
              <time class="muted">{{ timeOfDay(r.finished_at) }}</time>
              <span class="badge plain">{{ r.trigger }}</span>
              <span class="badge" :class="statusBadge(r.status)">{{ STATUS_LABEL[r.status] || r.status }}</span>
              <span class="muted run-summary">
                解锁 {{ r.detail?.unlocks?.length || 0 }} · 领取 {{ r.detail?.accepted?.length || 0 }}
                <template v-if="r.detail?.errors?.length"> · 错误 {{ r.detail.errors.length }}</template>
              </span>
            </div>
            <details class="run-detail">
              <summary class="muted">详情</summary>
              <div class="muted run-detail-body">
                <div v-for="(s, i) in r.detail?.steps || []" :key="'s' + i">→ {{ s }}</div>
                <div v-for="(u, i) in r.detail?.unlocks || []" :key="'u' + i">
                  {{ u.ok ? "✓" : "✗" }} 解锁 movieId={{ u.movieId }} (listId={{ u.listId }})
                  <span v-if="u.error" class="alert err" style="display: inline; padding: var(--space-1) var(--space-2)">{{ u.error }}</span>
                </div>
                <div v-for="(e, i) in r.detail?.errors || []" :key="'e' + i" style="color: var(--color-danger)">⚠ {{ e }}</div>
              </div>
            </details>
          </article>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.history-groups {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.history-day {
  margin: 0 0 var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--font-sm);
  font-weight: var(--weight-semibold);
}

.run-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.run-item {
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border-subtle);
}

.run-item:last-child {
  border-bottom: 0;
}

.run-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.run-summary {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.run-detail {
  margin-top: var(--space-1);
}

.run-detail-body {
  margin-top: var(--space-2);
  padding-left: var(--space-3);
}
</style>
