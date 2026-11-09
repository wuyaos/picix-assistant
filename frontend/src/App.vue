<script setup>
import { onMounted, ref } from "vue";
import ConfigView from "./components/ConfigView.vue";
import HistoryView from "./components/HistoryView.vue";
import PackagesCard from "./components/PackagesCard.vue";
import TasksCard from "./components/TasksCard.vue";
import ConsumptionCard from "./components/ConsumptionCard.vue";
import StatusHeader from "./components/StatusHeader.vue";
import { api, fmtTime } from "./api";
const tab = ref("status"); const status = ref(null); const tgStatus = ref({ linked: false }); const statusError = ref(""); const historyRef = ref(null);
async function loadStatus() { statusError.value = ""; try { const [picix, telegram] = await Promise.all([api("/status"), api("/tg/status")]); status.value = picix; tgStatus.value = telegram; } catch (e) { statusError.value = e.message; } }
function onChanged() { loadStatus(); historyRef.value?.load(); }
function todayRuns() { const runs = status.value?.last_runs || []; const now = new Date(); return runs.filter((r) => { const d = new Date((r.started_at || 0) * 1000); return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate(); }); }
function runSummary(r) { const d = r.detail || {}; const unlocks = Array.isArray(d.unlocks) ? d.unlocks.length : 0; const errors = Array.isArray(d.errors) ? d.errors.filter(Boolean) : []; return `${unlocks ? `解锁${unlocks}` : ""}${errors.length ? ` · ${errors[0].slice(0, 32)}` : ""}` || (r.status === "skipped" ? "跳过" : "-"); }
function statusLabel(s) { return { success: "完成", partial: "部分成功", failed: "失败", skipped: "跳过", running: "运行中" }[s] || s; }
onMounted(loadStatus);
</script>
<template>
  <div class="container">
    <header class="topbar">
      <div class="brand"><img src="/picix-icon.png" alt="picix" class="logo" /><h1>picix-assistant</h1><span v-if="status?.version" class="ver">v{{ status.version }}</span></div>
      <nav class="segmented-nav"><button v-for="t in [['status','状态'],['config','配置'],['history','历史']]" :key="t[0]" class="tab" :class="{ active: tab === t[0] }" @click="tab = t[0]">{{ t[1] }}</button></nav>
    </header>
    <div v-if="statusError" class="alert err">{{ statusError }}</div>
    <main v-show="tab === 'status'">
      <StatusHeader :status="status" :tg-status="tgStatus" @run="onChanged" @run-force="onChanged" @go-config="tab = 'config'" />
      <div class="status-grid"><TasksCard :status="status" @go-history="tab = 'history'" /><PackagesCard :status="status" /></div>
      <ConsumptionCard :status="status" />
      <section class="card runs-card"><div class="section-title"><h3>今日运行记录</h3><button class="btn ghost small" @click="tab = 'history'">查看全部历史 →</button></div><div v-if="!status" class="loading">正在加载运行记录…</div><div v-else-if="!todayRuns().length" class="empty">今天还没有运行。</div><div v-else class="run-list"><div v-for="r in todayRuns()" :key="r.id" class="run-row"><time class="muted">{{ fmtTime(r.started_at * 1000) }}</time><span class="badge" :class="r.status === 'success' ? 'ok' : r.status === 'failed' ? 'err' : r.status === 'partial' ? 'warn' : 'muted'">{{ statusLabel(r.status) }}</span><span class="run-summary">{{ runSummary(r) }}</span></div></div></section>
    </main>
    <main v-show="tab === 'config'" class="config-page"><ConfigView @changed="loadStatus" /></main>
    <main v-show="tab === 'history'"><HistoryView ref="historyRef" /></main>
  </div>
</template>
<style scoped>
.container { max-width: 860px; margin: 0 auto; padding: var(--container-pad-top) var(--container-pad-x) var(--container-pad-bottom); }
.topbar { position: sticky; top: 0; z-index: 20; display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); margin: calc(var(--container-pad-top) * -1) calc(var(--container-pad-x) * -1) var(--space-5); padding: var(--space-3) var(--container-pad-x); background: var(--color-surface); border-bottom: 1px solid var(--color-border); }
.brand, .segmented-nav, .section-title, .run-row { display: flex; align-items: center; gap: var(--space-2); }
.brand h1 { margin: 0; background: linear-gradient(135deg, var(--color-accent), var(--color-accent-strong)); -webkit-background-clip: text; background-clip: text; color: transparent; }
.logo { width: 24px; height: 24px; }
.ver { color: var(--color-text-muted); font-size: var(--font-sm); font-weight: var(--weight-regular); }
.segmented-nav { padding: var(--space-1); background: var(--color-field); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-pill); }
.tab { padding: var(--space-2) var(--space-4); color: var(--color-text-muted); background: transparent; border: 0; border-radius: var(--radius-pill); cursor: pointer; }
.tab.active { color: var(--color-text); background: var(--color-surface-raised); box-shadow: var(--shadow-sm); }
.status-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); margin-bottom: var(--space-4); }
.status-grid > .card { margin-bottom: 0; }
.section-title { justify-content: space-between; margin-bottom: var(--space-3); }
.section-title h3 { margin: 0; }
.empty, .loading { padding: var(--space-4) 0; color: var(--color-text-muted); text-align: center; }
.runs-card { margin-top: var(--space-4); }
.run-list { display: flex; flex-direction: column; gap: var(--space-2); }
.run-row { gap: var(--space-3); padding: var(--space-2) 0; border-bottom: 1px solid var(--color-border-subtle); }
.run-row:last-child { border-bottom: 0; }
.run-summary { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.config-page { display: flex; flex-direction: column; }
@media (max-width: 720px) { .status-grid { grid-template-columns: 1fr; } }
@media (max-width: 520px) { .container { padding: var(--space-4) var(--space-3) var(--space-7); } .topbar { margin: calc(var(--space-4) * -1) calc(var(--space-3) * -1) var(--space-4); padding-inline: var(--space-3); flex-wrap: wrap; } .segmented-nav { width: 100%; } .tab { flex: 1; } .run-row { align-items: flex-start; flex-wrap: wrap; } }
</style>
