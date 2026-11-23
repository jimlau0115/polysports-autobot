import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import DB_PATH, DEFAULT_BOT_CONFIG, get_env_clob_credentials
from app.polymarket_client import normalize_event_key


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_utc_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Database:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=20.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=MEMORY;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        except sqlite3.OperationalError:
            pass
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    sport TEXT NOT NULL,
                    event_title TEXT NOT NULL,
                    question TEXT NOT NULL,
                    market_slug TEXT DEFAULT '',
                    outcome_label TEXT NOT NULL,
                    outcome_index INTEGER DEFAULT 0,
                    trading_mode TEXT NOT NULL, -- PAPER or LIVE
                    trigger_type TEXT NOT NULL, -- AUTO_70_RULE or MANUAL
                    entry_prob REAL NOT NULL,
                    current_prob REAL NOT NULL,
                    exit_prob REAL,
                    shares REAL NOT NULL,
                    amount_usdc REAL NOT NULL,
                    unrealized_pnl_usdc REAL DEFAULT 0.0,
                    realized_pnl_usdc REAL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'OPEN', -- OPEN, CLOSED_TP, CLOSED_SL, CLOSED_MANUAL, SETTLED_WIN
                    order_id TEXT DEFAULT '',
                    trigger_reason TEXT DEFAULT '',
                    end_date TEXT DEFAULT '',
                    opened_at TEXT NOT NULL,
                    closed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_positions_market ON positions(market_id);
                CREATE INDEX IF NOT EXISTS idx_positions_condition ON positions(condition_id);
                CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);

                CREATE TABLE IF NOT EXISTS trade_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL, --EXEC, EXIT, INFO, WARN, ERROR
                    trading_mode TEXT NOT NULL,
                    sport TEXT DEFAULT '',
                    market_id TEXT DEFAULT '',
                    event_title TEXT DEFAULT '',
                    outcome_label TEXT DEFAULT '',
                    prob REAL DEFAULT 0.0,
                    amount_usdc REAL DEFAULT 0.0,
                    message TEXT NOT NULL,
                    details_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_trade_logs_created ON trade_logs(created_at DESC);
                """
            )
            # Schema migration: ensure end_date column exists on existing positions tables
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(positions)").fetchall()}
            if "end_date" not in cols:
                conn.execute("ALTER TABLE positions ADD COLUMN end_date TEXT DEFAULT ''")
            # Ensure default config exists
            cur = conn.execute("SELECT value FROM settings WHERE key = 'bot_config'")
            row = cur.fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("bot_config", json.dumps(DEFAULT_BOT_CONFIG), utc_now_iso()),
                )
            else:
                # Merge any new default keys
                saved = json.loads(row["value"])
                merged = {**DEFAULT_BOT_CONFIG, **saved}
                if merged != saved:
                    conn.execute(
                        "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'bot_config'",
                        (json.dumps(merged), utc_now_iso()),
                    )

            # Initialize CLOB credentials from env if not yet saved
            cur_creds = conn.execute("SELECT value FROM settings WHERE key = 'clob_credentials'")
            if not cur_creds.fetchone():
                env_creds = get_env_clob_credentials()
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("clob_credentials", json.dumps(env_creds), utc_now_iso()),
                )

            # Initialize Admin Auth credentials for LIVE trading protection
            cur_auth = conn.execute("SELECT value FROM settings WHERE key = 'admin_auth'")
            if not cur_auth.fetchone():
                default_pw = os.environ.get("ADMIN_PASSWORD", "poly8888").strip() or "poly8888"
                auth_obj = {
                    "password": default_pw,
                    "token_secret": secrets.token_hex(32),
                }
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("admin_auth", json.dumps(auth_obj), utc_now_iso()),
                )

            # One-time deduplication of any duplicate positions on the same sports event/item
            dedup_row = conn.execute("SELECT value FROM settings WHERE key = 'dedup_positions_by_event_v2'").fetchone()
            if not dedup_row:
                all_pos = conn.execute("SELECT * FROM positions ORDER BY id ASC").fetchall()
                seen_keys = set()
                dup_ids = []
                refund_cash = 0.0
                for p in all_pos:
                    ev_key = normalize_event_key(p["event_title"], p["market_slug"])
                    if not ev_key:
                        ev_key = str(p["market_id"])
                    is_old_future = "end of 2026" in ev_key or "2027 champion" in ev_key
                    if ev_key in seen_keys or (is_old_future and p["trading_mode"] == "PAPER"):
                        dup_ids.append(p["id"])
                        if p["status"] == "OPEN" and p["trading_mode"] == "PAPER":
                            refund_cash += float(p["amount_usdc"] or 0.0)
                    else:
                        seen_keys.add(ev_key)
                if dup_ids:
                    placeholders = ",".join("?" for _ in dup_ids)
                    conn.execute(f"DELETE FROM positions WHERE id IN ({placeholders})", dup_ids)
                    if refund_cash > 0:
                        cfg_r = conn.execute("SELECT value FROM settings WHERE key = 'bot_config'").fetchone()
                        if cfg_r:
                            cfg_obj = json.loads(cfg_r["value"])
                            cfg_obj["paper_cash_balance"] = round(float(cfg_obj.get("paper_cash_balance", 1000.0)) + refund_cash, 2)
                            conn.execute(
                                "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'bot_config'",
                                (json.dumps(cfg_obj), utc_now_iso()),
                            )
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("dedup_positions_by_event_v2", "true", utc_now_iso()),
                )

            # One-time migration: apply +EV strategy optimization (70%-80% entry window, 93% auto TP, 30m/55% SL, $3000 min liquidity, exclude high-upset sports)
            ev_mig = conn.execute("SELECT value FROM settings WHERE key = 'plus_ev_strategy_upgrade_v1'").fetchone()
            if not ev_mig:
                cfg_r = conn.execute("SELECT value FROM settings WHERE key = 'bot_config'").fetchone()
                if cfg_r:
                    cfg_obj = json.loads(cfg_r["value"])
                    cfg_obj["min_win_prob"] = 0.70
                    cfg_obj["max_win_prob"] = 0.80
                    cfg_obj["min_liquidity_usdc"] = 3000.0
                    cfg_obj["auto_take_profit_enabled"] = True
                    cfg_obj["take_profit_prob"] = 0.93
                    cfg_obj["auto_stop_loss_enabled"] = True
                    cfg_obj["stop_loss_minutes_before_end"] = 30
                    cfg_obj["stop_loss_prob"] = 0.55
                    cfg_obj["exclude_high_upset_sports"] = True
                    conn.execute(
                        "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'bot_config'",
                        (json.dumps(cfg_obj), utc_now_iso()),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("plus_ev_strategy_upgrade_v1", "true", utc_now_iso()),
                )

    def get_config(self) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'bot_config'").fetchone()
            if not row:
                return dict(DEFAULT_BOT_CONFIG)
            data = json.loads(row["value"])
            return {**DEFAULT_BOT_CONFIG, **data}

    def update_config(self, partial: Dict[str, Any]) -> Dict[str, Any]:
        current = self.get_config()
        # Type coercion and validation
        bool_keys = {
            "bot_enabled",
            "prevent_duplicate_market",
            "exclude_high_upset_sports",
            "auto_take_profit_enabled",
            "auto_stop_loss_enabled",
        }
        float_keys = {
            "min_win_prob",
            "max_win_prob",
            "bet_amount_usdc",
            "min_liquidity_usdc",
            "min_volume_usdc",
            "max_spread",
            "daily_budget_usdc",
            "take_profit_prob",
            "stop_loss_prob",
            "paper_initial_balance",
            "paper_cash_balance",
        }
        int_keys = {"scan_interval_sec", "max_open_positions", "stop_loss_minutes_before_end"}

        for k, v in partial.items():
            if k not in DEFAULT_BOT_CONFIG:
                continue
            if k in bool_keys:
                current[k] = bool(v)
            elif k in float_keys:
                current[k] = float(v)
            elif k in int_keys:
                if k == "scan_interval_sec":
                    current[k] = max(2, int(v))
                elif k == "stop_loss_minutes_before_end":
                    current[k] = max(0, min(1440, int(v)))
                else:
                    current[k] = max(1, int(v))
            else:
                current[k] = str(v)

        # Ensure sensible bounds
        current["min_win_prob"] = max(0.05, min(0.98, current["min_win_prob"]))
        current["max_win_prob"] = max(current["min_win_prob"] + 0.01, min(0.995, current["max_win_prob"]))
        current["bet_amount_usdc"] = max(1.0, current["bet_amount_usdc"])

        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("bot_config", json.dumps(current), utc_now_iso()),
            )
        return current

    def get_clob_credentials(self) -> Dict[str, Any]:
        env_creds = get_env_clob_credentials()
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'clob_credentials'").fetchone()
            saved = json.loads(row["value"]) if row else {}
        # Prefer saved non-empty values, fallback to env
        merged = {}
        for k in ("private_key", "funder_address", "api_key", "api_secret", "api_passphrase"):
            merged[k] = saved.get(k) or env_creds.get(k) or ""
        merged["signature_type"] = int(saved.get("signature_type", env_creds.get("signature_type", 1)))
        return merged

    def get_masked_clob_credentials(self) -> Dict[str, Any]:
        creds = self.get_clob_credentials()
        def _mask(val: str) -> str:
            if not val:
                return ""
            if len(val) <= 10:
                return "*" * len(val)
            return f"{val[:6]}...{val[-4:]}"

        return {
            "has_private_key": bool(creds.get("private_key")),
            "private_key_masked": _mask(creds.get("private_key", "")),
            "funder_address": creds.get("funder_address", ""),
            "has_api_key": bool(creds.get("api_key")),
            "api_key_masked": _mask(creds.get("api_key", "")),
            "signature_type": creds.get("signature_type", 1),
            "ready_for_live": bool(creds.get("private_key")),
        }

    def update_clob_credentials(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        current = self.get_clob_credentials()
        for k in ("private_key", "funder_address", "api_key", "api_secret", "api_passphrase"):
            if k in payload and payload[k] is not None:
                val = str(payload[k]).strip()
                # Only overwrite if not masked placeholder
                if "..." not in val and not val.startswith("***"):
                    current[k] = val
        if "signature_type" in payload:
            current["signature_type"] = int(payload["signature_type"])
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("clob_credentials", json.dumps(current), utc_now_iso()),
            )
        return self.get_masked_clob_credentials()

    def _get_admin_auth_obj(self) -> Dict[str, str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'admin_auth'").fetchone()
            if not row:
                return {
                    "password": os.environ.get("ADMIN_PASSWORD", "poly8888").strip() or "poly8888",
                    "token_secret": "default-secret-polyquant",
                }
            return json.loads(row["value"])

    def verify_admin_password(self, password: str) -> bool:
        if not password:
            return False
        pw = str(password).strip()
        env_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
        auth_obj = self._get_admin_auth_obj()
        saved_pw = str(auth_obj.get("password", "poly8888")).strip()
        if env_pw and hmac.compare_digest(pw, env_pw):
            return True
        return hmac.compare_digest(pw, saved_pw)

    def update_admin_password(self, current_password: str, new_password: str) -> bool:
        if not self.verify_admin_password(current_password):
            return False
        new_pw = str(new_password).strip()
        if len(new_pw) < 4:
            raise ValueError("新密碼長度至少需 4 個字元")
        auth_obj = {
            "password": new_pw,
            "token_secret": secrets.token_hex(32),
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("admin_auth", json.dumps(auth_obj), utc_now_iso()),
            )
        return True

    def create_admin_token(self) -> str:
        auth_obj = self._get_admin_auth_obj()
        secret = str(auth_obj.get("token_secret", "default-secret")).encode("utf-8")
        return hmac.new(secret, b"polyquant-live-admin-session", hashlib.sha256).hexdigest()

    def verify_admin_token(self, token: Optional[str]) -> bool:
        if not token or not str(token).strip():
            return False
        expected = self.create_admin_token()
        return hmac.compare_digest(str(token).strip(), expected)

    def has_bet_on_market(
        self,
        market_id: str,
        condition_id: str,
        market_slug: str = "",
        event_title: str = "",
        trading_mode: Optional[str] = None,
    ) -> bool:
        target_ev_key = normalize_event_key(event_title, market_slug)
        mode_filter = str(trading_mode).upper() if trading_mode else None
        with self._connect() as conn:
            if mode_filter:
                row = conn.execute(
                    """
                    SELECT 1 FROM positions
                    WHERE trading_mode = ?
                      AND (
                        market_id = ?
                        OR (condition_id != '' AND condition_id = ?)
                        OR (? != '' AND LOWER(TRIM(market_slug)) = LOWER(TRIM(?)))
                        OR (? != '' AND LOWER(TRIM(event_title)) = LOWER(TRIM(?)))
                      )
                    LIMIT 1
                    """,
                    (mode_filter, market_id, condition_id, market_slug, market_slug, event_title, event_title),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT 1 FROM positions
                    WHERE market_id = ?
                       OR (condition_id != '' AND condition_id = ?)
                       OR (? != '' AND LOWER(TRIM(market_slug)) = LOWER(TRIM(?)))
                       OR (? != '' AND LOWER(TRIM(event_title)) = LOWER(TRIM(?)))
                    LIMIT 1
                    """,
                    (market_id, condition_id, market_slug, market_slug, event_title, event_title),
                ).fetchone()
            if row is not None:
                return True
            if target_ev_key:
                if mode_filter:
                    all_rows = conn.execute(
                        "SELECT event_title, market_slug FROM positions WHERE trading_mode = ?",
                        (mode_filter,),
                    ).fetchall()
                else:
                    all_rows = conn.execute("SELECT event_title, market_slug FROM positions").fetchall()
                for r in all_rows:
                    if normalize_event_key(r["event_title"], r["market_slug"]) == target_ev_key:
                        return True
            return False

    def get_today_spend_usdc(self, trading_mode: Optional[str] = None) -> float:
        prefix = today_utc_prefix()
        query = "SELECT COALESCE(SUM(amount_usdc), 0.0) AS spend FROM positions WHERE opened_at LIKE ?"
        params: List[Any] = [f"{prefix}%"]
        if trading_mode:
            query += " AND trading_mode = ?"
            params.append(trading_mode)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
            return float(row["spend"]) if row else 0.0

    def count_open_positions(self, trading_mode: Optional[str] = None) -> int:
        query = "SELECT COUNT(*) AS cnt FROM positions WHERE status = 'OPEN'"
        params: List[Any] = []
        if trading_mode:
            query += " AND trading_mode = ?"
            params.append(trading_mode)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
            return int(row["cnt"]) if row else 0

    def create_position(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO positions (
                    market_id, condition_id, token_id, sport, event_title, question,
                    market_slug, outcome_label, outcome_index, trading_mode, trigger_type,
                    entry_prob, current_prob, shares, amount_usdc, unrealized_pnl_usdc,
                    realized_pnl_usdc, status, order_id, trigger_reason, end_date, opened_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
                """,
                (
                    str(pos["market_id"]),
                    str(pos.get("condition_id", "")),
                    str(pos.get("token_id", "")),
                    str(pos.get("sport", "SPORTS")),
                    str(pos.get("event_title", "")),
                    str(pos.get("question", "")),
                    str(pos.get("market_slug", "")),
                    str(pos["outcome_label"]),
                    int(pos.get("outcome_index", 0)),
                    str(pos.get("trading_mode", "PAPER")),
                    str(pos.get("trigger_type", "AUTO_70_RULE")),
                    float(pos["entry_prob"]),
                    float(pos.get("current_prob", pos["entry_prob"])),
                    float(pos["shares"]),
                    float(pos["amount_usdc"]),
                    0.0,
                    0.0,
                    str(pos.get("order_id", "")),
                    str(pos.get("trigger_reason", "")),
                    str(pos.get("end_date", "")),
                    now,
                ),
            )
            pos_id = cur.lastrowid
            row = conn.execute("SELECT * FROM positions WHERE id = ?", (pos_id,)).fetchone()
            return dict(row)

    def get_positions(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        trading_mode: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM positions"
        clauses: List[str] = []
        params: List[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if trading_mode:
            clauses.append("trading_mode = ?")
            params.append(str(trading_mode).upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def update_open_position_mark(
        self, pos_id: int, current_prob: float, end_date: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM positions WHERE id = ? AND status = 'OPEN'", (pos_id,)).fetchone()
            if not row:
                return None
            shares = float(row["shares"])
            amount_usdc = float(row["amount_usdc"])
            market_value = shares * current_prob
            unrealized = round(market_value - amount_usdc, 2)
            effective_end = str(end_date or row["end_date"] or "")
            conn.execute(
                "UPDATE positions SET current_prob = ?, unrealized_pnl_usdc = ?, end_date = ? WHERE id = ?",
                (round(current_prob, 4), unrealized, effective_end, pos_id),
            )
            updated = conn.execute("SELECT * FROM positions WHERE id = ?", (pos_id,)).fetchone()
            return dict(updated) if updated else None

    def close_position(
        self, pos_id: int, exit_prob: float, close_status: str = "CLOSED_MANUAL"
    ) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM positions WHERE id = ? AND status = 'OPEN'", (pos_id,)).fetchone()
            if not row:
                return None
            shares = float(row["shares"])
            amount_usdc = float(row["amount_usdc"])
            payout = shares * exit_prob
            realized_pnl = round(payout - amount_usdc, 2)
            conn.execute(
                """
                UPDATE positions
                SET status = ?, current_prob = ?, exit_prob = ?,
                    unrealized_pnl_usdc = 0.0, realized_pnl_usdc = ?, closed_at = ?
                WHERE id = ?
                """,
                (close_status, round(exit_prob, 4), round(exit_prob, 4), realized_pnl, now, pos_id),
            )
            # Credit back paper cash balance if PAPER mode
            if row["trading_mode"] == "PAPER":
                cfg_row = conn.execute("SELECT value FROM settings WHERE key = 'bot_config'").fetchone()
                if cfg_row:
                    cfg = json.loads(cfg_row["value"])
                    cfg["paper_cash_balance"] = round(float(cfg.get("paper_cash_balance", 1000.0)) + payout, 2)
                    conn.execute(
                        "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'bot_config'",
                        (json.dumps(cfg), now),
                    )
            updated = conn.execute("SELECT * FROM positions WHERE id = ?", (pos_id,)).fetchone()
            return dict(updated) if updated else None

    def add_log(
        self,
        level: str,
        trading_mode: str,
        message: str,
        sport: str = "",
        market_id: str = "",
        event_title: str = "",
        outcome_label: str = "",
        prob: float = 0.0,
        amount_usdc: float = 0.0,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        details_str = json.dumps(details or {}, ensure_ascii=False)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO trade_logs (
                    level, trading_mode, sport, market_id, event_title,
                    outcome_label, prob, amount_usdc, message, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    level,
                    trading_mode,
                    sport,
                    str(market_id),
                    event_title,
                    outcome_label,
                    round(float(prob), 4),
                    round(float(amount_usdc), 2),
                    message,
                    details_str,
                    now,
                ),
            )
            log_id = cur.lastrowid
            # Keep logs table bounded to latest 500 entries
            conn.execute(
                "DELETE FROM trade_logs WHERE id NOT IN (SELECT id FROM trade_logs ORDER BY id DESC LIMIT 500)"
            )
            row = conn.execute("SELECT * FROM trade_logs WHERE id = ?", (log_id,)).fetchone()
            return dict(row)

    def get_logs(self, limit: int = 80, trading_mode: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if trading_mode:
                rows = conn.execute(
                    "SELECT * FROM trade_logs WHERE trading_mode = ? ORDER BY id DESC LIMIT ?",
                    (str(trading_mode).upper(), limit),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM trade_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_portfolio_summary(self, trading_mode: str = "PAPER") -> Dict[str, Any]:
        mode_upper = str(trading_mode or "PAPER").upper()
        cfg = self.get_config()
        with self._connect() as conn:
            open_rows = conn.execute(
                "SELECT * FROM positions WHERE status = 'OPEN' AND trading_mode = ?",
                (mode_upper,),
            ).fetchall()
            closed_rows = conn.execute(
                "SELECT * FROM positions WHERE status != 'OPEN' AND trading_mode = ?",
                (mode_upper,),
            ).fetchall()

        open_invested = sum(float(r["amount_usdc"]) for r in open_rows)
        open_unrealized = sum(float(r["unrealized_pnl_usdc"]) for r in open_rows)
        open_market_value = round(open_invested + open_unrealized, 2)

        realized_pnl = sum(float(r["realized_pnl_usdc"]) for r in closed_rows)
        wins = sum(1 for r in closed_rows if float(r["realized_pnl_usdc"]) > 0)
        losses = sum(1 for r in closed_rows if float(r["realized_pnl_usdc"]) <= 0)
        closed_count = len(closed_rows)
        win_rate = round((wins / closed_count) * 100, 1) if closed_count > 0 else 0.0

        total_pnl = round(realized_pnl + open_unrealized, 2)
        if mode_upper == "LIVE":
            total_invested_ever = open_invested + sum(float(r["amount_usdc"]) for r in closed_rows)
            roi_pct = round((total_pnl / total_invested_ever) * 100, 2) if total_invested_ever > 0 else 0.0
            paper_cash = 0.0
            paper_initial = round(total_invested_ever, 2)
            paper_total_equity = round(open_market_value + realized_pnl, 2)
        else:
            paper_cash = round(float(cfg.get("paper_cash_balance", 1000.0)), 2)
            paper_initial = round(float(cfg.get("paper_initial_balance", 1000.0)), 2)
            paper_total_equity = round(paper_cash + open_market_value, 2)
            roi_pct = round((total_pnl / paper_initial) * 100, 2) if paper_initial > 0 else 0.0

        return {
            "trading_mode": mode_upper,
            "paper_initial_balance": paper_initial,
            "paper_cash_balance": paper_cash,
            "open_positions_count": len(open_rows),
            "closed_positions_count": closed_count,
            "open_invested_usdc": round(open_invested, 2),
            "open_market_value_usdc": open_market_value,
            "unrealized_pnl_usdc": round(open_unrealized, 2),
            "realized_pnl_usdc": round(realized_pnl, 2),
            "total_pnl_usdc": total_pnl,
            "paper_total_equity_usdc": paper_total_equity,
            "roi_pct": roi_pct,
            "win_rate_pct": win_rate,
            "wins": wins,
            "losses": losses,
            "today_spend_usdc": round(self.get_today_spend_usdc(trading_mode=mode_upper), 2),
            "daily_budget_usdc": round(float(cfg.get("daily_budget_usdc", 300.0)), 2),
        }

    def reset_paper_portfolio(self, initial_balance: float = 1000.0) -> Dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute("DELETE FROM positions WHERE trading_mode = 'PAPER'")
            conn.execute("DELETE FROM trade_logs WHERE trading_mode = 'PAPER'")
            try:
                conn.execute("DELETE FROM hk_temp_trades WHERE LOWER(mode) = 'paper'")
            except Exception:
                pass
            row = conn.execute("SELECT value FROM settings WHERE key = 'bot_config'").fetchone()
            cfg = json.loads(row["value"]) if row else dict(DEFAULT_BOT_CONFIG)
            cfg["paper_initial_balance"] = float(initial_balance)
            cfg["paper_cash_balance"] = float(initial_balance)
            conn.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'bot_config'",
                (json.dumps(cfg), now),
            )
        self.add_log(
            level="INFO",
            trading_mode="PAPER",
            message=f"模擬交易帳戶已重置，初始資金設定為 ${initial_balance:,.2f} USDC",
        )
        return self.get_portfolio_summary()
