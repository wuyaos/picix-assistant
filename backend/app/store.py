"""SQLite 持久化：配置、会话、登录待确认态、执行历史。

单用户自用工具，直接用标准库 sqlite3，无 ORM。
密钥/会话明文存于 data/picix.db（自部署场景，README 有安全说明）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "schedule_time": "09:00",  # 每日执行时间 HH:MM
    "timezone": "Asia/Shanghai",
    "accept_task_codes": ["D_UL_1", "M_UL_50", "M_UL_ML_20", "M_UL_ML_50"],
    "unlock_list_ids": [],  # 解锁影片来源片单（按顺序）
    "max_unlocks_per_run": 2,  # 单次解锁上限（含冲刺补解），恒定生效
    "monthly_unlocks_per_day": 1,  # 月度任务每日额外解锁节奏（0-5，0=只靠每日任务搭车）
    "mall_enabled": False,  # 固定购买开关（资源包没了就买）
    "mall_good_id": 0,  # 固定购买的商品 id（0=未设置）
    "proxy": "",  # httpx 代理，如 http://127.0.0.1:7890
    "tg_api_id": 0,  # Telegram API 凭据（my.telegram.org 申请）
    "tg_api_hash": "",
    "tg_auto_login": False,  # 登录需确认时自动经 TG 会话发送确认消息
    "notify_target": "me",  # 推送目标：me=保存的消息；@username=公开频道；纯数字=Telegram ID
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL,          -- running / success / failed / skipped / partial
    trigger TEXT NOT NULL,         -- schedule / startup / manual
    detail TEXT NOT NULL DEFAULT '{}'  -- JSON: steps, unlocks, errors
);
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_request_id TEXT NOT NULL UNIQUE,  -- payGood 幂等键，先入库再请求
    good_id INTEGER NOT NULL,
    good_name TEXT,
    price INTEGER,
    status TEXT NOT NULL,          -- pending / success / failed / unknown
    order_code TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    finished_at REAL
);
"""


class Store:
    def __init__(self, data_dir: Path):
        # data_dir 是目录，库文件固定为其中的 picix.db（容器里 /data 是挂载目录）
        self._path = Path(data_dir) / "picix.db"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.Connection(self._path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:  # 事务：成功提交，异常回滚
                yield conn
        finally:
            conn.close()

    # ---- kv ----
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, payload),
            )

    def delete(self, key: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM kv WHERE key=?", (key,))


    def claim_kv(self, key: str, value: Any = True) -> bool:
        """原子占位：键不存在则写入并返回 True；已存在返回 False（不覆盖）。

        用于推送去重（占位即视为已发送，接受可能漏发，不重复打扰）。
        """
        payload = json.dumps(value, ensure_ascii=False)
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
                (key, payload),
            )
            return cur.rowcount > 0
    # ---- config ----
    def get_config(self) -> dict[str, Any]:
        stored = self.get("config", {})
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG and v is not None})
        return cfg

    def update_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        merged = self.get_config()
        merged.update({k: v for k, v in patch.items() if k in DEFAULT_CONFIG})
        self.put("config", merged)
        return merged


    def apply_config_file(self) -> list[str]:
        """读取 data_dir/config.json（宿主机可直接编辑），把已填写的键合并进配置。

        语义：文件优先，容器启动时生效；null/空字符串视为未填写跳过；
        类型按 DEFAULT_CONFIG 现值强转，转不过的键忽略并记入 skipped。
        """
        path = self._path.parent / "config.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"config.json 解析失败: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("config.json 必须是 JSON 对象")
        patch: dict[str, Any] = {}
        for key, value in data.items():
            if key not in DEFAULT_CONFIG or value is None or value == "":
                continue
            default = DEFAULT_CONFIG[key]
            try:
                if isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, int):
                    value = int(value)
                elif isinstance(default, list):
                    if not isinstance(value, list):
                        continue
                else:
                    value = str(value)
            except (TypeError, ValueError):
                continue
            patch[key] = value
        if patch:
            self.update_config(patch)
        return list(patch)

    # ---- session ----
    def get_session(self) -> dict[str, Any] | None:
        return self.get("session")

    def save_session(self, session: dict[str, Any]) -> None:
        session["logged_at"] = time.time()
        self.put("session", session)

    def clear_session(self) -> None:
        self.delete("session")
        self.delete("login_pending")

    def get_login_pending(self) -> dict[str, Any] | None:
        return self.get("login_pending")

    def save_login_pending(self, pending: dict[str, Any]) -> None:
        pending["issued_at"] = time.time()
        self.put("login_pending", pending)

    # ---- runs ----
    def start_run(self, trigger: str) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO runs(started_at, status, trigger) VALUES(?, 'running', ?)",
                (time.time(), trigger),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, detail: dict[str, Any]) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE runs SET finished_at=?, status=?, detail=? WHERE id=?",
                (time.time(), status, json.dumps(detail, ensure_ascii=False), run_id),
            )


    def mark_interrupted_runs(self) -> int:
        """启动时把残留的 running 行置为 failed（上次进程中断）。返回处理行数。"""
        with self._lock, self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, detail FROM runs WHERE status='running'"
            ).fetchall()
            for r in rows:
                try:
                    detail = json.loads(r["detail"] or "{}")
                    if not isinstance(detail, dict):
                        detail = {}
                except json.JSONDecodeError:
                    detail = {}
                detail.setdefault("errors", []).append("上次进程中断")
                conn.execute(
                    "UPDATE runs SET finished_at=?, status='failed', detail=? WHERE id=?",
                    (time.time(), json.dumps(detail, ensure_ascii=False), r["id"]),
                )
            return len(rows)
    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["detail"] = json.loads(item.get("detail") or "{}")
            except json.JSONDecodeError:
                item["detail"] = {}
            out.append(item)
        return out

    def last_run(self, status: str | None = None) -> dict[str, Any] | None:
        runs = self.list_runs(limit=50)
        for r in runs:
            if status is None or r["status"] == status:
                return r
        return None

    # ---- purchases（商城购买记录，幂等）----
    def create_purchase(
        self, client_request_id: str, good_id: int, good_name: str = "", price: int | None = None
    ) -> tuple[int, bool]:
        """返回 (purchase_id, is_new)。重复幂等键复用已有记录。"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM purchases WHERE client_request_id=?", (client_request_id,)
            ).fetchone()
            if row:
                return int(row[0]), False
            cur = conn.execute(
                "INSERT INTO purchases(client_request_id, good_id, good_name, price, status, created_at)"
                " VALUES(?,?,?,?, 'pending', ?)",
                (client_request_id, good_id, good_name, price, time.time()),
            )
            return int(cur.lastrowid), True

    def finish_purchase(
        self,
        purchase_id: int,
        status: str,
        order_code: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE purchases SET status=?, order_code=COALESCE(?, order_code), error=?, finished_at=?"
                " WHERE id=?",
                (status, order_code, error, time.time(), purchase_id),
            )

    def get_purchase(self, purchase_id: int) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT id,client_request_id,good_id,good_name,price,status,order_code,error,created_at,finished_at"
                " FROM purchases WHERE id=?",
                (purchase_id,),
            ).fetchone()
        if not row:
            return None
        keys = (
            "id",
            "client_request_id",
            "good_id",
            "good_name",
            "price",
            "status",
            "order_code",
            "error",
            "created_at",
            "finished_at",
        )
        return dict(zip(keys, row))

    def list_purchases(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT id,client_request_id,good_id,good_name,price,status,order_code,error,created_at,finished_at"
                " FROM purchases ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = (
            "id",
            "client_request_id",
            "good_id",
            "good_name",
            "price",
            "status",
            "order_code",
            "error",
            "created_at",
            "finished_at",
        )
        return [dict(zip(keys, r)) for r in rows]
