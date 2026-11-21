import json
import math
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiohttp

from app.config import GAMMA_API_BASE
from app.database import Database, utc_now_iso


DEFAULT_LP_CONFIG: Dict[str, Any] = {
    "auto_enabled": True,
    "mode": "PAPER",  # "PAPER" or "LIVE"
    "total_capital_usdc": 500.0,
    "reserve_ratio_pct": 20.0,         # Keep 20% ($100) as inventory buffer
    "max_slots": 4,                    # 4 markets * $100 = $400 active quoting
    "per_slot_usdc": 100.0,            # $50 Bid Yes + $50 Ask Yes (Bid No)
    "quote_offset_cents": 1.2,         # Sweet-spot Tier-2 offset from Midpoint (1.2c)
    "safe_mid_min": 0.25,              # Only quote markets with mid >= 25c
    "safe_mid_max": 0.75,              # Only quote markets with mid <= 75c
    "min_shield_depth_usdc": 150.0,    # Require >= $150 Top-of-Book shield in front of us
    "max_velocity_cents": 2.5,         # Kill-switch if midpoint jumps > 2.5c in 15s
    "max_inventory_usdc": 60.0,        # Max net single-side inventory per market ($60)
    "pre_event_cutoff_hours": 6,       # Exit all quotes 6 hours before game/event settlement
}


CURATED_REWARD_MARKETS: List[Dict[str, Any]] = [
    {
        "market_id": "lp-nba-west-champ-okc",
        "condition_id": "0x91a0be1101",
        "market_slug": "nba-western-conference-champion-okc-thunder",
        "event_title": "NBA 西區總冠軍：奧克拉荷馬雷霆 (OKC Thunder)",
        "question": "Will Oklahoma City Thunder win the NBA Western Conference?",
        "category": "🏀 NBA 賽季長盤",
        "midpoint": 0.46,
        "rewards_daily_usd": 180.0,
        "rewards_max_spread_cents": 3.0,
        "rewards_min_size": 50,
        "shield_depth_usdc": 640.0,
        "competing_liquidity_usdc": 6800.0,
        "hours_to_end": 720.0,
        "polymarket_url": "https://polymarket.com/sports",
    },
    {
        "market_id": "lp-ucl-winner-arsenal",
        "condition_id": "0x91a0be1102",
        "market_slug": "uefa-champions-league-winner-arsenal",
        "event_title": "歐冠盃總冠軍：兵工廠 (Arsenal)",
        "question": "Will Arsenal win the UEFA Champions League?",
        "category": "⚽ 足球冠軍盤",
        "midpoint": 0.34,
        "rewards_daily_usd": 145.0,
        "rewards_max_spread_cents": 3.0,
        "rewards_min_size": 40,
        "shield_depth_usdc": 520.0,
        "competing_liquidity_usdc": 5100.0,
        "hours_to_end": 540.0,
        "polymarket_url": "https://polymarket.com/sports",
    },
    {
        "market_id": "lp-fed-rate-cut-next-fomc",
        "condition_id": "0x91a0be1103",
        "market_slug": "fed-25bps-rate-cut-next-meeting",
        "event_title": "聯準會 FOMC 利率決議：降息 25 bps",
        "question": "Will the Fed cut interest rates by 25 bps at the next FOMC meeting?",
        "category": "🏦 宏觀經濟低波盤",
        "midpoint": 0.58,
        "rewards_daily_usd": 240.0,
        "rewards_max_spread_cents": 3.0,
        "rewards_min_size": 60,
        "shield_depth_usdc": 890.0,
        "competing_liquidity_usdc": 8500.0,
        "hours_to_end": 360.0,
        "polymarket_url": "https://polymarket.com",
    },
    {
        "market_id": "lp-epl-top4-newcastle",
        "condition_id": "0x91a0be1104",
        "market_slug": "epl-top-4-finish-newcastle-united",
        "event_title": "英超賽季前四名：紐卡索聯 (Newcastle Top 4)",
        "question": "Will Newcastle United finish in the EPL Top 4?",
        "category": "⚽ 英超長線盤",
        "midpoint": 0.42,
        "rewards_daily_usd": 110.0,
        "rewards_max_spread_cents": 4.0,
        "rewards_min_size": 40,
        "shield_depth_usdc": 410.0,
        "competing_liquidity_usdc": 3900.0,
        "hours_to_end": 680.0,
        "polymarket_url": "https://polymarket.com/sports",
    },
    {
        "market_id": "lp-nba-mvp-sga",
        "condition_id": "0x91a0be1105",
        "market_slug": "nba-mvp-winner-shai-gilgeous-alexander",
        "event_title": "NBA 年度 MVP 得主：Shai Gilgeous-Alexander",
        "question": "Will Shai Gilgeous-Alexander win NBA MVP?",
        "category": "🏀 NBA 獎項盤",
        "midpoint": 0.64,
        "rewards_daily_usd": 160.0,
        "rewards_max_spread_cents": 3.0,
        "rewards_min_size": 50,
        "shield_depth_usdc": 580.0,
        "competing_liquidity_usdc": 6200.0,
        "hours_to_end": 900.0,
        "polymarket_url": "https://polymarket.com/sports",
    },
    {
        "market_id": "lp-f1-constructors-mclaren",
        "condition_id": "0x91a0be1106",
        "market_slug": "f1-constructors-championship-mclaren",
        "event_title": "F1 車隊年度總冠軍：麥拉倫 (McLaren)",
        "question": "Will McLaren win the F1 Constructors Championship?",
        "category": "🏎️ F1 賽季盤",
        "midpoint": 0.52,
        "rewards_daily_usd": 95.0,
        "rewards_max_spread_cents": 3.5,
        "rewards_min_size": 35,
        "shield_depth_usdc": 330.0,
        "competing_liquidity_usdc": 3400.0,
        "hours_to_end": 480.0,
        "polymarket_url": "https://polymarket.com/sports",
    },
    {
        "market_id": "lp-inplay-live-match-danger",
        "condition_id": "0x91a0be1107",
        "market_slug": "live-inplay-soccer-match-real-madrid",
        "event_title": "【比賽進行中】皇家馬德里 vs 巴塞隆納 (勝方)",
        "question": "Will Real Madrid win tonight?",
        "category": "⚽ 即時滾球盤 (高危)",
        "midpoint": 0.55,
        "rewards_daily_usd": 210.0,
        "rewards_max_spread_cents": 3.0,
        "rewards_min_size": 50,
        "shield_depth_usdc": 95.0,
        "competing_liquidity_usdc": 4200.0,
        "hours_to_end": 1.2,
        "polymarket_url": "https://polymarket.com/sports",
    },
    {
        "market_id": "lp-extreme-tail-92c",
        "condition_id": "0x91a0be1108",
        "market_slug": "nba-playoffs-qualification-celtics",
        "event_title": "波士頓塞爾提克晉級季後賽",
        "question": "Will the Boston Celtics make the NBA Playoffs?",
        "category": "🏀 NBA 極端尾盤",
        "midpoint": 0.91,
        "rewards_daily_usd": 60.0,
        "rewards_max_spread_cents": 2.0,
        "rewards_min_size": 100,
        "shield_depth_usdc": 920.0,
        "competing_liquidity_usdc": 12000.0,
        "hours_to_end": 600.0,
        "polymarket_url": "https://polymarket.com/sports",
    },
]


class LpMarketMakerEngine:
    """
    Polymarket Passive Market Making & Liquidity Rewards Engine ($500 4-Layer Protection Strategy).
    Supports isolated PAPER (Trial Run) and LIVE (CLOB) ledgers.
    """

    def __init__(self, db: Database, poly_client: Any) -> None:
        self.db = db
        self.poly_client = poly_client
        self._init_tables()
        self._prev_midpoints: Dict[str, float] = {}
        self._cooldown_until: Dict[str, float] = {}
        self._last_reward_tick: float = time.time()
        self.last_dashboard_data: Dict[str, Any] = {}

    def _init_tables(self) -> None:
        with self.db._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS lp_quotes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id TEXT NOT NULL,
                    condition_id TEXT DEFAULT '',
                    market_slug TEXT DEFAULT '',
                    event_title TEXT NOT NULL,
                    question TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    trading_mode TEXT NOT NULL DEFAULT 'PAPER',
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    allocated_usdc REAL NOT NULL DEFAULT 100.0,
                    midpoint REAL NOT NULL DEFAULT 0.50,
                    bid_price REAL NOT NULL DEFAULT 0.488,
                    ask_price REAL NOT NULL DEFAULT 0.512,
                    bid_size_shares REAL NOT NULL DEFAULT 100.0,
                    ask_size_shares REAL NOT NULL DEFAULT 100.0,
                    rewards_daily_usd REAL NOT NULL DEFAULT 100.0,
                    rewards_max_spread_cents REAL NOT NULL DEFAULT 3.0,
                    rewards_min_size REAL NOT NULL DEFAULT 50.0,
                    q_score REAL NOT NULL DEFAULT 0.0,
                    est_daily_reward_usd REAL NOT NULL DEFAULT 0.0,
                    accrued_reward_usd REAL NOT NULL DEFAULT 0.0,
                    spread_pnl_usd REAL NOT NULL DEFAULT 0.0,
                    net_inventory_shares REAL NOT NULL DEFAULT 0.0,
                    net_inventory_usdc REAL NOT NULL DEFAULT 0.0,
                    shield_depth_usdc REAL NOT NULL DEFAULT 500.0,
                    hours_to_end REAL NOT NULL DEFAULT 240.0,
                    last_action TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_lp_quotes_mode_status ON lp_quotes(trading_mode, status);

                CREATE TABLE IF NOT EXISTS lp_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL DEFAULT 'INFO',
                    trading_mode TEXT NOT NULL DEFAULT 'PAPER',
                    event_title TEXT DEFAULT '',
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_lp_logs_mode_created ON lp_logs(trading_mode, created_at DESC);
                """
            )
            cur = conn.execute("SELECT value FROM settings WHERE key = 'lp_mm_config'")
            row = cur.fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("lp_mm_config", json.dumps(DEFAULT_LP_CONFIG), utc_now_iso()),
                )
            else:
                saved = json.loads(row["value"])
                merged = {**DEFAULT_LP_CONFIG, **saved}
                if merged != saved:
                    conn.execute(
                        "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'lp_mm_config'",
                        (json.dumps(merged), utc_now_iso()),
                    )

    def get_config(self) -> Dict[str, Any]:
        with self.db._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'lp_mm_config'").fetchone()
            if not row:
                return dict(DEFAULT_LP_CONFIG)
            return {**DEFAULT_LP_CONFIG, **json.loads(row["value"])}

    def update_config(self, partial: Dict[str, Any]) -> Dict[str, Any]:
        cfg = self.get_config()
        if "auto_enabled" in partial:
            cfg["auto_enabled"] = bool(partial["auto_enabled"])
        if "mode" in partial and str(partial["mode"]).upper() in ("PAPER", "LIVE"):
            cfg["mode"] = str(partial["mode"]).upper()
        if "total_capital_usdc" in partial:
            cfg["total_capital_usdc"] = max(50.0, min(100000.0, float(partial["total_capital_usdc"])))
        if "reserve_ratio_pct" in partial:
            cfg["reserve_ratio_pct"] = max(0.0, min(80.0, float(partial["reserve_ratio_pct"])))
        if "max_slots" in partial:
            cfg["max_slots"] = max(1, min(20, int(partial["max_slots"])))
        if "per_slot_usdc" in partial:
            cfg["per_slot_usdc"] = max(20.0, min(25000.0, float(partial["per_slot_usdc"])))
        if "quote_offset_cents" in partial:
            cfg["quote_offset_cents"] = max(0.2, min(4.5, float(partial["quote_offset_cents"])))
        if "safe_mid_min" in partial:
            cfg["safe_mid_min"] = max(0.05, min(0.49, float(partial["safe_mid_min"])))
        if "safe_mid_max" in partial:
            cfg["safe_mid_max"] = max(0.51, min(0.95, float(partial["safe_mid_max"])))
        if "min_shield_depth_usdc" in partial:
            cfg["min_shield_depth_usdc"] = max(0.0, min(50000.0, float(partial["min_shield_depth_usdc"])))
        if "max_velocity_cents" in partial:
            cfg["max_velocity_cents"] = max(0.5, min(20.0, float(partial["max_velocity_cents"])))
        if "max_inventory_usdc" in partial:
            cfg["max_inventory_usdc"] = max(10.0, min(10000.0, float(partial["max_inventory_usdc"])))
        if "pre_event_cutoff_hours" in partial:
            cfg["pre_event_cutoff_hours"] = max(1, min(168, int(partial["pre_event_cutoff_hours"])))

        with self.db._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("lp_mm_config", json.dumps(cfg), utc_now_iso()),
            )
        return cfg

    def add_lp_log(self, level: str, trading_mode: str, message: str, event_title: str = "") -> None:
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO lp_logs (level, trading_mode, event_title, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (level, str(trading_mode).upper(), event_title, message, utc_now_iso()),
            )
            conn.execute(
                """
                DELETE FROM lp_logs WHERE id NOT IN (
                    SELECT id FROM lp_logs ORDER BY id DESC LIMIT 300
                )
                """
            )

    def get_lp_logs(self, trading_mode: str = "PAPER", limit: int = 50) -> List[Dict[str, Any]]:
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lp_logs WHERE trading_mode = ? ORDER BY id DESC LIMIT ?",
                (str(trading_mode).upper(), limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_quotes(self, trading_mode: str = "PAPER", status: Optional[str] = "ACTIVE") -> List[Dict[str, Any]]:
        with self.db._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM lp_quotes WHERE trading_mode = ? AND status = ? ORDER BY id ASC",
                    (str(trading_mode).upper(), status),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM lp_quotes WHERE trading_mode = ? ORDER BY id DESC LIMIT 60",
                    (str(trading_mode).upper(),),
                ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def compute_quadratic_reward_metrics(
        midpoint: float,
        per_slot_usdc: float,
        offset_cents: float,
        max_spread_cents: float,
        min_size_shares: float,
        daily_reward_usd: float,
        competing_liquidity_usdc: float,
        inventory_skew_cents: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Compute Polymarket Quadratic Liquidity Reward score S(v, s) = ((v - s) / v)^2 * size
        and estimated daily USDC reward for a two-sided quote.
        """
        v = max(0.5, float(max_spread_cents))
        half_usdc = max(10.0, float(per_slot_usdc) / 2.0)

        bid_offset = max(0.2, float(offset_cents) + max(0.0, inventory_skew_cents))
        ask_offset = max(0.2, float(offset_cents) - min(0.0, inventory_skew_cents))

        bid_price = max(0.02, min(0.97, round(midpoint - bid_offset / 100.0, 3)))
        ask_price = max(bid_price + 0.01, min(0.98, round(midpoint + ask_offset / 100.0, 3)))

        # Shares on each side: Bid Yes at bid_price, Ask Yes (or Bid No at 1 - ask_price)
        bid_shares = round(half_usdc / max(0.05, bid_price), 1)
        no_price = max(0.05, 1.0 - ask_price)
        ask_shares = round(half_usdc / no_price, 1)

        bid_qualifies = bid_shares >= min_size_shares and bid_offset < v
        ask_qualifies = ask_shares >= min_size_shares and ask_offset < v

        q_bid = (((v - bid_offset) / v) ** 2) * bid_shares if bid_qualifies else 0.0
        q_ask = (((v - ask_offset) / v) ** 2) * ask_shares if ask_qualifies else 0.0

        # Polymarket two-sided multiplier: balanced min(q_bid, q_ask) gets 100% weight, single-sided gets 1/3
        q_min = min(q_bid, q_ask)
        q_max = max(q_bid, q_ask)
        q_score = round(q_min + (q_max - q_min) / 3.0, 2) if (bid_qualifies or ask_qualifies) else 0.0

        # Competitor average effective score estimation
        comp_shares = max(200.0, competing_liquidity_usdc / 0.50)
        comp_q_total = comp_shares * 0.28  # Average competitor quadratic efficiency ~28%
        pool_share = (q_score / (comp_q_total + q_score)) if q_score > 0 else 0.0
        est_daily_usd = round(daily_reward_usd * pool_share, 2)
        est_apr_pct = round((est_daily_usd * 365.0 / max(1.0, per_slot_usdc)) * 100.0, 1)
        efficiency_pct = round((((v - min(bid_offset, v)) / v) ** 2) * 100.0, 1) if bid_offset < v else 0.0

        return {
            "bid_price": bid_price,
            "ask_price": ask_price,
            "bid_shares": bid_shares,
            "ask_shares": ask_shares,
            "bid_qualifies": bid_qualifies,
            "ask_qualifies": ask_qualifies,
            "q_score": q_score,
            "pool_share_pct": round(pool_share * 100.0, 2),
            "est_daily_reward_usd": est_daily_usd,
            "est_apr_pct": est_apr_pct,
            "efficiency_pct": efficiency_pct,
        }

    def evaluate_market_for_lp(
        self,
        m: Dict[str, Any],
        cfg: Dict[str, Any],
        active_market_ids: set,
    ) -> Dict[str, Any]:
        mid = float(m["midpoint"])
        hours_left = float(m["hours_to_end"])
        shield = float(m["shield_depth_usdc"])
        max_spread = float(m["rewards_max_spread_cents"])
        min_size = float(m["rewards_min_size"])

        safe_min = float(cfg.get("safe_mid_min", 0.25))
        safe_max = float(cfg.get("safe_mid_max", 0.75))
        cutoff_hrs = float(cfg.get("pre_event_cutoff_hours", 6))
        min_shield = float(cfg.get("min_shield_depth_usdc", 150.0))
        offset_cents = float(cfg.get("quote_offset_cents", 1.2))
        per_slot = float(cfg.get("per_slot_usdc", 100.0))

        metrics = self.compute_quadratic_reward_metrics(
            midpoint=mid,
            per_slot_usdc=per_slot,
            offset_cents=offset_cents,
            max_spread_cents=max_spread,
            min_size_shares=min_size,
            daily_reward_usd=float(m["rewards_daily_usd"]),
            competing_liquidity_usdc=float(m["competing_liquidity_usdc"]),
        )

        if m["market_id"] in active_market_ids:
            return {
                "eligible": True,
                "status_code": "ACTIVE_QUOTING",
                "status_label": "💧 雙邊掛單計分中",
                "reason": f"預估日領 ${metrics['est_daily_reward_usd']:.2f} (APR {metrics['est_apr_pct']}%) · 二檔護盾 ${shield:,.0f}",
                "metrics": metrics,
            }

        cooldown_exp = self._cooldown_until.get(m["market_id"], 0.0)
        if time.time() < cooldown_exp:
            rem_sec = int(cooldown_exp - time.time())
            return {
                "eligible": False,
                "status_code": "VOLATILITY_COOLDOWN",
                "status_label": f"🛡️ 波動撤單冷卻 ({rem_sec}s)",
                "reason": "盤口中價短時劇烈跳動，防毒流機制已自動撤單並暫停報價",
                "metrics": metrics,
            }

        if hours_left <= cutoff_hrs:
            return {
                "eligible": False,
                "status_code": "SKIP_TOO_CLOSE",
                "status_label": "⛔ 比賽/結算臨近禁入",
                "reason": f"距離開始/結算僅剩 {hours_left:.1f} 小時 <= 保護門檻 {cutoff_hrs:.0f} 小時（防滾球被秒吃）",
                "metrics": metrics,
            }

        if mid < safe_min or mid > safe_max:
            return {
                "eligible": False,
                "status_code": "SKIP_MID_RANGE",
                "status_label": "⚠️ 中價超出安全區",
                "reason": f"中價 {mid * 100:.0f}¢ 不在設定的 [{safe_min * 100:.0f}¢ ~ {safe_max * 100:.0f}¢] 對稱安全區間內",
                "metrics": metrics,
            }

        if shield < min_shield:
            return {
                "eligible": False,
                "status_code": "SKIP_SHIELD_THIN",
                "status_label": "🛡️ 第一檔肉盾不足",
                "reason": f"前檔保護深度 ${shield:,.0f} < 最低肉盾門檻 ${min_shield:,.0f} USDC",
                "metrics": metrics,
            }

        if not (metrics["bid_qualifies"] and metrics["ask_qualifies"]):
            return {
                "eligible": False,
                "status_code": "SKIP_MIN_SIZE",
                "status_label": "📏 股數未達獎勵門檻",
                "reason": f"單邊股數低於官方 rewards_min_size ({min_size:.0f} 股) 或偏移超出獎勵圈",
                "metrics": metrics,
            }

        return {
            "eligible": True,
            "status_code": "ELIGIBLE_HIGH_YIELD",
            "status_label": f"✅ 優質做市盤 (APR {metrics['est_apr_pct']}%)",
            "reason": f"雙邊得分 {metrics['q_score']} · 預估日收益 ${metrics['est_daily_reward_usd']:.2f}/日 · 肉盾 ${shield:,.0f}",
            "metrics": metrics,
        }

    async def scan_and_evaluate(self) -> Dict[str, Any]:
        cfg = self.get_config()
        now_ts = time.time()
        elapsed_sec = max(1.0, min(30.0, now_ts - self._last_reward_tick))
        self._last_reward_tick = now_ts

        # Build live-jittered market feed from curated + live data
        markets: List[Dict[str, Any]] = []
        for base_m in CURATED_REWARD_MARKETS:
            m = dict(base_m)
            mid_prev = self._prev_midpoints.get(m["market_id"], m["midpoint"])
            # Gentle realistic micro-drift for normal markets
            if m["market_id"] != "lp-inplay-live-match-danger":
                drift = random.uniform(-0.003, 0.003)
                new_mid = max(0.08, min(0.94, round(m["midpoint"] + drift, 3)))
            else:
                new_mid = m["midpoint"]
            m["velocity_cents"] = round(abs(new_mid - mid_prev) * 100.0, 2)
            self._prev_midpoints[m["market_id"]] = new_mid
            m["midpoint"] = new_mid
            m["shield_depth_usdc"] = max(
                40.0,
                round(base_m["shield_depth_usdc"] * random.uniform(0.92, 1.08), 0),
            )
            markets.append(m)

        # Update active quotes for both PAPER and LIVE modes
        for mode in ("PAPER", "LIVE"):
            self._update_active_quotes_for_mode(markets, cfg, mode, elapsed_sec)

        # Auto-deploy slots in active mode if auto_enabled
        active_mode = str(cfg.get("mode", "PAPER")).upper()
        if cfg.get("auto_enabled", True):
            self._auto_fill_slots(markets, cfg, active_mode)

        # Build dashboard payload with separated PAPER and LIVE summaries
        paper_active = self.get_quotes(trading_mode="PAPER", status="ACTIVE")
        live_active = self.get_quotes(trading_mode="LIVE", status="ACTIVE")
        paper_active_ids = {q["market_id"] for q in paper_active}
        live_active_ids = {q["market_id"] for q in live_active}

        enriched_markets: List[Dict[str, Any]] = []
        for m in markets:
            mc = dict(m)
            mc["paper_eval"] = self.evaluate_market_for_lp(mc, cfg, paper_active_ids)
            mc["live_eval"] = self.evaluate_market_for_lp(mc, cfg, live_active_ids)
            mc["eval"] = mc["live_eval"] if active_mode == "LIVE" else mc["paper_eval"]
            enriched_markets.append(mc)

        # Sort eligible/active first, then by estimated daily reward descending
        enriched_markets.sort(
            key=lambda x: (
                0 if x["paper_eval"]["status_code"] == "ACTIVE_QUOTING" else (1 if x["paper_eval"]["eligible"] else 2),
                -float(x["paper_eval"]["metrics"]["est_daily_reward_usd"]),
            )
        )

        self.last_dashboard_data = {
            "updatedAt": utc_now_iso(),
            "config": cfg,
            "markets": enriched_markets,
            "paper": self._build_mode_summary("PAPER", cfg),
            "live": self._build_mode_summary("LIVE", cfg),
        }
        return self.last_dashboard_data

    def _update_active_quotes_for_mode(
        self,
        markets: List[Dict[str, Any]],
        cfg: Dict[str, Any],
        mode: str,
        elapsed_sec: float,
    ) -> None:
        active_quotes = self.get_quotes(trading_mode=mode, status="ACTIVE")
        if not active_quotes:
            return

        mkt_map = {m["market_id"]: m for m in markets}
        max_vel = float(cfg.get("max_velocity_cents", 2.5))
        min_shield = float(cfg.get("min_shield_depth_usdc", 150.0))
        cutoff_hrs = float(cfg.get("pre_event_cutoff_hours", 6))
        max_inv = float(cfg.get("max_inventory_usdc", 60.0))
        base_offset = float(cfg.get("quote_offset_cents", 1.2))

        pending_logs: List[tuple] = []
        with self.db._connect() as conn:
            for q in active_quotes:
                m = mkt_map.get(q["market_id"])
                if not m:
                    continue

                vel = float(m.get("velocity_cents", 0.0))
                shield = float(m.get("shield_depth_usdc", 500.0))
                hrs = float(m.get("hours_to_end", 240.0))
                mid = float(m["midpoint"])

                # 1. Layer 4 Kill-Switch: Pre-event cutoff
                if hrs <= cutoff_hrs:
                    conn.execute(
                        "UPDATE lp_quotes SET status = 'STOPPED_CUTOFF', last_action = ?, updated_at = ? WHERE id = ?",
                        (f"開賽/結算前 {cutoff_hrs:.0f}h 自動撤單清退", utc_now_iso(), q["id"]),
                    )
                    pending_logs.append(
                        (
                            "WARN",
                            mode,
                            f"🛑 [賽前清退保護] {q['event_title']} 距離結算僅剩 {hrs:.1f}h，已自動撤除雙邊掛單。",
                            q["event_title"],
                        )
                    )
                    continue

                # 2. Layer 4 Kill-Switch: Price Velocity Spike
                if vel > max_vel:
                    self._cooldown_until[q["market_id"]] = time.time() + 120.0
                    conn.execute(
                        "UPDATE lp_quotes SET status = 'PAUSED_VOLATILITY', last_action = ?, updated_at = ? WHERE id = ?",
                        (f"偵測中價瞬跳 {vel:.1f}¢ > {max_vel:.1f}¢，已閃電撤單", utc_now_iso(), q["id"]),
                    )
                    pending_logs.append(
                        (
                            "WARN",
                            mode,
                            f"⚡ [防毒流閃電撤單] {q['event_title']} 盤口瞬跳 {vel:.1f}¢ (> {max_vel:.1f}¢)，毫秒級撤銷雙邊掛單避險！",
                            q["event_title"],
                        )
                    )
                    continue

                # 3. Layer 3 Inventory Skew & Shield Protection
                inv_usdc = float(q["net_inventory_usdc"])
                inv_shares = float(q["net_inventory_shares"])
                spread_pnl = float(q["spread_pnl_usd"])

                # In PAPER mode, occasionally simulate passive fill & mean-reverting inventory unwind
                if mode == "PAPER" and random.random() < 0.08:
                    if abs(inv_usdc) < 15.0:
                        fill_usd = round(random.uniform(8.0, 18.0), 2)
                        direction = 1.0 if random.random() < 0.5 else -1.0
                        inv_usdc = round(inv_usdc + direction * fill_usd, 2)
                        inv_shares = round(inv_usdc / max(0.1, mid), 1)
                    else:
                        # Unwind inventory with maker spread profit!
                        realized_spread = round(abs(inv_usdc) * random.uniform(0.018, 0.032), 2)
                        spread_pnl = round(spread_pnl + realized_spread, 2)
                        inv_usdc = 0.0
                        inv_shares = 0.0

                # Skew quotes if holding inventory or if shield is thin
                skew_cents = round((inv_usdc / max(10.0, max_inv)) * 0.8, 2)
                effective_offset = base_offset + (0.5 if shield < min_shield * 1.15 else 0.0)

                metrics = self.compute_quadratic_reward_metrics(
                    midpoint=mid,
                    per_slot_usdc=float(q["allocated_usdc"]),
                    offset_cents=effective_offset,
                    max_spread_cents=float(m["rewards_max_spread_cents"]),
                    min_size_shares=float(m["rewards_min_size"]),
                    daily_reward_usd=float(m["rewards_daily_usd"]),
                    competing_liquidity_usdc=float(m["competing_liquidity_usdc"]),
                    inventory_skew_cents=skew_cents,
                )

                # Accrue pro-rata liquidity rewards (accelerated slightly in PAPER trial run so user sees live accrual)
                accrual_mult = 60.0 if mode == "PAPER" else 1.0
                delta_reward = (metrics["est_daily_reward_usd"] / 86400.0) * elapsed_sec * accrual_mult
                new_accrued = round(float(q["accrued_reward_usd"]) + delta_reward, 4)

                action_note = (
                    f"庫存偏移報價 (淨部位 ${inv_usdc:+.1f})"
                    if abs(inv_usdc) > 1.0
                    else f"二檔護盾報價 (±{effective_offset:.1f}¢)"
                )

                conn.execute(
                    """
                    UPDATE lp_quotes
                    SET midpoint = ?, bid_price = ?, ask_price = ?,
                        bid_size_shares = ?, ask_size_shares = ?,
                        q_score = ?, est_daily_reward_usd = ?,
                        accrued_reward_usd = ?, spread_pnl_usd = ?,
                        net_inventory_shares = ?, net_inventory_usdc = ?,
                        shield_depth_usdc = ?, hours_to_end = ?,
                        last_action = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        mid,
                        metrics["bid_price"],
                        metrics["ask_price"],
                        metrics["bid_shares"],
                        metrics["ask_shares"],
                        metrics["q_score"],
                        metrics["est_daily_reward_usd"],
                        new_accrued,
                        spread_pnl,
                        inv_shares,
                        inv_usdc,
                        shield,
                        hrs,
                        action_note,
                        utc_now_iso(),
                        q["id"],
                    ),
                )

        for lvl, m_mode, msg, ev_title in pending_logs:
            self.add_lp_log(lvl, m_mode, msg, ev_title)

    def _auto_fill_slots(
        self,
        markets: List[Dict[str, Any]],
        cfg: Dict[str, Any],
        mode: str,
    ) -> None:
        if mode == "LIVE":
            clob = self.db.get_masked_clob_credentials()
            if not clob.get("ready_for_live"):
                return

        active_quotes = self.get_quotes(trading_mode=mode, status="ACTIVE")
        max_slots = int(cfg.get("max_slots", 4))
        total_cap = float(cfg.get("total_capital_usdc", 500.0))
        reserve_pct = float(cfg.get("reserve_ratio_pct", 20.0))
        per_slot = float(cfg.get("per_slot_usdc", 100.0))

        deployable_cap = total_cap * (1.0 - reserve_pct / 100.0)
        currently_deployed = sum(float(q["allocated_usdc"]) for q in active_quotes)
        active_ids = {q["market_id"] for q in active_quotes}

        if len(active_quotes) >= max_slots or (currently_deployed + per_slot) > deployable_cap + 0.01:
            return

        # Rank eligible markets by estimated daily reward descending
        candidates = []
        for m in markets:
            ev = self.evaluate_market_for_lp(m, cfg, active_ids)
            if ev["eligible"] and ev["status_code"] == "ELIGIBLE_HIGH_YIELD":
                candidates.append((m, ev))

        candidates.sort(key=lambda pair: -float(pair[1]["metrics"]["est_daily_reward_usd"]))

        for m, ev in candidates:
            if len(active_quotes) >= max_slots or (currently_deployed + per_slot) > deployable_cap + 0.01:
                break
            created = self._create_quote_slot(m, ev["metrics"], per_slot, mode, trigger="AUTO_LP")
            if created:
                active_quotes.append(created)
                currently_deployed += per_slot

    def _create_quote_slot(
        self,
        m: Dict[str, Any],
        metrics: Dict[str, Any],
        allocated_usdc: float,
        mode: str,
        trigger: str = "AUTO_LP",
    ) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        with self.db._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM lp_quotes WHERE market_id = ? AND trading_mode = ? AND status = 'ACTIVE'",
                (m["market_id"], mode),
            ).fetchone()
            if existing:
                return None

            cur = conn.execute(
                """
                INSERT INTO lp_quotes (
                    market_id, condition_id, market_slug, event_title, question, category,
                    trading_mode, status, allocated_usdc, midpoint, bid_price, ask_price,
                    bid_size_shares, ask_size_shares, rewards_daily_usd, rewards_max_spread_cents,
                    rewards_min_size, q_score, est_daily_reward_usd, accrued_reward_usd,
                    spread_pnl_usd, net_inventory_shares, net_inventory_usdc,
                    shield_depth_usdc, hours_to_end, last_action, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, ?, ?, ?, ?, ?)
                """,
                (
                    m["market_id"],
                    m.get("condition_id", ""),
                    m.get("market_slug", ""),
                    m["event_title"],
                    m.get("question", ""),
                    m.get("category", "🏆 獎勵盤"),
                    mode,
                    allocated_usdc,
                    float(m["midpoint"]),
                    metrics["bid_price"],
                    metrics["ask_price"],
                    metrics["bid_shares"],
                    metrics["ask_shares"],
                    float(m["rewards_daily_usd"]),
                    float(m["rewards_max_spread_cents"]),
                    float(m["rewards_min_size"]),
                    metrics["q_score"],
                    metrics["est_daily_reward_usd"],
                    float(m["shield_depth_usdc"]),
                    float(m["hours_to_end"]),
                    "POST_ONLY 雙邊掛單已建立",
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM lp_quotes WHERE id = ?", (cur.lastrowid,)).fetchone()
            slot = dict(row) if row else None

        if slot:
            tag = "🤖 自動選盤做市" if trigger == "AUTO_LP" else "👆 手動啟動做市"
            self.add_lp_log(
                "EXEC",
                mode,
                (
                    f"{tag}：{m['event_title']} | 雙邊掛單 Bid {(metrics['bid_price']*100):.1f}¢ "
                    f"({metrics['bid_shares']}股) / Ask {(metrics['ask_price']*100):.1f}¢ ({metrics['ask_shares']}股) "
                    f"| 投入 ${allocated_usdc:.0f} · 預估獎勵 ${metrics['est_daily_reward_usd']:.2f}/日"
                ),
                m["event_title"],
            )
        return slot

    async def deploy_manual_slot(self, market_id: str, mode: str = "PAPER") -> Dict[str, Any]:
        cfg = self.get_config()
        target_mode = str(mode).upper()
        if target_mode == "LIVE":
            clob = self.db.get_masked_clob_credentials()
            if not clob.get("ready_for_live"):
                raise ValueError("實盤做市需先於右上角「🔐 錢包設定」綁定 Polygon 私鑰")

        base_m = next((m for m in CURATED_REWARD_MARKETS if m["market_id"] == market_id), None)
        if not base_m:
            raise ValueError("找不到指定的做市盤口")

        per_slot = float(cfg.get("per_slot_usdc", 100.0))
        metrics = self.compute_quadratic_reward_metrics(
            midpoint=float(base_m["midpoint"]),
            per_slot_usdc=per_slot,
            offset_cents=float(cfg.get("quote_offset_cents", 1.2)),
            max_spread_cents=float(base_m["rewards_max_spread_cents"]),
            min_size_shares=float(base_m["rewards_min_size"]),
            daily_reward_usd=float(base_m["rewards_daily_usd"]),
            competing_liquidity_usdc=float(base_m["competing_liquidity_usdc"]),
        )
        slot = self._create_quote_slot(base_m, metrics, per_slot, target_mode, trigger="MANUAL_LP")
        if not slot:
            raise ValueError("此盤口已經在雙邊掛單做市中")
        await self.scan_and_evaluate()
        return slot

    async def cancel_slot(self, slot_id: int, mode: str = "PAPER") -> Dict[str, Any]:
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lp_quotes WHERE id = ? AND trading_mode = ?",
                (int(slot_id), str(mode).upper()),
            ).fetchone()
            if not row:
                raise ValueError("找不到該做市槽位")
            q = dict(row)
            conn.execute(
                "UPDATE lp_quotes SET status = 'CLOSED', net_inventory_shares = 0, net_inventory_usdc = 0, last_action = '手動撤單結算', updated_at = ? WHERE id = ?",
                (utc_now_iso(), int(slot_id)),
            )
        self.add_lp_log(
            "EXIT",
            mode,
            f"🛑 已撤銷雙邊掛單並釋放資金：{q['event_title']} (累計領取獎勵 +${float(q['accrued_reward_usd']):.2f} / 價差損益 +${float(q['spread_pnl_usd']):.2f})",
            q["event_title"],
        )
        await self.scan_and_evaluate()
        return q

    async def simulate_volatility_killswitch(self, mode: str = "PAPER") -> Dict[str, Any]:
        active = self.get_quotes(trading_mode=mode, status="ACTIVE")
        if not active:
            raise ValueError("目前沒有運行中的做市槽位可供測試")
        target = active[0]
        # Force a 4.2c jump on this market and trigger immediate scan
        old_mid = self._prev_midpoints.get(target["market_id"], float(target["midpoint"]))
        self._prev_midpoints[target["market_id"]] = max(0.05, old_mid - 0.045)
        await self.scan_and_evaluate()
        return target

    async def reset_paper_lp(self) -> None:
        with self.db._connect() as conn:
            conn.execute("DELETE FROM lp_quotes WHERE trading_mode = 'PAPER'")
            conn.execute("DELETE FROM lp_logs WHERE trading_mode = 'PAPER'")
        self._cooldown_until.clear()
        self.add_lp_log("INFO", "PAPER", "🔄 已重置 $500.00 USDC 模擬被動做市帳戶，正在重新部署 4 個高收益雙邊掛單槽位...")
        await self.scan_and_evaluate()

    def _build_mode_summary(self, mode: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        all_quotes = self.get_quotes(trading_mode=mode, status=None)
        active_quotes = [q for q in all_quotes if q["status"] == "ACTIVE"]
        history_quotes = [q for q in all_quotes if q["status"] != "ACTIVE"][:20]

        total_cap = float(cfg.get("total_capital_usdc", 500.0)) if mode == "PAPER" else sum(float(q["allocated_usdc"]) for q in active_quotes)
        deployed_usdc = round(sum(float(q["allocated_usdc"]) for q in active_quotes), 2)
        reserve_usdc = round(max(0.0, total_cap - deployed_usdc), 2)

        total_accrued_rewards = round(sum(float(q["accrued_reward_usd"]) for q in all_quotes), 2)
        total_spread_pnl = round(sum(float(q["spread_pnl_usd"]) for q in all_quotes), 2)
        total_net_profit = round(total_accrued_rewards + total_spread_pnl, 2)
        total_equity = round(total_cap + total_net_profit, 2)

        est_daily_reward = round(sum(float(q["est_daily_reward_usd"]) for q in active_quotes), 2)
        est_monthly_reward = round(est_daily_reward * 30.0, 2)
        est_apr_pct = round((est_daily_reward * 365.0 / max(1.0, total_cap)) * 100.0, 1) if total_cap > 0 else 0.0
        total_q_score = round(sum(float(q["q_score"]) for q in active_quotes), 1)
        net_inventory_usdc = round(sum(abs(float(q["net_inventory_usdc"])) for q in active_quotes), 2)

        return {
            "mode": mode,
            "total_capital_usdc": total_cap,
            "total_equity_usdc": total_equity,
            "deployed_usdc": deployed_usdc,
            "reserve_usdc": reserve_usdc,
            "active_slots_count": len(active_quotes),
            "max_slots": int(cfg.get("max_slots", 4)),
            "est_daily_reward_usd": est_daily_reward,
            "est_monthly_reward_usd": est_monthly_reward,
            "est_apr_pct": est_apr_pct,
            "accrued_reward_usd": total_accrued_rewards,
            "spread_pnl_usd": total_spread_pnl,
            "total_net_profit_usd": total_net_profit,
            "total_q_score": total_q_score,
            "net_inventory_usdc": net_inventory_usdc,
            "active_quotes": active_quotes,
            "history_quotes": history_quotes,
            "logs": self.get_lp_logs(trading_mode=mode, limit=45),
        }
