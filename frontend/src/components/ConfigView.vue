<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "../api";
import TelegramCard from "./TelegramCard.vue";
import LoginCard from "./LoginCard.vue";

const emit = defineEmits(["changed"]);


// 任务标题：登录后从 /api/status 的任务表取站点真名；未登录用编码解析兜底
const taskMeta = ref(null); // code -> 任务对象(name/description/periodType/rewardPoints/...)

const tasksLoaded = ref(false);
const tasksSource = ref("");
async function loadTasks() {
  try {
    const st = await api("/status");
    const raw = Array.isArray(st.tasks) ? st.tasks : [];
    const map = {};
    for (const t of raw) if (t.code) map[t.code] = t;
    taskMeta.value = map;
    tasksLoaded.value = true;
    tasksSource.value = st.logged_in ? (raw.length ? "已解析" : "已登录但站点未返回任务") : "登录并总解析后拉取站点任务列表";
  } catch (e) {
    taskMeta.value = {};
    tasksLoaded.value = true;
    tasksSource.value = "刷新失败：" + e.message;
  }
}

function describeCode(code) {
  const monthly = code.startsWith("M_");
  const ml = code.includes("_ML_");
  const n = (code.match(/(\d+)$/) || [])[1];
  return `${monthly ? "每月" : "每日"}${ml ? "片单" : ""}解锁${n ? ` ${n} 部` : "影片"}`;
}

function taskLabel(code) {
  const t = taskMeta.value?.[code];
  if (t?.name) return t.name;
  return describeCode(code);
}
const availableCodes = computed(() => Object.keys(taskMeta.value || {}));
const form = ref({
  schedule_time: "09:00",
  timezone: "Asia/Shanghai",
  max_unlocks_per_run: 2,
  monthly_unlocks_per_day: 1,
  mall_enabled: false,
  mall_good_id: 0,
  proxy: "",
});
const goods = ref([]); // 商品列表（登录后拉取，固定购买下拉用）
const acceptCodes = ref([]);
const parsing = ref(false);
const lastParseAt = ref(0);
async function parseAll() {
  parsing.value = true;
  try {
    await Promise.allSettled([loadTasks(), loadLists(), loadGoods()]);
    lastParseAt.value = Date.now();
    localStorage.setItem("picix_last_parse", String(lastParseAt.value));
  } finally {
    parsing.value = false;
  }
}
const pendingCode = ref("");
const template = ref("custom");
const unlockListIds = ref([]);
const lists = ref([]); // [{id, name}]
const loadingLists = ref(false);
const saving = ref(false);
const error = ref("");
const saved = ref(false);
const testingProxy = ref(false);
const proxyTestResult = ref(null);

async function testProxy() {
  testingProxy.value = true;
  proxyTestResult.value = null;
  try {
    proxyTestResult.value = await api("/proxy/test", {
      method: "POST",
      body: { proxy: form.value.proxy ?? "" },
    });
  } catch (e) {
    proxyTestResult.value = { ok: false, error: e.message, proxy: form.value.proxy || "(直连)" };
  } finally {
    testingProxy.value = false;
  }
}

async function loadGoods() {
  try {
    const resp = await api("/mall/goods");
    goods.value = Array.isArray(resp?.items) ? resp.items : [];
  } catch {
    goods.value = []; // 未登录：下拉留空提示
  }
}

async function load() {
  try {
    const cfg = await api("/config");
    form.value = {
      schedule_time: cfg.schedule_time,
      timezone: cfg.timezone,
      max_unlocks_per_run: cfg.max_unlocks_per_run,
      monthly_unlocks_per_day: cfg.monthly_unlocks_per_day,
      mall_enabled: !!cfg.mall_enabled,
      mall_good_id: cfg.mall_good_id || 0,
      proxy: cfg.proxy || "",
    };
    acceptCodes.value = [...(cfg.accept_task_codes || [])];
    unlockListIds.value = [...(cfg.unlock_list_ids || [])];
  } catch (e) {
    error.value = e.message;
  }
}

// 已解析片单缓存到 localStorage：刷新页面不丢失，未拉取成功时也能显示已保存选择
const LISTS_CACHE = "picix_lists_cache";
function cacheLists() {
  try {
    localStorage.setItem(LISTS_CACHE, JSON.stringify(lists.value));
  } catch {}
}
function hydrateLists() {
  try {
    const cached = JSON.parse(localStorage.getItem(LISTS_CACHE) || "[]");
    if (Array.isArray(cached) && cached.length) lists.value = cached;
  } catch {}
}

async function loadLists(silent = false) {
  loadingLists.value = true;
  if (!silent) error.value = "";
  try {
    const resp = await api("/lists");
    const mine = Array.isArray(resp?.mine) ? resp.mine : [];
    const subs = Array.isArray(resp?.subscriptions) ? resp.subscriptions : [];
    lists.value = [
      ...mine,
      ...subs,
    ]
      .map((l) => ({
        id: l.id ?? l.listId ?? l.movieListId,
        name: l.name || l.title || l.listName || `#${l.id ?? l.listId ?? l.movieListId}`,
        subscribed: !!l.subscribed,
      }))
      .filter((l) => l.id != null);
    cacheLists();
  } catch (e) {
    if (!silent) error.value = e.message;
  } finally {
    loadingLists.value = false;
  }
}

// 已保存但当前列表里没有的片单 id，兜底显示，避免勾选“消失”
const listsDisplay = computed(() => {
  const have = new Set(lists.value.map((l) => l.id));
  const missing = unlockListIds.value
    .filter((id) => !have.has(id))
    .map((id) => ({ id, name: `#${id}（已保存的选择）`, subscribed: false }));
  return [...lists.value, ...missing];
});

function toggleList(id) {
  const i = unlockListIds.value.indexOf(id);
  if (i >= 0) unlockListIds.value.splice(i, 1);
  else unlockListIds.value.push(id);
}

const taskDropOpen = ref(false);
function toggleTask(c) {
  const i = acceptCodes.value.indexOf(c);
  if (i >= 0) acceptCodes.value.splice(i, 1);
  else acceptCodes.value = [...acceptCodes.value, c];
}

function removeTask(c) {
  acceptCodes.value = acceptCodes.value.filter((x) => x !== c);
}

function applyTemplate(name) {
  if (name === "keepalive") {
    acceptCodes.value = ["D_UL_1"];
    form.value.max_unlocks_per_run = 1;
    form.value.mall_enabled = false;
  } else if (name === "daily") {
    const all = availableCodes.value.length
      ? [...availableCodes.value]
      : ["D_UL_1", "M_UL_50", "M_UL_ML_20", "M_UL_ML_50"];
    acceptCodes.value = all;
  }
}

async function save() {
  saving.value = true;
  error.value = "";
  saved.value = false;
  try {
    await api("/config", {
      method: "PUT",
      body: {
        schedule_time: form.value.schedule_time,
        timezone: form.value.timezone,
        max_unlocks_per_run: Number(form.value.max_unlocks_per_run),
        monthly_unlocks_per_day: Number(form.value.monthly_unlocks_per_day),
        mall_enabled: !!form.value.mall_enabled,
        mall_good_id: Number(form.value.mall_good_id) || 0,
        proxy: form.value.proxy,
        accept_task_codes: acceptCodes.value,
        unlock_list_ids: unlockListIds.value.map(Number),
      },
    });
    saved.value = true;
    emit("changed");
    setTimeout(() => (saved.value = false), 2500);
  } catch (e) {
    error.value = e.message;
  } finally {
    saving.value = false;
  }
}

// 任务下拉浮层：点击浮层外或按 Esc 关闭
function onDocClick(e) {
  if (!taskDropOpen.value) return;
  if (!e.target.closest(".task-drop-area")) taskDropOpen.value = false;
}
function onKeydown(e) {
  if (e.key === "Escape") taskDropOpen.value = false;
}

// 锚点导航：桌面 ≥67.5rem 显示，点击滚动到分区并高亮当前项
const sections = [
  { id: "sec-telegram", label: "Telegram 连接" },
  { id: "sec-login", label: "Picix 登录" },
  { id: "sec-schedule", label: "调度与运行" },
  { id: "sec-unlock", label: "解锁策略" },
  { id: "sec-tasks", label: "自动领取任务" },
  { id: "sec-mall", label: "商城固定购买" },
  { id: "sec-parse", label: "站点数据解析" },
];
const activeSection = ref(sections[0].id);
function scrollToSection(id) {
  activeSection.value = id;
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function onChanged() {
  emit("changed");
}

onMounted(() => {
  load();
  lastParseAt.value = Number(localStorage.getItem("picix_last_parse") || 0);
  hydrateLists();
  loadTasks();
  loadLists(true).catch(() => {});
  loadGoods();
  document.addEventListener("click", onDocClick);
  document.addEventListener("keydown", onKeydown);
  // 定时刷新：保持已解析任务/片单为最新
  setInterval(() => {
    loadTasks();
    loadLists(true).catch(() => {});
  }, 300000);
});
onUnmounted(() => {
  document.removeEventListener("click", onDocClick);
  document.removeEventListener("keydown", onKeydown);
});
</script>

<template>
  <div class="config-layout">
    <nav class="config-anchor-nav" aria-label="配置分区导航">
      <a
        v-for="s in sections"
        :key="s.id"
        href="javascript:void(0)"
        class="anchor-link"
        :class="{ active: activeSection === s.id }"
        @click="scrollToSection(s.id)"
      >{{ s.label }}</a>
    </nav>

    <div class="config-sections">
      <section id="sec-telegram" class="config-section">
        <TelegramCard @changed="onChanged" />
      </section>

      <section id="sec-login" class="config-section">
        <LoginCard @changed="onChanged" />
      </section>

      <section id="sec-schedule" class="config-section card">
        <h3>③ 调度与运行</h3>
        <div class="grid config-grid">
          <label class="field">
            <span>每日执行时间（HH:MM）</span>
            <input v-model="form.schedule_time" type="text" placeholder="09:00" />
          </label>
          <label class="field">
            <span>时区（IANA 名称）</span>
            <input v-model="form.timezone" type="text" placeholder="Asia/Shanghai" />
          </label>
          <label class="field">
            <span>单次解锁上限（含冲刺补解，恒定生效）</span>
            <input v-model.number="form.max_unlocks_per_run" type="number" min="1" max="50" />
          </label>
          <label class="field">
            <span>代理（http://host:port 或 socks5://…，留空直连；环境变量 PICIX_PROXY 优先）</span>
            <div class="row proxy-row">
              <input v-model="form.proxy" type="text" placeholder="设了 PICIX_PROXY 时此处不生效" />
              <button class="btn small proxy-test-button" :disabled="testingProxy" @click="testProxy">
                {{ testingProxy ? "测试中…" : "测试" }}
              </button>
            </div>
            <div v-if="proxyTestResult" :class="['alert', 'proxy-result', proxyTestResult.ok ? 'ok' : 'err']">
              {{ proxyTestResult.ok
                ? `连通正常（HTTP ${proxyTestResult.status_code}，${proxyTestResult.elapsed_ms}ms）· ${proxyTestResult.proxy}`
                : `连通失败 · ${proxyTestResult.proxy} · ${proxyTestResult.error}` }}
            </div>
          </label>
        </div>
        <p class="muted config-note">
          会话失效自动恢复在上方「Telegram 连接」卡配置（开启后请勿与浏览器同时使用站点，会互顶会话）。
        </p>

        <h4 class="template-heading">任务模板（预设加载，加载后可再改）</h4>
        <div class="row template-options">
          <label class="template-option">
            <input v-model="template" name="tpl" type="radio" value="keepalive" @change="applyTemplate('keepalive')" />
            <span>最低保号 <span class="muted">只领每日解锁任务 + 解锁 1 部，不碰商城</span></span>
          </label>
          <label class="template-option">
            <input v-model="template" name="tpl" type="radio" value="daily" @change="applyTemplate('daily')" />
            <span>每日任务 <span class="muted">领全部任务 → 商城 → 按缺口解锁</span></span>
          </label>
          <label class="template-option">
            <input v-model="template" name="tpl" type="radio" value="custom" />
            <span>自定义 <span class="muted">不动当前配置，下面手动改</span></span>
          </label>
        </div>
      </section>

      <section id="sec-unlock" class="config-section card">
        <h3>④ 解锁策略</h3>
        <div class="grid config-grid">
          <label class="field">
            <span>月度解锁节奏（部/天，0–5）</span>
            <input v-model.number="form.monthly_unlocks_per_day" type="number" min="0" max="5" />
          </label>
        </div>
        <p class="muted config-note">
          解锁决策：每日任务未完成解 1 部 + 月度任务未完成按「月度节奏」解 X 部（0 =
          月度只靠每日任务搭车推进）；两者合计受「单次解锁上限」约束。月度截止前 3
          天若有缺口会自动冲刺补解（仍受上限约束）并推送提醒。
        </p>
        <h4>解锁片单（按顺序解锁未解锁影片）</h4>
        <div class="row lists-summary">
          <span v-if="lists.length" class="muted">已解析 {{ lists.length }} 个片单，已选 {{ unlockListIds.length }} 个</span>
          <span v-else class="muted">尚未解析片单，请点下方「总解析」</span>
        </div>
        <div v-if="lists.length" class="grid list-grid">
          <label
            v-for="l in listsDisplay"
            :key="l.id"
            class="list-option"
          >
            <input type="checkbox" :checked="unlockListIds.includes(l.id)" @change="toggleList(l.id)" />
            <span>{{ l.name }}</span>
            <span v-if="l.subscribed" class="badge plain">订阅</span>
          </label>
        </div>
        <p v-else class="muted">登录后点下方「总解析」拉取我的片单后选择解锁来源。顺序即勾选顺序对应的片单顺序（列表顺序）。</p>
      </section>

      <section id="sec-tasks" class="config-section card">
        <div class="section-title"><h3>⑤ 自动领取的任务</h3><span v-if="tasksSource" class="muted">{{ tasksSource }}</span></div>
        <div class="row task-drop-area">
          <button class="btn small" :disabled="!availableCodes.length" @click="taskDropOpen = !taskDropOpen">
            从站点任务中选择（已选 {{ acceptCodes.length }}）{{ taskDropOpen ? "▲" : "▼" }}
          </button>
          <span v-if="tasksSource" class="muted task-source-inline">{{ tasksSource }}</span>
          <div
            v-if="taskDropOpen"
            class="task-menu"
          >
            <label v-for="c in availableCodes" :key="c" class="task-option">
              <input type="checkbox" :checked="acceptCodes.includes(c)" @change="toggleTask(c)" />
              <span>{{ taskLabel(c) }}</span>
            </label>
            <div v-if="!availableCodes.length" class="muted">暂无任务，请先总解析</div>
          </div>
        </div>
        <div v-if="acceptCodes.length" class="row selected-tasks">
          <span v-for="c in acceptCodes" :key="c" class="badge plain task-tag">
            {{ taskLabel(c) }}
            <a href="javascript:void(0)" class="task-tag-x" @click="removeTask(c)">×</a>
          </span>
        </div>
        <p class="muted">
          选中后每日运行时自动领取；片单类任务（M_UL_ML_*）需同时配置上方解锁片单。
        </p>
      </section>

      <section id="sec-mall" class="config-section card">
        <h3>⑥ 商城固定购买</h3>
        <label class="mall-toggle">
          <input v-model="form.mall_enabled" type="checkbox" />
          <span>资源包没了就自动购买下面的固定商品</span>
        </label>
        <label class="field mall-field">
          <span>固定购买商品</span>
          <select v-model.number="form.mall_good_id" :disabled="!goods.length">
            <option :value="0">未设置</option>
            <option v-for="g in goods" :key="g.id" :value="g.id">
              {{ g.name }}（{{ g.price }} 积分<template v-if="g.validDays">，{{ g.validDays }} 天</template>）
            </option>
          </select>
        </label>
        <p v-if="!goods.length" class="muted">登录后可拉取商品列表。</p>
        <p class="muted">
          仅任务模板「每日任务」且开关开启时执行；判定标准为无任何未过期且有剩余额度的资源包。
          购买前会先对账未决记录（订单列表核对），避免重复购买；幂等键先入库再请求。
        </p>
      </section>

      <section id="sec-parse" class="config-section card">
        <div class="row parse-header">
          <div class="row parse-meta">
            <h3>⑦ 站点数据解析</h3>
            <span class="badge plain">任务 {{ availableCodes.length }}</span>
            <span class="badge plain">片单 {{ lists.length }}</span>
            <span class="badge plain">商品 {{ goods.length }}</span>
            <span v-if="lastParseAt" class="muted">上次解析 {{ new Date(lastParseAt).toLocaleTimeString() }}</span>
          </div>
          <button class="btn primary small" :disabled="parsing" @click="parseAll">
            {{ parsing ? "解析中…" : "总解析" }}
          </button>
        </div>
      </section>
    </div>

    <div class="save-bar save-bar-sticky">
      <div>
        <div v-if="error" class="alert err save-message">{{ error }}</div>
        <div v-if="saved" class="alert ok save-message">已保存，调度已生效</div>
      </div>
      <button class="btn primary" :disabled="saving" @click="save">
        {{ saving ? "保存中…" : "保存配置" }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.config-layout {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-4);
}

.config-sections {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.config-section.card {
  margin-bottom: 0;
}

.config-anchor-nav {
  display: none;
}

@media (min-width: 67.5rem) {
  .config-layout {
    grid-template-columns: minmax(0, 0.22fr) minmax(0, 1fr);
    align-items: start;
  }

  .config-anchor-nav {
    position: sticky;
    top: calc(var(--space-3) + 3.5rem);
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    padding: var(--space-3);
    background: var(--color-surface);
    border: var(--border-width-thin) solid var(--color-border-subtle);
    border-radius: var(--radius-lg);
  }
}

.anchor-link {
  padding: var(--space-2) var(--space-3);
  color: var(--color-text-muted);
  font-size: var(--font-sm);
  text-decoration: none;
  border-radius: var(--radius-md);
}

.anchor-link:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

.anchor-link.active {
  color: var(--color-accent-strong);
  background: var(--color-accent-muted);
  font-weight: var(--weight-semibold);
}

.config-section {
  scroll-margin-top: calc(var(--space-3) + 3.5rem);
}

.save-bar-sticky {
  position: sticky;
  bottom: 0;
  z-index: 20;
  grid-column: 1 / -1;
  padding: var(--space-3) 0;
}
.config-grid { grid-template-columns: repeat(auto-fit, minmax(var(--size-grid-min), 1fr)); }
.proxy-row { align-items: stretch; flex-wrap: nowrap; }
.proxy-row input { min-width: 0; }
.proxy-test-button { white-space: nowrap; }
.proxy-result { margin: var(--space-2) 0 0; }
.config-note { margin-top: 0; }
.template-heading { margin-top: var(--space-5); }
.template-options { gap: var(--space-4); }
.template-option,
.mall-toggle,
.task-option { display: flex; gap: var(--space-2); align-items: center; cursor: pointer; }
.template-option input,
.mall-toggle input,
.task-option input,
.list-option input { width: auto; }
.lists-summary { margin-bottom: var(--space-3); }
.list-grid { grid-template-columns: repeat(auto-fill, minmax(var(--size-list-min), 1fr)); }
.list-option { display: flex; gap: var(--space-2); align-items: center; padding: var(--space-2) var(--space-3); color: var(--color-text-secondary); border: var(--border-width-thin) solid var(--color-border); border-radius: var(--radius-md); cursor: pointer; }
.list-option:hover { background: var(--color-surface-hover); border-color: var(--color-accent); }
.task-drop-area { position: relative; align-items: center; }
.task-menu { position: absolute; top: 100%; left: 0; z-index: 30; display: flex; flex-direction: column; gap: var(--space-2); min-width: var(--size-dropdown-min); max-height: var(--size-dropdown-max); overflow: auto; padding: var(--space-3); background: var(--color-surface); border: var(--border-width-thin) solid var(--color-border); border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.selected-tasks { flex-wrap: wrap; margin-top: var(--space-3); }
.mall-toggle { margin-bottom: var(--space-3); }
.mall-field { max-width: var(--size-field-max); }
.parse-header { justify-content: space-between; }
.parse-meta { flex-wrap: wrap; gap: var(--space-2); }
.save-message { margin: 0; }

@media (max-width: 36rem) {
  .proxy-row { flex-wrap: wrap; }
  .proxy-test-button { width: 100%; }
  .task-menu { right: 0; min-width: 0; }
}
</style>
