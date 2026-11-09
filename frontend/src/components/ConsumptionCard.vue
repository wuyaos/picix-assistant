<script setup>
import { computed } from "vue";
import { fmtEpoch } from "../api";
const props = defineProps({ status: { type: Object, default: null } });
const mc = computed(() => props.status?.minimum_consumption || null);
const mcDue = computed(() => (mc.value?.phase || "").toUpperCase() === "DUE");
const shortfall = computed(() => {
  if (!mc.value) return "-";
  const est = mc.value.estimatedShortfall;
  if (typeof est === "number" && est > 0) return est;
  return Math.max(0, (mc.value.minimumPoints || 0) - (mc.value.spentPoints || 0));
});
</script>
<template>
  <section class="card consumption-card">
    <div class="section-title"><h3>最低消费预估</h3><span v-if="mcDue" class="badge err">结算期临近</span></div>
    <div v-if="!status" class="loading">正在加载最低消费数据…</div>
    <div v-else-if="!status.logged_in" class="empty">登录后查看最低消费预估。</div>
    <div v-else-if="!mc" class="empty">暂无最低消费数据</div>
    <template v-else>
      <div class="consumption-meta"><span>距标准还差 <b>{{ shortfall }}</b> 分</span><span class="muted">当前 {{ mc.spentPoints ?? 0 }} / {{ mc.minimumPoints ?? 0 }}</span></div>
      <div class="progress"><div :style="{ width: Math.min(100, ((mc.spentPoints || 0) / (mc.minimumPoints || 1)) * 100) + '%' }"></div></div>
      <div class="muted">结算时间：{{ mc.nextSettlementAt ? fmtEpoch(mc.nextSettlementAt) : "-" }}</div>
    </template>
  </section>
</template>
<style scoped>
.section-title, .consumption-meta { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.consumption-meta { margin-bottom: var(--space-2); flex-wrap: wrap; }
.consumption-card .muted { margin-top: var(--space-1); }
</style>
