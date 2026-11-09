<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "../api";

const emit = defineEmits(["changed"]);

const tg = ref({ configured: false, linked: false, auto_enabled: false, account: null });
const form = ref({ tg_api_id: "", tg_api_hash: "", tg_auto_login: false });
const qr = ref(null); // {status, qr_svg, error, account}
const starting = ref(false);
const disconnecting = ref(false);
const showHash = ref(false);
const error = ref("");
let pollTimer = null;
const passwordSubmitting = ref(false);

const qrDataUri = computed(() =>
  qr.value?.qr_svg ? "data:image/svg+xml;utf8," + encodeURIComponent(qr.value.qr_svg) : ""
);

async function load() {
  try {
    tg.value = await api("/tg/status");
    const cfg = await api("/config");
    form.value = {
      tg_api_id: cfg.tg_api_id || "",
      tg_api_hash: cfg.tg_api_hash || "",
      tg_auto_login: !!cfg.tg_auto_login,
    };
    notifyTarget.value = cfg.notify_target || "me";
    if (tg.value.linked) loadPeers();
  } catch (e) {
    error.value = e.message;
  }
}

async function saveConnect() {
  error.value = "";
  starting.value = true;
  qr.value = null;
  clearTimeout(pollTimer);
  try {
    // TG 卡片独立保存，不依赖配置页的「保存配置」按钮
    await api("/config", {
      method: "PUT",
      body: {
        tg_api_id: Number(form.value.tg_api_id) || 0,
        tg_api_hash: String(form.value.tg_api_hash || "").trim(),
        tg_auto_login: !!form.value.tg_auto_login,
      },
    });
    qr.value = await api("/tg/qr/start", { method: "POST" });
    if (qr.value.status === "authorized") {
      await load();
      emit("changed");
    } else if (qr.value.status === "waiting") {
      schedulePoll();
    }
  } catch (e) {
    error.value = e.message;
  } finally {
    starting.value = false;
  }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollQr, 2000);
}

async function pollQr() {
  if (!qr.value || !["waiting", "waiting_password"].includes(qr.value.status)) return;
  try {
    const r = await api("/tg/qr/poll", { method: "POST" });
    qr.value = r;
    if (r.status === "waiting") {
      schedulePoll();
    } else if (r.status === "authorized") {
      await load();
      emit("changed");
    }
  } catch (e) {
    error.value = e.message;
  }
}

async function cancelScan() {
  clearTimeout(pollTimer);
  qr.value = null;
  try {
    await api("/tg/qr/cancel", { method: "POST" });
  } catch {
    /* 忽略 */
  }
}
async function submitPassword() {
  const password = window.prompt("Telegram 两步验证密码：");
  if (password === null) return;
  passwordSubmitting.value = true;
  error.value = "";
  try {
    qr.value = await api("/tg/qr/password", { method: "POST", body: { password } });
    await load();
    emit("changed");
  } catch (e) {
    error.value = e.message;
  } finally {
    passwordSubmitting.value = false;
  }
}


const notifyTarget = ref("me");
const peers = ref([]);
const peersLoading = ref(false);
const targetSaved = ref(false);
let targetTimer = null;

async function loadPeers() {
  peersLoading.value = true;
  try {
    const r = await api("/tg/peers");
    peers.value = r.peers || [];
  } catch {
    /* 拉取失败时下拉静默降级为仅「保存的消息」 */
  } finally {
    peersLoading.value = false;
  }
}

async function saveTarget() {
  targetSaved.value = false;
  try {
    await api("/config", { method: "PUT", body: { notify_target: notifyTarget.value } });
    targetSaved.value = true;
    clearTimeout(targetTimer);
    targetTimer = setTimeout(() => (targetSaved.value = false), 2500);
  } catch (e) {
    error.value = e.message;
  }
}
const testingNotify = ref(false);
async function testNotify() {
  testingNotify.value = true;
  error.value = "";
  try {
    await api("/notify/test", { method: "POST" });
    error.value = "";
    window.alert("测试推送已发送，请在上方推送目标中查看。");
  } catch (e) {
    error.value = e.message;
  } finally {
    testingNotify.value = false;
  }
}

async function disconnect() {
  if (!window.confirm("断开并终止 Telegram 会话？TG 里将无法再自动确认登录。")) return;
  disconnecting.value = true;
  try {
    await api("/tg/logout", { method: "POST" });
    await load();
    emit("changed");
  } catch (e) {
    error.value = e.message;
  } finally {
    disconnecting.value = false;
  }
}

onMounted(load);
onUnmounted(() => clearTimeout(pollTimer));
</script>

<template>
  <div class="card">
    <h3>① Telegram 连接</h3>
    <p class="muted" style="margin-top: 0">
      连接后 Picix 登录可全自动完成（向 @vStreamingBot 发送确认消息）。
      api_id / api_hash 在
      <a href="https://my.telegram.org" target="_blank" rel="noopener" style="color: var(--color-accent-strong)">my.telegram.org</a>
      → API development tools 一次性免费申请。
    </p>

    <div class="row tg-status-row">
      <span v-if="tg.linked" class="badge ok">
        已连接{{ tg.account?.name ? `：${tg.account.name}` : "" }}{{ tg.account?.username ? `（@${tg.account.username}）` : "" }}
      </span>
      <span v-else class="badge plain">未连接（不连接也可手动点 t.me 链接完成登录）</span>
      <span v-if="tg.auto_enabled" class="badge plain">自动确认已开启</span>
    </div>

    <div v-if="tg.linked" class="row notify-row">
      <label class="field" style="flex: 1; margin: 0">
        <span>推送目标（运行结果 / 登录恢复 / 测试推送的接收位置）</span>
        <span class="hash-wrap">
          <select v-model="notifyTarget" :disabled="peersLoading" @change="saveTarget">
            <option value="me">保存的消息</option>
            <option v-for="p in peers.filter((x) => x.id !== 'me')" :key="p.id" :value="p.id">
              {{ p.type }}：{{ p.name }}
            </option>
          </select>
          <button type="button" class="btn small hash-toggle" :disabled="peersLoading" @click="loadPeers">
            {{ peersLoading ? "加载中…" : "刷新列表" }}
          </button>
        </span>
      </label>
      <span v-if="targetSaved" class="badge ok">已保存</span>
    </div>
    <details class="tg-config-details" :open="!tg.linked">
      <summary>连接参数（api_id / api_hash）</summary>
      <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); margin-top: var(--space-3)">
        <label class="field">
          <span>api_id</span>
          <input v-model="form.tg_api_id" type="number" placeholder="1234567" />
        </label>
        <label class="field">
          <span>api_hash</span>
          <span class="hash-wrap">
            <input v-model="form.tg_api_hash" :type="showHash ? 'text' : 'password'" placeholder="0123456789abcdef…" autocomplete="off" />
            <button type="button" class="btn small hash-toggle" @click="showHash = !showHash">
              {{ showHash ? "隐藏" : "显示" }}
            </button>
          </span>
        </label>
      </div>
      <label style="display: flex; gap: var(--space-2); align-items: center; margin-top: var(--space-3); margin-bottom: var(--space-1)">
        <input v-model="form.tg_auto_login" type="checkbox" style="width: auto" />
        <span>自动确认：Picix 登录时自动发送 TG 消息（需已连接）</span>
      </label>
      <p class="muted" style="margin-top: 0">
        注意：开启后请勿与浏览器同时登录使用站点（会互顶会话）。
      </p>
    </details>

    <div class="action-area">
      <div class="row">
        <button class="btn primary" :disabled="starting" @click="saveConnect">
          {{ starting ? "生成二维码…" : tg.linked ? "重新扫码连接" : "保存并扫码连接" }}
        </button>
        <button v-if="qr && qr.status === 'waiting'" class="btn small" @click="cancelScan">取消扫码</button>
        <button class="btn small danger" :disabled="disconnecting" @click="disconnect">
          {{ disconnecting ? "断开中…" : "断开会话" }}
        </button>
        <button class="btn small" :disabled="testingNotify" @click="testNotify">
          {{ testingNotify ? "发送中…" : "发送测试推送" }}
        </button>
      </div>
    </div>

    <div v-if="qr && qr.status === 'waiting' && qrDataUri" style="margin-top: var(--space-3); text-align: center">
      <img
        :src="qrDataUri"
        alt="Telegram 登录二维码"
        style="width: 190px; height: 190px; background: var(--color-qr-background); border-radius: var(--radius-md); padding: var(--space-2)"
      />
      <p class="muted">用 Telegram App「设置 → 设备 → 链接桌面设备」扫描；二维码约每 30 秒自动轮换</p>
    </div>
    <div v-if="qr && qr.status === 'authorized'" class="alert ok">扫码成功，Telegram 会话已建立</div>
    <div v-if="qr && qr.status === 'waiting_password'" class="alert info">
      Telegram 已开启两步验证，需要输入账号密码才能完成连接。
      <button class="btn small" :disabled="passwordSubmitting" @click="submitPassword">
        {{ passwordSubmitting ? "验证中…" : "输入两步验证密码" }}
      </button>
    </div>
    <div v-if="qr && qr.error" class="alert err">{{ qr.error }}</div>
    <div v-if="error" class="alert err">{{ error }}</div>
  </div>
</template>

<style scoped>
.tg-status-row {
  margin-bottom: var(--space-3);
}

.tg-config-details {
  margin-bottom: var(--space-3);
}

.action-area {
  margin-top: var(--space-3);
}

.hash-wrap {
  display: flex;
  align-items: stretch;
  gap: var(--space-2);
}

.hash-wrap input {
  flex: 1;
  min-width: 0;
}

.hash-toggle {
  flex-shrink: 0;
  white-space: nowrap;
}
</style>
