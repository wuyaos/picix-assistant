<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { api, fmtEpoch } from "../api";
const props = defineProps({ status: { type: Object, default: null } });
const packages = ref([]); const traffic = ref(null); const purchases = ref([]); const loading = ref(false); const msg = ref(null);
const pending = computed(() => purchases.value.filter((p) => p.status === "pending" || p.status === "unknown"));
function fmtBytes(n) { if (n == null || n === "") return "-"; const v = Number(n); if (Number.isNaN(v)) return String(n); const units = ["B", "KB", "MB", "GB", "TB", "PB"]; let x = v, u = 0; while (Math.abs(x) >= 1024 && u < units.length - 1) { x /= 1024; u++; } return `${x.toFixed(x >= 100 || u === 0 ? 0 : 1)} ${units[u]}`; }
function unitB(p) { return Number(p.unitBytes) > 0 ? Number(p.unitBytes) : 1; }
function pkgPct(p) { const total = Number(p.total || 0); return total > 0 ? Math.min(100, (Number(p.used || 0) / total) * 100) : 0; }
function pkgRemain(p) { const total = Number(p.total || 0); return total > 0 ? fmtBytes((total - Number(p.used || 0)) * unitB(p)) : "-"; }
function pkgExpired(p) { const e = p.expiredAt; if (!e) return false; return (typeof e === "number" ? (e > 1e12 ? e / 1000 : e) * 1000 : new Date(String(e)).getTime()) <= Date.now(); }
function trafficEntries() { const t = traffic.value; if (!t) return []; return [["totalRemainingBytes", "可用流量"], ["totalUsedBytes", "已用流量"], ["canStream", "可串流"]].filter(([k]) => t[k] != null).map(([key, label]) => ({ key, label, val: key === "canStream" ? (t[key] ? "是" : "否") : fmtBytes(t[key]) })); }
async function load() { if (!props.status?.logged_in) return; loading.value = true; msg.value = null; try { const [pk, ps] = await Promise.all([api("/mall/packages"), api("/mall/purchases")]); packages.value = Array.isArray(pk?.items) ? pk.items : []; traffic.value = pk?.traffic || null; purchases.value = Array.isArray(ps) ? ps : []; } catch (e) { msg.value = e.message; } finally { loading.value = false; } }
defineExpose({ load }); onMounted(load);
watch(() => props.status?.logged_in, (v) => { if (v) load(); });
</script>
<template>
  <section class="card packages-card">
    <div class="section-title"><h3>资源包</h3><div class="traffic-badges"><span v-for="e in trafficEntries()" :key="e.key" class="badge plain">{{ e.label }} {{ e.val }}</span></div><button class="btn ghost small" :disabled="loading || !status?.logged_in" @click="load">{{ loading ? "刷新中…" : "刷新" }}</button></div>
    <div v-if="!status" class="loading">正在加载资源包…</div>
    <div v-else-if="!status.logged_in" class="empty">登录后查看资源包。</div>
    <template v-else>
      <div v-if="msg" class="alert err">{{ msg }}</div>
      <div v-else-if="loading && !packages.length" class="loading">正在加载资源包…</div>
      <div v-else-if="!packages.length" class="empty">暂无资源包</div>
      <div v-else class="package-list">
        <article v-for="(p, i) in packages" :key="i" class="package-item">
          <div class="package-head"><strong>{{ p.name || p.goodName || `#${p.id ?? p.goodId ?? i}` }}</strong><span v-if="pkgExpired(p)" class="badge err">已过期</span></div>
          <div class="package-meta"><span>剩余 {{ pkgRemain(p) }} / {{ fmtBytes(Number(p.total || 0) * unitB(p)) }}</span><span class="muted">{{ p.expiredAt ? fmtEpoch(typeof p.expiredAt === "number" ? (p.expiredAt > 1e12 ? p.expiredAt / 1000 : p.expiredAt) : new Date(String(p.expiredAt)).getTime() / 1000) : "永久" }}</span></div>
          <div class="progress"><div :class="{ warning: 100 - pkgPct(p) < 20 }" :style="{ width: pkgPct(p) + '%' }"></div></div>
        </article>
      </div>
      <div v-if="pending.length" class="alert warn">有 {{ pending.length }} 笔自动购买待确认，请先在站点核对订单。</div>
      <details v-if="purchases.length" class="purchase-details"><summary>自动购买记录（{{ purchases.length }}）</summary><div v-for="p in purchases.slice(0, 10)" :key="p.id" class="purchase-row"><span>{{ p.good_name || `#${p.good_id}` }}</span><span class="badge" :class="p.status === 'success' ? 'ok' : p.status === 'pending' || p.status === 'unknown' ? 'warn' : 'err'">{{ p.status }}</span><span class="muted">{{ p.error || p.order_code || "" }}</span></div></details>
    </template>
  </section>
</template>
<style scoped>
.section-title, .package-head, .package-meta { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.section-title { flex-wrap: wrap; }
.traffic-badges { display: flex; flex: 1; flex-wrap: wrap; gap: var(--space-1); justify-content: flex-end; }
.traffic-badges .badge { min-height: 1.5rem; padding: 0.2rem 0.5rem; font-size: var(--font-xs); }
.package-list { display: flex; flex-direction: column; gap: var(--space-3); }
.package-item { padding: var(--space-3); background: var(--color-surface-raised); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); }
.package-meta { margin: var(--space-2) 0; font-size: var(--font-sm); }
.package-item .progress > div.warning { background: var(--color-warning); }
.purchase-details { margin-top: var(--space-3); border-top: 1px solid var(--color-border-subtle); padding-top: var(--space-3); }
.purchase-row { display: grid; grid-template-columns: 1fr auto 1fr; gap: var(--space-2); align-items: center; padding-top: var(--space-2); font-size: var(--font-sm); }
</style>
