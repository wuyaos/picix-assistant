export async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body != null ? JSON.stringify(options.body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* 非 JSON 响应 */
  }
  if (!res.ok) {
    const msg = data?.detail || data?.message || `HTTP ${res.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export function fmtTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString("zh-CN", { hour12: false });
}

export function fmtEpoch(sec) {
  if (!sec) return "-";
  return new Date(sec * 1000).toLocaleString("zh-CN", { hour12: false });
}

// 积分取值：兼容站点字段命名差异
export function getPoints(uc) {
  if (!uc) return null;
  return uc.points ?? uc.pointBalance ?? uc.balance ?? null;
}

export const STATUS_LABEL = {
  success: "成功",
  failed: "失败",
  skipped: "跳过",
  partial: "部分成功",
  running: "运行中",
};
