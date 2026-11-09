<script setup>
import { ref } from "vue";
import { api, fmtTime, getPoints } from "../api";

const props = defineProps({ status: { type: Object, default: null }, tgStatus: { type: Object, default: () => ({ linked: false }) } });
const emit = defineEmits(["run", "run-force", "go-config"]);
const running = ref(false);
const error = ref("");
const result = ref(null);

async function runNow(force = false) {
  if (force && !window.confirm("强制完整执行将忽略今日跳过判定并推送结果，确定？")) return;
  error.value = "";
  result.value = null;
  running.value = true;
  try {
    result.value = await api("/run", { method: "POST", body: { force } });
    emit(force ? "run-force" : "run");
  } catch (e) {
    error.value = e.message;
  } finally {
    running.value = false;
  }
}
</script>

<template>
  <section class="status-header card">
    <div v-if="!status?.logged_in" class="status-login">
      <div><strong>登录 Picix 后开始管理任务</strong><p class="muted">连接账号即可查看积分、资源包与今日任务进度。</p></div>
      <button class="btn primary" @click="emit('go-config')">前往配置</button>
    </div>
    <template v-else>
      <div class="status-header-main">
        <div class="badge-group">
          <span class="badge" :class="tgStatus.linked ? 'ok' : 'muted'">TG {{ tgStatus.linked ? "已连接" : "未连接" }}</span>
          <span class="badge ok">Picix 已登录</span>
          <span class="points"><b>{{ getPoints(status.user_config) ?? "-" }}</b><small>积分</small></span>
        </div>
        <div class="status-actions">
          <span class="muted">下次运行：{{ fmtTime(status.next_run) }}</span>
          <button class="btn primary small" :disabled="running" @click="runNow(false)">{{ running ? "执行中…" : "立即执行" }}</button>
          <button class="btn danger small" :disabled="running" @click="runNow(true)">强制执行</button>
        </div>
      </div>
      <div v-if="error" class="alert err">{{ error }}</div>
      <div v-if="result" class="alert" :class="result.status === 'failed' ? 'err' : 'ok'">执行结果：{{ result.status }} · 解锁 {{ result.unlocks?.length || 0 }} · 领取 {{ result.accepted?.length || 0 }}</div>
    </template>
  </section>
</template>

<style scoped>
.status-header { margin-bottom: var(--space-4); }
.status-header-main, .status-login, .status-actions, .badge-group { display: flex; align-items: center; gap: var(--space-3); }
.status-header-main, .status-login { justify-content: space-between; }
.status-actions { flex-wrap: wrap; justify-content: flex-end; }
.status-login p { margin: var(--space-1) 0 0; }
.points { display: inline-flex; align-items: baseline; gap: var(--space-1); color: var(--color-accent-strong); }
.points b { font-size: var(--font-xxl); }
.points small { color: var(--color-text-muted); }
@media (max-width: 45rem) { .status-header-main, .status-login { align-items: flex-start; flex-direction: column; } .status-actions { justify-content: flex-start; } }
</style>
