import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from app.config import DB_PATH, GAMMA_API_BASE
from app.database import Database
from app.polymarket_client import PolymarketClient, _parse_json_list

HK_TZ = timezone(timedelta(hours=8))

DEFAULT_HK_TEMP_CONFIG = {
    "auto_enabled": True,
    "mode": "PAPER",  # "PAPER" or "LIVE"
    "gate_hour_hkt": 11,
    "price_min": 0.70,  # 70% or above, no upper cap
    "stake_usdc": 10.0,
    "stake_min_usdc": 1.0,
    "stake_max_usdc": 1000.0,
    "one_bet_per_day": True,
    "aws_finalize_hour_hkt": 19,
    "migrated_70_v1": True,
}

INITIAL_WEATHER_HISTORY = [
    {"marketDate": "2026-11-19", "forecastMaxC": 22.0, "forecastIssuedAt": "2026-11-19T00:00:00+08:00", "forecastDate": "20261119", "officialMaxC": None, "officialLabel": "觀測中", "awsMaxC": 22.0, "awsOccurredAt": "2026-11-19T00:10:00+08:00", "awsFinalized": True, "awsTimeLabel": "00:10", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-11-18", "forecastMaxC": 27.0, "forecastIssuedAt": "2026-11-18T00:00:00+08:00", "forecastDate": "20261118", "officialMaxC": None, "officialLabel": "觀測中", "awsMaxC": 27.3, "awsOccurredAt": "2026-11-18T11:20:00+08:00", "awsFinalized": True, "awsTimeLabel": "11:20", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-31", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 29.2, "officialLabel": "29.2°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-30", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 30.2, "officialLabel": "30.2°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-29", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 29.0, "officialLabel": "29°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-28", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 27.2, "officialLabel": "27.2°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-27", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 29.7, "officialLabel": "29.7°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-26", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 30.3, "officialLabel": "30.3°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-25", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 30.5, "officialLabel": "30.5°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-24", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 31.9, "officialLabel": "31.9°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-23", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 32.3, "officialLabel": "32.3°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-22", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 30.7, "officialLabel": "30.7°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-21", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 32.5, "officialLabel": "32.5°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-20", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 31.8, "officialLabel": "31.8°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-19", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 31.9, "officialLabel": "31.9°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
    {"marketDate": "2026-10-18", "forecastMaxC": None, "forecastIssuedAt": None, "forecastDate": None, "officialMaxC": 31.5, "officialLabel": "31.5°C", "awsMaxC": None, "awsOccurredAt": None, "awsFinalized": False, "awsTimeLabel": "—", "awsTimeDetail": "", "forecastErrorC": None},
]


def now_hkt() -> datetime:
    return datetime.now(HK_TZ)


class HkTempEngine:
    """
    Hong Kong Daily Highest Temperature Polymarket Auto-Trading Engine.
    Integrates all features of https://hk-temp-trading.vercel.app/ into PolySports.
    Rule: After 11:00 HKT, monitors all 'Yes' temperature buckets for today's
    'Highest temperature in Hong Kong' market; when any bucket reaches >= 75% (no upper cap),
    immediately places a bet with the configured stake (1 bet per market day).
    """

    def __init__(self, db: Database, poly_client: PolymarketClient, db_path: Path = DB_PATH) -> None:
        self.db = db
        self.poly_client = poly_client
        self.db_path = db_path
        self._init_tables()
        self.last_dashboard_data: Optional[Dict[str, Any]] = None
        self.observatory_state: str = "live"
        self.polymarket_state: str = "live"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        now_iso = now_hkt().isoformat()
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS hk_weather_records (
                    market_date TEXT PRIMARY KEY,
                    forecast_max_c REAL,
                    forecast_issued_at TEXT,
                    forecast_date TEXT,
                    official_max_c REAL,
                    official_label TEXT DEFAULT '觀測中',
                    aws_max_c REAL,
                    aws_occurred_at TEXT,
                    aws_finalized INTEGER DEFAULT 0,
                    aws_time_label TEXT DEFAULT '—',
                    aws_time_detail TEXT DEFAULT '',
                    forecast_error_c REAL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hk_temp_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_date TEXT NOT NULL,
                    market_slug TEXT DEFAULT '',
                    outcome_name TEXT NOT NULL,
                    token_id TEXT DEFAULT '',
                    side TEXT DEFAULT 'YES',
                    mode TEXT NOT NULL, -- PAPER or LIVE
                    trigger_type TEXT DEFAULT 'AUTO_75_HKT',
                    price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    stake_usdc REAL NOT NULL,
                    size_shares REAL NOT NULL,
                    status TEXT DEFAULT 'filled', -- filled, submitted, failed, settled_win, settled_loss
                    pnl_usdc REAL DEFAULT 0.0,
                    order_id TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )
            row = conn.execute("SELECT value FROM settings WHERE key = 'hk_temp_config'").fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("hk_temp_config", json.dumps(DEFAULT_HK_TEMP_CONFIG), now_iso),
                )
            else:
                try:
                    saved_cfg = json.loads(row["value"])
                    if not saved_cfg.get("migrated_70_v1"):
                        saved_cfg["price_min"] = 0.70
                        saved_cfg["migrated_70_v1"] = True
                        conn.execute(
                            "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'hk_temp_config'",
                            (json.dumps(saved_cfg), now_iso),
                        )
                except Exception:
                    pass

            # Seed initial historical weather records
            for rec in INITIAL_WEATHER_HISTORY:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO hk_weather_records (
                        market_date, forecast_max_c, forecast_issued_at, forecast_date,
                        official_max_c, official_label, aws_max_c, aws_occurred_at,
                        aws_finalized, aws_time_label, aws_time_detail, forecast_error_c, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rec["marketDate"],
                        rec["forecastMaxC"],
                        rec["forecastIssuedAt"],
                        rec["forecastDate"],
                        rec["officialMaxC"],
                        rec["officialLabel"],
                        rec["awsMaxC"],
                        rec["awsOccurredAt"],
                        1 if rec["awsFinalized"] else 0,
                        rec["awsTimeLabel"],
                        rec["awsTimeDetail"],
                        rec["forecastErrorC"],
                        now_iso,
                    ),
                )

    def get_config(self) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'hk_temp_config'").fetchone()
            if not row:
                return dict(DEFAULT_HK_TEMP_CONFIG)
            saved = json.loads(row["value"])
            return {**DEFAULT_HK_TEMP_CONFIG, **saved}

    def update_config(self, partial: Dict[str, Any]) -> Dict[str, Any]:
        cfg = self.get_config()
        if "auto_enabled" in partial:
            cfg["auto_enabled"] = bool(partial["auto_enabled"])
        if "mode" in partial:
            cfg["mode"] = str(partial["mode"]).upper()
        if "gate_hour_hkt" in partial:
            cfg["gate_hour_hkt"] = max(0, min(23, int(partial["gate_hour_hkt"])))
        if "price_min" in partial:
            cfg["price_min"] = max(0.05, min(0.99, float(partial["price_min"])))
        if "stake_usdc" in partial:
            val = float(partial["stake_usdc"])
            cfg["stake_usdc"] = max(cfg["stake_min_usdc"], min(cfg["stake_max_usdc"], val))
        if "one_bet_per_day" in partial:
            cfg["one_bet_per_day"] = bool(partial["one_bet_per_day"])

        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("hk_temp_config", json.dumps(cfg), now_hkt().isoformat()),
            )
        return cfg

    def has_traded_market_date(self, market_date: str, mode: Optional[str] = None) -> bool:
        with self._connect() as conn:
            if mode:
                row = conn.execute(
                    "SELECT 1 FROM hk_temp_trades WHERE market_date = ? AND LOWER(mode) = LOWER(?) LIMIT 1",
                    (market_date, str(mode)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM hk_temp_trades WHERE market_date = ? LIMIT 1",
                    (market_date,),
                ).fetchone()
            return row is not None

    def get_trades(self, limit: int = 60, mode: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if mode:
                rows = conn.execute(
                    "SELECT * FROM hk_temp_trades WHERE LOWER(mode) = LOWER(?) ORDER BY id DESC LIMIT ?",
                    (str(mode), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM hk_temp_trades ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_summary_for_mode(self, mode: str) -> Dict[str, Any]:
        trades = self.get_trades(100, mode=mode)
        open_trades = [t for t in trades if t["status"] in ("filled", "submitted")]
        exposure_usdc = round(sum(float(t["stake_usdc"]) for t in open_trades), 2)
        unrealized_pnl = round(sum(float(t["pnl_usdc"]) for t in open_trades), 2)
        settled_trades = [t for t in trades if t["status"] in ("settled_win", "settled_loss")]
        realized_pnl = round(sum(float(t["pnl_usdc"]) for t in settled_trades), 2)
        wins = sum(1 for t in settled_trades if float(t["pnl_usdc"]) > 0)
        losses = sum(1 for t in settled_trades if float(t["pnl_usdc"]) <= 0)
        win_rate = round((wins / len(settled_trades)) * 100, 1) if settled_trades else None
        return {
            "openCount": len(open_trades),
            "exposureUsdc": exposure_usdc,
            "unrealizedPnl": unrealized_pnl,
            "realizedPnl": realized_pnl,
            "wins": wins,
            "losses": losses,
            "winRatePct": win_rate,
        }

    def get_weather_records(self, limit: int = 40) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM hk_weather_records ORDER BY market_date DESC LIMIT ?", (limit,)
            ).fetchall()
            out = []
            for r in rows:
                out.append(
                    {
                        "marketDate": r["market_date"],
                        "forecastMaxC": r["forecast_max_c"],
                        "forecastIssuedAt": r["forecast_issued_at"],
                        "forecastDate": r["forecast_date"],
                        "officialMaxC": r["official_max_c"],
                        "officialLabel": r["official_label"],
                        "awsMaxC": r["aws_max_c"],
                        "awsOccurredAt": r["aws_occurred_at"],
                        "awsFinalized": bool(r["aws_finalized"]),
                        "awsTimeLabel": r["aws_time_label"],
                        "awsTimeDetail": r["aws_time_detail"],
                        "forecastErrorC": r["forecast_error_c"],
                    }
                )
            return out

    async def scan_and_evaluate(self) -> Dict[str, Any]:
        cfg = self.get_config()
        dt_hkt = now_hkt()
        market_date = dt_hkt.strftime("%Y-%m-%d")
        gate_hour = int(cfg.get("gate_hour_hkt", 11))
        gate_open = dt_hkt.hour >= gate_hour
        price_min = float(cfg.get("price_min", 0.70))

        # 1. Fetch HKO weather + Polymarket daily HK temperature market
        weather_info = await self._fetch_hko_weather(dt_hkt, market_date)
        market_info = await self._fetch_polymarket_hk_temp_market(dt_hkt, price_min, gate_open)

        # 2. Update mark-to-market on today's trades
        if market_info and market_info.get("outcomes"):
            self._update_trades_mark(market_date, market_info["outcomes"])

        # 3. Evaluate Auto-Betting Rule (After 11:00 HKT, any 'Yes' outcome >= 70%, 1 bet per marketDate per mode)
        active_mode = str(cfg.get("mode", "PAPER")).upper()
        already_traded_today = self.has_traded_market_date(market_date, mode=active_mode)
        qualified_outcomes = [
            o for o in (market_info.get("outcomes") if market_info else []) if o.get("inBand")
        ]

        phase = "disabled"
        phase_label = "自動交易已關閉"
        phase_msg = "目前未開啟香港氣溫自動交易。"

        if cfg.get("auto_enabled"):
            if already_traded_today:
                trades_today = [
                    t for t in self.get_trades(20, mode=active_mode) if t["market_date"] == market_date
                ]
                first_t = trades_today[0] if trades_today else None
                phase = "ordered"
                phase_label = "本日已完成自動投注"
                if first_t:
                    phase_msg = (
                        f"已於今日觸發並買入「{first_t['outcome_name']}」@ {first_t['price'] * 100:.1f}% "
                        f"({first_t['stake_usdc']:.2f} USDC)。同一市場日只自動投注第一個達標選項。"
                    )
                else:
                    phase_msg = "同一市場日只自動投注第一個達標的「是」選項。"
            elif not gate_open:
                phase = "standby"
                phase_label = f"待命（{gate_hour:02d}:00 開始監測）"
                phase_msg = f"自動交易已開啟，將於香港時間今日 {gate_hour:02d}:00 後開始監測所有「是」選項。"
            elif not market_info or not market_info.get("outcomes"):
                phase = "no_market"
                phase_label = "等待市場開盤"
                phase_msg = "找不到今日「香港最高氣溫」市場，可能尚未開市或已結算。"
            elif qualified_outcomes:
                target = qualified_outcomes[0]
                placed = await self._execute_hk_temp_bet(
                    market_date=market_date,
                    market_slug=market_info.get("slug", ""),
                    outcome=target,
                    stake_usdc=float(cfg.get("stake_usdc", 10.0)),
                    mode=active_mode,
                    trigger_type="AUTO_70_HKT",
                )
                if placed:
                    phase = "ordered"
                    phase_label = "已達標並送出委託"
                    phase_msg = (
                        f"香港時間 {gate_hour:02d}:00 後「{target['name']}」達到 {target['execPrice'] * 100:.1f}% "
                        f"(>= {price_min * 100:.0f}%)，已立即以 {cfg['stake_usdc']:.2f} USDC 自動投注！"
                    )
                else:
                    phase = "failed"
                    phase_label = "自動下單未完成"
                    phase_msg = "達標嘗試下單時未完成，請檢查模擬餘額或 CLOB 金鑰設定。"
            else:
                phase = "monitoring"
                phase_label = "即時監測中"
                phase_msg = (
                    f"香港時間 {gate_hour:02d}:00 閘門已開啟，正監測當日所有「是」選項；"
                    f"價格達到 {price_min * 100:.0f}% 或以上時立即以 {cfg['stake_usdc']:.0f} USDC 自動投注（無價格上限）。"
                )

        paper_summary = self.get_summary_for_mode("PAPER")
        live_summary = self.get_summary_for_mode("LIVE")
        paper_trades = self.get_trades(40, mode="PAPER")
        live_trades = self.get_trades(40, mode="LIVE")

        state = {
            "nowHkt": dt_hkt.isoformat(),
            "marketDate": market_date,
            "gateOpen": gate_open,
            "gateHour": gate_hour,
            "observatoryState": self.observatory_state,
            "polymarketState": self.polymarket_state,
            "current": weather_info["current"],
            "forecast": weather_info["forecast"],
            "officialMax": weather_info["officialMax"],
            "maxTime": weather_info["maxTime"],
            "market": market_info,
            "engine": {
                "autoEnabled": cfg["auto_enabled"],
                "mode": cfg["mode"].lower(),
                "priceMin": price_min,
                "stakeUsdc": cfg["stake_usdc"],
                "stakeMin": cfg["stake_min_usdc"],
                "stakeMax": cfg["stake_max_usdc"],
                "oneBetPerDay": cfg["one_bet_per_day"],
                "phase": phase,
                "phaseLabel": phase_label,
                "message": phase_msg,
                "hasClobCredentials": self.db.get_masked_clob_credentials().get("ready_for_live", False),
                "paperCash": self.db.get_config().get("paper_cash_balance", 1000.0),
            },
            "summary": paper_summary,
            "trades": paper_trades,
            "summaryByMode": {
                "PAPER": paper_summary,
                "LIVE": live_summary,
            },
            "tradesByMode": {
                "PAPER": paper_trades,
                "LIVE": live_trades,
            },
            "weatherRecords": self.get_weather_records(30),
        }
        self.last_dashboard_data = state
        return state

    async def _fetch_hko_weather(self, dt_hkt: datetime, market_date: str) -> Dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=6.0)
        current_temp = {
            "place": "香港天文臺",
            "tempC": 20.0,
            "unit": "C",
            "recordTime": dt_hkt.replace(minute=0, second=0, microsecond=0).isoformat(),
            "stale": False,
            "ageMinutes": dt_hkt.minute,
        }
        forecast_info = {
            "maxC": 22.0,
            "minC": 18.0,
            "forecastDate": dt_hkt.strftime("%Y%m%d"),
            "issuedAt": dt_hkt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            "weather": "大致多雲，有一兩陣微雨。",
            "locked": True,
        }
        official_max = {"status": "observing", "label": "觀測中", "tempC": None}
        max_time = {
            "label": "00:10",
            "annotation": "最高溫出現時間是香港天文臺站開放數據「午夜至今最高氣溫」首次出現在十分鐘公布檔的時間，準確至約 10 分鐘。",
        }

        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Fetch direct HKO RHRREAD API for real-time Hong Kong Observatory temperature
            try:
                rhr_url = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=tc"
                async with session.get(rhr_url) as resp:
                    if resp.status == 200:
                        rhr = await resp.json()
                        temps = rhr.get("temperature", {}).get("data", [])
                        hko_entry = next(
                            (x for x in temps if "天文台" in str(x.get("place", "")) or "天文臺" in str(x.get("place", ""))),
                            temps[0] if temps else None,
                        )
                        rec_time_str = rhr.get("temperature", {}).get("recordTime") or current_temp["recordTime"]
                        if hko_entry:
                            val_c = float(hko_entry.get("value", 20.0))
                            try:
                                rec_dt = datetime.fromisoformat(rec_time_str)
                                age_min = max(0, int((dt_hkt - rec_dt).total_seconds() // 60))
                            except Exception:
                                age_min = 0
                            current_temp = {
                                "place": "香港天文臺",
                                "tempC": val_c,
                                "unit": "C",
                                "recordTime": rec_time_str,
                                "stale": age_min > 30,
                                "ageMinutes": age_min,
                            }
                            self.observatory_state = "delayed" if age_min > 30 else "live"
            except Exception:
                self.observatory_state = "delayed"

            # 2. Fetch HKO Local Weather Forecast (flw) for forecast max/min & weather overview
            try:
                flw_url = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=flw&lang=tc"
                async with session.get(flw_url) as resp:
                    if resp.status == 200:
                        flw = await resp.json()
                        desc = str(flw.get("forecastDesc") or flw.get("generalSituation") or "").strip()
                        if desc:
                            forecast_info["weather"] = desc[:90]
            except Exception:
                pass

        # Update today's row in hk_weather_records if needed
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM hk_weather_records WHERE market_date = ?", (market_date,)
            ).fetchone()
            if not row:
                conn.execute(
                    """
                    INSERT INTO hk_weather_records (
                        market_date, forecast_max_c, forecast_issued_at, forecast_date,
                        official_max_c, official_label, aws_max_c, aws_occurred_at,
                        aws_finalized, aws_time_label, aws_time_detail, forecast_error_c, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        market_date,
                        forecast_info["maxC"],
                        forecast_info["issuedAt"],
                        forecast_info["forecastDate"],
                        None,
                        "觀測中",
                        current_temp["tempC"],
                        current_temp["recordTime"],
                        0,
                        dt_hkt.strftime("%H:%M"),
                        "",
                        None,
                        dt_hkt.isoformat(),
                    ),
                )
            else:
                if row["aws_max_c"] is not None:
                    max_time["label"] = row["aws_time_label"] or max_time["label"]
                if row["forecast_max_c"] is not None:
                    forecast_info["maxC"] = float(row["forecast_max_c"])

        return {
            "current": current_temp,
            "forecast": forecast_info,
            "officialMax": official_max,
            "maxTime": max_time,
        }

    async def _fetch_polymarket_hk_temp_market(
        self, dt_hkt: datetime, price_min: float, gate_open: bool
    ) -> Dict[str, Any]:
        month_name = dt_hkt.strftime("%B").lower()
        day_num = dt_hkt.day
        year_num = dt_hkt.year
        candidate_slugs = [
            f"highest-temperature-in-hong-kong-on-{month_name}-{day_num}-{year_num}",
            f"highest-temperature-in-hong-kong-on-{month_name}-{day_num}",
        ]

        timeout = aiohttp.ClientTimeout(total=7.0)
        headers = {"Accept": "application/json", "User-Agent": "PolySports-HKTemp/1.0"}

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for slug in candidate_slugs:
                try:
                    url = f"{GAMMA_API_BASE}/events"
                    async with session.get(url, params={"slug": slug}) as resp:
                        if resp.status != 200:
                            continue
                        events = await resp.json()
                        if isinstance(events, list) and events:
                            self.polymarket_state = "live"
                            return self._parse_hk_temp_event(events[0], price_min, gate_open)
                except Exception:
                    continue

            # Fallback: search by seriesSlug or title query
            try:
                url = f"{GAMMA_API_BASE}/events"
                async with session.get(
                    url, params={"tag_slug": "weather", "active": "true", "closed": "false", "limit": "50"}
                ) as resp:
                    if resp.status == 200:
                        events = await resp.json()
                        for ev in events if isinstance(events, list) else []:
                            if "hong kong" in str(ev.get("title", "")).lower() and "temperature" in str(ev.get("title", "")).lower():
                                self.polymarket_state = "live"
                                return self._parse_hk_temp_event(ev, price_min, gate_open)
            except Exception:
                pass

        self.polymarket_state = "offline"
        return {
            "title": f"Highest temperature in Hong Kong on {dt_hkt.strftime('%B')} {day_num}?",
            "slug": candidate_slugs[0],
            "status": "unavailable",
            "statusLabel": "未開盤或連線中",
            "outcomes": [],
        }

    def _parse_hk_temp_event(
        self, ev: Dict[str, Any], price_min: float, gate_open: bool
    ) -> Dict[str, Any]:
        markets = ev.get("markets") or []
        outcomes_out = []
        for m in markets:
            if not isinstance(m, dict):
                continue
            name = str(m.get("groupItemTitle") or m.get("question") or "").strip()
            question = str(m.get("question") or "").strip()
            prices = _parse_json_list(m.get("outcomePrices"))
            tokens = _parse_json_list(m.get("clobTokenIds"))

            gamma_yes = 0.0
            if prices:
                try:
                    gamma_yes = float(prices[0])
                except (ValueError, TypeError):
                    gamma_yes = 0.0

            best_bid = float(m["bestBid"]) if m.get("bestBid") is not None else max(0.0, round(gamma_yes - 0.001, 4))
            best_ask = float(m["bestAsk"]) if m.get("bestAsk") is not None else min(1.0, round(gamma_yes + 0.001, 4))
            yes_price = round((best_bid + best_ask) / 2.0, 4) if (best_bid or best_ask) else gamma_yes
            exec_price = best_ask if best_ask > 0 else yes_price
            in_band = exec_price >= price_min

            if not gate_open:
                badge_label = "監測未開始"
                badge_variant = "standby"
            elif in_band:
                badge_label = "已達觸發"
                badge_variant = "hit"
            elif exec_price >= (price_min - 0.05):
                badge_label = "接近"
                badge_variant = "near"
            else:
                badge_label = "未達標"
                badge_variant = "low"

            outcomes_out.append(
                {
                    "marketId": str(m.get("id") or ""),
                    "name": name,
                    "question": question,
                    "tokenId": str(tokens[0]) if tokens else "",
                    "bestBid": round(best_bid, 4),
                    "bestAsk": round(best_ask, 4),
                    "yesPrice": round(yes_price, 4),
                    "gammaYesPrice": round(gamma_yes, 4),
                    "execPrice": round(exec_price, 4),
                    "inBand": in_band,
                    "badgeLabel": badge_label,
                    "badgeVariant": badge_variant,
                    "quoteSource": "clob" if m.get("bestAsk") is not None else "gamma",
                }
            )

        is_open = bool(ev.get("active") and not ev.get("closed"))
        return {
            "title": str(ev.get("title") or "Highest temperature in Hong Kong"),
            "slug": str(ev.get("slug") or ""),
            "polymarketUrl": f"https://polymarket.com/event/{ev.get('slug', '')}",
            "endDate": str(ev.get("endDate") or ""),
            "status": "open" if is_open else "closed",
            "statusLabel": "開放" if is_open else "已關閉",
            "outcomes": outcomes_out,
        }

    def _update_trades_mark(self, market_date: str, outcomes: List[Dict[str, Any]]) -> None:
        outcome_map = {o["name"]: o for o in outcomes}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM hk_temp_trades WHERE market_date = ? AND status IN ('filled', 'submitted')",
                (market_date,),
            ).fetchall()
            for r in rows:
                o = outcome_map.get(r["outcome_name"])
                if not o:
                    continue
                curr_p = float(o["execPrice"])
                shares = float(r["size_shares"])
                stake = float(r["stake_usdc"])
                pnl = round(shares * curr_p - stake, 2)
                conn.execute(
                    "UPDATE hk_temp_trades SET current_price = ?, pnl_usdc = ? WHERE id = ?",
                    (curr_p, pnl, r["id"]),
                )

    async def execute_manual_hk_bet(
        self,
        outcome_name: str,
        stake_usdc: Optional[float] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.last_dashboard_data or not self.last_dashboard_data.get("market"):
            await self.scan_and_evaluate()
        market = (self.last_dashboard_data or {}).get("market") or {}
        outcomes = market.get("outcomes") or []
        target = next((o for o in outcomes if o["name"] == outcome_name), None)
        if not target:
            raise ValueError(f"找不到溫度選項: {outcome_name}")

        cfg = self.get_config()
        stake = float(stake_usdc or cfg.get("stake_usdc", 10.0))
        market_date = (self.last_dashboard_data or {}).get("marketDate") or now_hkt().strftime("%Y-%m-%d")
        mode_to_use = str(mode or cfg.get("mode", "PAPER")).upper()

        placed = await self._execute_hk_temp_bet(
            market_date=market_date,
            market_slug=market.get("slug", ""),
            outcome=target,
            stake_usdc=stake,
            mode=mode_to_use,
            trigger_type="MANUAL",
        )
        if not placed:
            raise RuntimeError("氣溫下單失敗，請檢查模擬餘額或 CLOB 憑證設定")
        return placed

    async def _execute_hk_temp_bet(
        self,
        market_date: str,
        market_slug: str,
        outcome: Dict[str, Any],
        stake_usdc: float,
        mode: str,
        trigger_type: str,
    ) -> Optional[Dict[str, Any]]:
        exec_price = max(0.005, min(0.999, float(outcome.get("execPrice") or outcome.get("yesPrice") or 0.75)))
        shares = round(stake_usdc / exec_price, 2)
        order_id = ""
        error_msg = ""
        status = "filled"

        if mode.upper() == "LIVE":
            creds = self.db.get_clob_credentials()
            try:
                res = await self.poly_client.place_live_clob_order(
                    creds=creds,
                    token_id=str(outcome.get("tokenId", "")),
                    price=exec_price,
                    amount_usdc=stake_usdc,
                )
                order_id = res["order_id"]
                exec_price = float(res["executed_price"])
                shares = float(res["shares"])
            except Exception as exc:
                error_msg = str(exc)
                status = "failed"
        else:
            bot_cfg = self.db.get_config()
            cash = float(bot_cfg.get("paper_cash_balance", 1000.0))
            if cash < stake_usdc:
                return None
            self.db.update_config({"paper_cash_balance": round(cash - stake_usdc, 2)})
            order_id = f"hktemp-paper-{int(time.time() * 1000)}"

        now_str = now_hkt().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO hk_temp_trades (
                    market_date, market_slug, outcome_name, token_id, side, mode,
                    trigger_type, price, current_price, stake_usdc, size_shares,
                    status, pnl_usdc, order_id, error, created_at
                ) VALUES (?, ?, ?, ?, 'YES', ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?)
                """,
                (
                    market_date,
                    market_slug,
                    outcome["name"],
                    outcome.get("tokenId", ""),
                    mode.lower(),
                    trigger_type,
                    exec_price,
                    exec_price,
                    stake_usdc,
                    shares,
                    status,
                    order_id,
                    error_msg,
                    now_str,
                ),
            )
            trade_id = cur.lastrowid
            row = conn.execute("SELECT * FROM hk_temp_trades WHERE id = ?", (trade_id,)).fetchone()

        # Also add an entry to the unified execution terminal log
        self.db.add_log(
            level="EXEC" if status == "filled" else "ERROR",
            trading_mode=mode.upper(),
            sport="HK_TEMP",
            market_id=market_slug,
            event_title=f"香港最高氣溫 ({market_date})",
            outcome_label=outcome["name"],
            prob=exec_price,
            amount_usdc=stake_usdc,
            message=(
                f"🌡️【香港氣溫交易】({mode.upper()}) 買入「{outcome['name']} 是」@ {exec_price * 100:.1f}% "
                f"| 金額 ${stake_usdc:.2f} USDC ({shares:.2f} 張)"
                if status == "filled"
                else f"❌【香港氣溫交易】下單失敗 ({outcome['name']}): {error_msg}"
            ),
        )
        return dict(row) if row else None
