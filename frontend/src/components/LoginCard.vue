<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { api } from "../api";

const emit = defineEmits(["changed"]);

const loggedIn = ref(false);
const loggedAt = ref(null);
const checking = ref(true);
const starting = ref(false);
const pending = ref(null); // { code, tg_url, ttl_seconds }
const pollStatus = ref("");
const tgAuto = ref(null); // 最近一次 poll 响应（tg_sent/tg_error）
const error = ref("");
let pollTimer = null;

async function refresh() {
  try {
    const s = await api("/status");
    loggedIn.value = s.logged_in;
    loggedAt.value = s.logged_at || null;
  } catch (e) {
    error.value = e.message;
  } finally {
    checking.value = false;
  }
}

async function start() {
  error.value = "";
  starting.value = true;
  pollStatus.value = "";
  try {
    pending.value = await api("/login/start", { method: "POST" });
    schedulePoll();
  } catch (e) {
    error.value = e.message;
  } finally {
    starting.value = false;
  }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, 3000);
}

async function poll() {
  if (!pending.value) return;
  try {
    const r = await api("/login/poll", { method: "POST" });
    if (r.status === "confirmed") {
      pending.value = null;
      pollStatus.value = "";
      await refresh();
      emit("changed");
    } else if (r.status === "expired") {
      pending.value = null;
      pollStatus.value = "登录码已过期，请重新生成";
    } else if (r.status === "pending") {
      pollStatus.value = `等待 Telegram 确认…（${r.code}）`;
      tgAuto.value = r;
      schedulePoll();
    }
  } catch (e) {
    pollStatus.value = "";
    error.value = e.message;
  }
}

async function logout() {
  await api("/logout", { method: "POST" });
  await refresh();
  emit("changed");
}

onMounted(refresh);
onUnmounted(() => clearTimeout(pollTimer));
</script>

<template>
  <div class="card">
    <h3>② Picix 登录</h3>
    <div v-if="checking" class="loading">检查中…</div>
    <template v-else>
      <div class="row login-status-row">
        <span class="badge" :class="loggedIn ? 'ok' : 'muted'">{{ loggedIn ? "已登录" : "未登录" }}</span>
        <span v-if="loggedIn" class="muted">登录时间：{{ loggedAt || "-" }}</span>
        <button v-if="loggedIn" class="btn small danger" @click="logout">退出登录</button>
      </div>
      <div v-if="!loggedIn">
        <p class="muted login-intro">
          未登录。点击「开始登录」后，用已注册 Picix 的 Telegram 账号打开生成的链接发送确认消息即可。
        </p>
        <button class="btn primary" :disabled="starting || !!pending" @click="start">
          {{ starting ? "生成中…" : "开始登录" }}
        </button>
        <div v-if="error" class="alert err">{{ error }}</div>
        <template v-if="pending">
          <div class="alert info">
            <div>登录码：<code>{{ pending.code }}</code></div>
            <div class="login-code-help">
              点击下面的链接（或在 Telegram 中打开 @vStreamingBot），发送
              <code>/login {{ pending.code }}</code>：
            </div>
            <a
              :href="pending.tg_url"
              target="_blank"
              rel="noopener"
              class="inline-link login-link"
              >{{ pending.tg_url }}</a
            >
          </div>
          <div v-if="tgAuto && tgAuto.tg_sent" class="alert ok">已自动向 Telegram 发送确认消息，无需手动操作…</div>
          <div v-else-if="tgAuto && tgAuto.tg_error" class="alert err">TG 自动确认失败：{{ tgAuto.tg_error }}，请手动点击上方链接</div>
          <div v-if="pollStatus" class="muted">{{ pollStatus }}</div>
        </template>
        <div v-else-if="pollStatus" class="alert err">{{ pollStatus }}</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.login-status-row {
  margin-bottom: var(--space-3);
}
.login-intro { margin-top: 0; }
.login-code-help { margin-top: var(--space-2); }
.inline-link { color: var(--color-accent-strong); }
.login-link { word-break: break-all; }
</style>
