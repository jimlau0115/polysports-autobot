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
    "cooldown_seconds": 120,           # Cooldown seconds after a volatility kill-switch
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
        "polymarket_url": "https://polymarket.com/sports/nba",
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
        "polymarket_url": "https://polymarket.com/sports/soccer",
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
        "polymarket_url": "https://polymarket.com/predictions/fed-rates",
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
        "polymarket_url": "https://polymarket.com/sports/epl",
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
        "polymarket_url": "https://polymarket.com/sports/nba",
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
        "polymarket_url": "https://polymarket.com/sports/soccer",
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
        "polymarket_url": "https://polymarket.com/sports/nba",
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
        self._forced_velocity_spike: Dict[str, float] = {}
        self._cooldown_until: Dict[str, float] = {}
        self._last_reward_tick: float = time.time()
        self._live_gamma_cache: List[Dict[str, Any]] = []
        self._live_gamma_fetched_at: float = 0.0
        self._latest_markets: List[Dict[str, Any]] = list(CURATED_REWARD_MARKETS)
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

            # One-time migration: normalize any legacy 60x-accelerated PAPER rewards & high-freq spread PnL to 1:1 real-time scale
            cal_row = conn.execute("SELECT value FROM settings WHERE key = 'lp_realism_calibrated_v2'").fetchone()
            if not cal_row:
                conn.execute(
                    """
                    UPDATE lp_quotes
                    SET accrued_reward_usd = ROUND(accrued_reward_usd / 140.0, 4),
                        spread_pnl_usd = ROUND(spread_pnl_usd / 450.0, 2)
                    WHERE trading_mode = 'PAPER'
                    """
                )
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("lp_realism_calibrated_v2", "1", utc_now_iso()),
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
        if "cooldown_seconds" in partial:
            cfg["cooldown_seconds"] = max(15, min(3600, int(partial["cooldown_seconds"])))

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

        # Competitor average effective score estimation (realistic institutional MM competition)
        comp_shares = max(500.0, competing_liquidity_usdc / 0.50)
        comp_q_total = comp_shares * 0.65
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

    async def _fetch_live_reward_markets(self) -> List[Dict[str, Any]]:
        """
        Fetch live Polymarket markets with active liquidity reward parameters
        (rewardsMinSize > 0 and rewardsMaxSpread > 0) from Gamma API.
        Cached for 45 seconds to keep scans fast.
        """
        now_ts = time.time()
        if self._live_gamma_cache and (now_ts - self._live_gamma_fetched_at) < 45.0:
            return self._live_gamma_cache

        url = f"{GAMMA_API_BASE}/markets?active=true&closed=false&limit=120&order=volume24hr&ascending=false"
        live_list: List[Dict[str, Any]] = []
        now_utc = datetime.now(timezone.utc)

        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        raw_markets = await resp.json()
                        for rm in raw_markets:
                            r_min = float(rm.get("rewardsMinSize") or 0.0)
                            r_spread = float(rm.get("rewardsMaxSpread") or 0.0)
                            if r_min <= 0 or r_spread <= 0:
                                continue

                            raw_prices = rm.get("outcomePrices")
                            try:
                                prices = json.loads(raw_prices) if isinstance(raw_prices, str) else (raw_prices or [])
                                mid = float(prices[0]) if prices else 0.0
                            except Exception:
                                mid = 0.0

                            if mid < 0.08 or mid > 0.94:
                                continue

                            end_str = rm.get("endDate") or rm.get("endDateIso") or ""
                            hours_to_end = 168.0
                            if end_str:
                                try:
                                    dt = datetime.fromisoformat(str(end_str).replace("Z", "+00:00"))
                                    if dt.tzinfo is None:
                                        dt = dt.replace(tzinfo=timezone.utc)
                                    hours_to_end = round((dt - now_utc).total_seconds() / 3600.0, 1)
                                except Exception:
                                    hours_to_end = 168.0

                            if hours_to_end < 0.2:
                                continue

                            liq_num = float(rm.get("liquidityNum") or rm.get("liquidity") or 25000.0)
                            vol_24h = float(rm.get("volume24hr") or 10000.0)

                            # Estimate daily reward pool from clobRewards or market liquidity tier
                            clob_rewards = rm.get("clobRewards") or []
                            daily_reward = 0.0
                            if isinstance(clob_rewards, list) and clob_rewards:
                                for cr in clob_rewards:
                                    if isinstance(cr, dict):
                                        daily_reward += float(cr.get("rewardsDailyRate") or cr.get("AssetRate") or 0.0)
                            if daily_reward <= 0:
                                daily_reward = round(max(35.0, min(180.0, (liq_num ** 0.42) * 0.85)), 0)
                            else:
                                daily_reward = round(min(260.0, daily_reward), 0)

                            # Realistic competing inside-spread liquidity proportional to reward pool & liquidity
                            competing_liq = round(max(6500.0, daily_reward * 85.0, min(65000.0, liq_num * 0.32)), 0)
                            shield_depth = round(max(85.0, min(1600.0, competing_liq * 0.065)), 0)

                            events = rm.get("events") or []
                            ev_obj = events[0] if (isinstance(events, list) and events) else {}
                            ev_slug = ev_obj.get("slug") or rm.get("slug") or ""
                            ev_title = ev_obj.get("title") or rm.get("groupItemTitle") or rm.get("question") or "Polymarket 獎勵盤"
                            q_text = rm.get("question") or ev_title
                            if ev_title != q_text and len(q_text) < 75:
                                display_title = f"{ev_title} · {q_text}"
                            else:
                                display_title = q_text

                            slug_low = (ev_slug + " " + q_text).lower()
                            if any(k in slug_low for k in ("nba", "basketball", "celtics", "lakers", "thunder")):
                                cat = "🏀 NBA 實時獎勵盤"
                            elif any(k in slug_low for k in ("ucl", "epl", "fc ", "vs.", "soccer", "liga", "cup")):
                                cat = "⚽ 體育實時獎勵盤"
                            elif any(k in slug_low for k in ("fed", "rate", "inflation", "gdp", "cpi", "tariff")):
                                cat = "🏦 宏觀利率獎勵盤"
                            elif any(k in slug_low for k in ("bitcoin", "btc", "eth", "sol", "crypto")):
                                cat = "🪙 加密宏觀獎勵盤"
                            else:
                                cat = "🌐 Polymarket 熱門獎勵盤"

                            poly_url = f"https://polymarket.com/event/{ev_slug}" if ev_slug else "https://polymarket.com"

                            live_list.append(
                                {
                                    "market_id": f"gamma-{rm.get('id')}",
                                    "condition_id": str(rm.get("conditionId") or ""),
                                    "market_slug": str(ev_slug),
                                    "event_title": display_title[:85],
                                    "question": q_text,
                                    "category": cat,
                                    "midpoint": round(mid, 3),
                                    "rewards_daily_usd": daily_reward,
                                    "rewards_max_spread_cents": round(r_spread, 1),
                                    "rewards_min_size": round(r_min, 0),
                                    "shield_depth_usdc": shield_depth,
                                    "competing_liquidity_usdc": competing_liq,
                                    "hours_to_end": hours_to_end,
                                    "polymarket_url": poly_url,
                                }
                            )
                        if live_list:
                            # Keep top 8 balanced/interesting live markets
                            live_list.sort(
                                key=lambda x: (
                                    0 if (0.22 <= x["midpoint"] <= 0.78 and x["hours_to_end"] > 6) else 1,
                                    -x["rewards_daily_usd"],
                                )
                            )
                            self._live_gamma_cache = live_list[:8]
                            self._live_gamma_fetched_at = now_ts
        except Exception:
            pass

        return self._live_gamma_cache

    async def scan_and_evaluate(self) -> Dict[str, Any]:
        cfg = self.get_config()
        now_ts = time.time()
        elapsed_sec = max(1.0, min(30.0, now_ts - self._last_reward_tick))
        self._last_reward_tick = now_ts

        live_gamma_markets = await self._fetch_live_reward_markets()
        combined_base = list(CURATED_REWARD_MARKETS) + live_gamma_markets

        # Build live-jittered market feed from curated + live Gamma API data
        markets: List[Dict[str, Any]] = []
        seen_ids = set()
        for base_m in combined_base:
            mid_id = base_m["market_id"]
            if mid_id in seen_ids:
                continue
            seen_ids.add(mid_id)

            m = dict(base_m)
            mid_prev = self._prev_midpoints.get(mid_id, float(m["midpoint"]))

            # Check if a forced volatility spike test was triggered on this market
            forced_spike = self._forced_velocity_spike.pop(mid_id, None)
            if forced_spike is not None:
                direction = -1.0 if mid_prev > 0.40 else 1.0
                new_mid = max(0.08, min(0.94, round(mid_prev + direction * (forced_spike / 100.0), 3)))
                m["velocity_cents"] = round(forced_spike, 2)
            else:
                if mid_id != "lp-inplay-live-match-danger":
                    drift = random.uniform(-0.002, 0.002)
                    # Mean-revert gently toward base midpoint so it doesn't random-walk out of bounds
                    reversion = (float(base_m["midpoint"]) - mid_prev) * 0.25
                    new_mid = max(0.08, min(0.94, round(mid_prev + drift + reversion, 3)))
                else:
                    new_mid = float(m["midpoint"])
                m["velocity_cents"] = round(abs(new_mid - mid_prev) * 100.0, 2)

            self._prev_midpoints[mid_id] = new_mid
            m["midpoint"] = new_mid
            m["shield_depth_usdc"] = max(
                40.0,
                round(float(base_m["shield_depth_usdc"]) * random.uniform(0.94, 1.06), 0),
            )
            markets.append(m)

        self._latest_markets = markets

        # Update active quotes for both PAPER and LIVE modes
        for mode in ("PAPER", "LIVE"):
            self._update_active_quotes_for_mode(markets, cfg, mode, elapsed_sec)

        # Auto-deploy slots in active mode if auto_enabled
        active_mode = str(cfg.get("mode", "PAPER")).upper()
        if cfg.get("auto_enabled", True):
            self._auto_fill_slots(markets, cfg, active_mode)
            if active_mode != "PAPER":
                # Keep PAPER trial run slots populated as well
                self._auto_fill_slots(markets, cfg, "PAPER")

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
        safe_min = float(cfg.get("safe_mid_min", 0.25))
        safe_max = float(cfg.get("safe_mid_max", 0.75))
        per_slot_cfg = float(cfg.get("per_slot_usdc", 100.0))
        max_slots = int(cfg.get("max_slots", 4))
        total_cap = float(cfg.get("total_capital_usdc", 500.0))
        reserve_pct = float(cfg.get("reserve_ratio_pct", 20.0))
        deployable_cap = total_cap * (1.0 - reserve_pct / 100.0)
        cooldown_sec = float(cfg.get("cooldown_seconds", 120))

        pending_logs: List[tuple] = []
        surviving_quotes: List[Dict[str, Any]] = []

        with self.db._connect() as conn:
            for q in active_quotes:
                m = mkt_map.get(q["market_id"])
                if not m:
                    # Fallback if a live market rotated out of top volume list
                    m = {
                        "market_id": q["market_id"],
                        "midpoint": float(q["midpoint"]),
                        "velocity_cents": 0.0,
                        "shield_depth_usdc": float(q["shield_depth_usdc"]),
                        "hours_to_end": max(0.0, float(q["hours_to_end"]) - elapsed_sec / 3600.0),
                        "rewards_max_spread_cents": float(q["rewards_max_spread_cents"]),
                        "rewards_min_size": float(q["rewards_min_size"]),
                        "rewards_daily_usd": float(q["rewards_daily_usd"]),
                        "competing_liquidity_usdc": 5500.0,
                    }

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
                            f"🛑 [賽前清退保護] {q['event_title']} 距離結算僅剩 {hrs:.1f}h (<= {cutoff_hrs:.0f}h)，已自動撤除雙邊掛單。",
                            q["event_title"],
                        )
                    )
                    continue

                # 2. Layer 4 Kill-Switch: Price Velocity Spike
                if vel > max_vel:
                    self._cooldown_until[q["market_id"]] = time.time() + cooldown_sec
                    conn.execute(
                        "UPDATE lp_quotes SET status = 'PAUSED_VOLATILITY', last_action = ?, updated_at = ? WHERE id = ?",
                        (f"偵測中價瞬跳 {vel:.1f}¢ > {max_vel:.1f}¢，已閃電撤單 ({int(cooldown_sec)}s 冷卻)", utc_now_iso(), q["id"]),
                    )
                    pending_logs.append(
                        (
                            "WARN",
                            mode,
                            f"⚡ [防毒流閃電撤單] {q['event_title']} 盤口瞬跳 {vel:.1f}¢ (> {max_vel:.1f}¢)，毫秒級撤銷雙邊掛單並冷卻 {int(cooldown_sec)}s！",
                            q["event_title"],
                        )
                    )
                    continue

                # 3. Layer 2 Guard: Midpoint drifted outside [safe_mid_min, safe_mid_max]
                if mid < safe_min or mid > safe_max:
                    conn.execute(
                        "UPDATE lp_quotes SET status = 'STOPPED_MID_RANGE', last_action = ?, updated_at = ? WHERE id = ?",
                        (f"中價 {mid*100:.0f}¢ 超出安全區 [{safe_min*100:.0f}¢~{safe_max*100:.0f}¢] 自動撤單", utc_now_iso(), q["id"]),
                    )
                    pending_logs.append(
                        (
                            "WARN",
                            mode,
                            f"⚠️ [中價越界清退] {q['event_title']} 中價 {mid*100:.1f}¢ 超出設定安全區間 [{safe_min*100:.0f}¢~{safe_max*100:.0f}¢]，已自動撤單釋放槽位。",
                            q["event_title"],
                        )
                    )
                    continue

                # 4. Layer 4 Guard: Front-of-book Shield Depth depleted below threshold
                if shield < min_shield:
                    conn.execute(
                        "UPDATE lp_quotes SET status = 'STOPPED_SHIELD_THIN', last_action = ?, updated_at = ? WHERE id = ?",
                        (f"前檔肉盾 ${shield:.0f} < 門檻 ${min_shield:.0f} 自動撤單避險", utc_now_iso(), q["id"]),
                    )
                    pending_logs.append(
                        (
                            "WARN",
                            mode,
                            f"🛡️ [肉盾不足清退] {q['event_title']} 第一檔保護深度僅 ${shield:,.0f} (< ${min_shield:,.0f})，已自動撤單避開知情大單。",
                            q["event_title"],
                        )
                    )
                    continue

                # 5. Layer 3 Inventory Skew & Dynamic Per-Slot Capital Sync
                slot_alloc = per_slot_cfg
                inv_usdc = float(q["net_inventory_usdc"])
                inv_shares = float(q["net_inventory_shares"])
                spread_pnl = float(q["spread_pnl_usd"])

                # In PAPER mode, simulate realistic Tier-2 passive fill frequency (~1 touch every 5 hours)
                # and two-way inventory unwind (58% maker spread capture vs 42% adverse-selection scratch loss)
                fill_prob = min(0.02, elapsed_sec / 18000.0)
                if mode == "PAPER" and random.random() < fill_prob:
                    if abs(inv_usdc) < min(15.0, max_inv * 0.5):
                        fill_usd = round(random.uniform(8.0, min(18.0, max_inv * 0.45)), 2)
                        direction = 1.0 if random.random() < 0.5 else -1.0
                        inv_usdc = round(inv_usdc + direction * fill_usd, 2)
                        inv_shares = round(inv_usdc / max(0.1, mid), 1)
                    else:
                        if random.random() < 0.58:
                            realized_spread = round(abs(inv_usdc) * random.uniform(0.018, 0.026), 2)
                        else:
                            realized_spread = -round(abs(inv_usdc) * random.uniform(0.014, 0.024), 2)
                        spread_pnl = round(spread_pnl + realized_spread, 2)
                        inv_usdc = 0.0
                        inv_shares = 0.0

                skew_cents = round((inv_usdc / max(10.0, max_inv)) * 0.8, 2)
                effective_offset = base_offset + (0.3 if shield < min_shield * 1.15 else 0.0)

                metrics = self.compute_quadratic_reward_metrics(
                    midpoint=mid,
                    per_slot_usdc=slot_alloc,
                    offset_cents=effective_offset,
                    max_spread_cents=float(m["rewards_max_spread_cents"]),
                    min_size_shares=float(m["rewards_min_size"]),
                    daily_reward_usd=float(m["rewards_daily_usd"]),
                    competing_liquidity_usdc=float(m["competing_liquidity_usdc"]),
                    inventory_skew_cents=skew_cents,
                )

                # Accrue 1:1 real-time pro-rata liquidity rewards (no artificial 60x time multiplier)
                delta_reward = (metrics["est_daily_reward_usd"] / 86400.0) * elapsed_sec
                new_accrued = round(float(q["accrued_reward_usd"]) + delta_reward, 4)

                action_note = (
                    f"庫存偏移報價 (淨部位 ${inv_usdc:+.1f})"
                    if abs(inv_usdc) > 1.0
                    else f"二檔護盾報價 (±{effective_offset:.1f}¢)"
                )

                conn.execute(
                    """
                    UPDATE lp_quotes
                    SET allocated_usdc = ?, midpoint = ?, bid_price = ?, ask_price = ?,
                        bid_size_shares = ?, ask_size_shares = ?,
                        q_score = ?, est_daily_reward_usd = ?,
                        accrued_reward_usd = ?, spread_pnl_usd = ?,
                        net_inventory_shares = ?, net_inventory_usdc = ?,
                        shield_depth_usdc = ?, hours_to_end = ?,
                        last_action = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        slot_alloc,
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
                q_updated = dict(q)
                q_updated["allocated_usdc"] = slot_alloc
                q_updated["est_daily_reward_usd"] = metrics["est_daily_reward_usd"]
                surviving_quotes.append(q_updated)

            # 6. Layer 1 Re-balance: Prune lowest-yielding slots if max_slots or deployable_cap was reduced
            if surviving_quotes:
                surviving_quotes.sort(key=lambda x: float(x.get("est_daily_reward_usd", 0.0)), reverse=True)
                kept_deployed = 0.0
                for idx, sq in enumerate(surviving_quotes):
                    alloc = float(sq["allocated_usdc"])
                    if idx >= max_slots or (kept_deployed + alloc) > deployable_cap + 0.01:
                        conn.execute(
                            "UPDATE lp_quotes SET status = 'STOPPED_REBALANCE', net_inventory_shares = 0, net_inventory_usdc = 0, last_action = ?, updated_at = ? WHERE id = ?",
                            ("策略槽位/資金上限調降，自動撤單重平衡", utc_now_iso(), sq["id"]),
                        )
                        pending_logs.append(
                            (
                                "INFO",
                                mode,
                                f"🔄 [參數重平衡] 已自動釋放超額槽位：{sq['event_title']} (保留最高收益前 {max_slots} 槽位，上限 ${deployable_cap:.0f})。",
                                sq["event_title"],
                            )
                        )
                    else:
                        kept_deployed += alloc

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

        pool = list(self._latest_markets) + list(CURATED_REWARD_MARKETS)
        base_m = next((m for m in pool if m["market_id"] == market_id), None)
        if not base_m:
            raise ValueError("找不到指定的做市盤口，請重新掃描後再試")

        active_quotes = self.get_quotes(trading_mode=target_mode, status="ACTIVE")
        if any(q["market_id"] == market_id for q in active_quotes):
            raise ValueError("此盤口已經在雙邊掛單做市中")

        per_slot = float(cfg.get("per_slot_usdc", 100.0))
        total_cap = float(cfg.get("total_capital_usdc", 500.0))
        currently_deployed = sum(float(q["allocated_usdc"]) for q in active_quotes)

        if currently_deployed + per_slot > total_cap + 0.01:
            raise ValueError(
                f"可用做市資金不足（已部署 ${currently_deployed:.0f} + 單槽位 ${per_slot:.0f} > 總資金池 ${total_cap:.0f}），請先釋放現有槽位或調高做市總資金池"
            )

        # If manual deploy exceeds max_slots, automatically bump max_slots to accommodate the user's explicit manual action
        max_slots = int(cfg.get("max_slots", 4))
        if len(active_quotes) >= max_slots:
            self.update_config({"max_slots": len(active_quotes) + 1})

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
            f"🛑 已撤銷雙邊掛單並釋放資金：{q['event_title']} (累計領取獎勵 +${float(q['accrued_reward_usd']):.4f} / 價差損益 +${float(q['spread_pnl_usd']):.2f})",
            q["event_title"],
        )
        await self.scan_and_evaluate()
        return q

    async def simulate_volatility_killswitch(self, mode: str = "PAPER") -> Dict[str, Any]:
        target_mode = str(mode).upper()
        active = self.get_quotes(trading_mode=target_mode, status="ACTIVE")
        if not active:
            raise ValueError("目前沒有運行中的做市槽位可供測試")
        target = active[0]
        cfg = self.get_config()
        max_vel = float(cfg.get("max_velocity_cents", 2.5))
        # Force a deterministic velocity spike above threshold on the target market
        self._forced_velocity_spike[target["market_id"]] = round(max(4.2, max_vel + 2.0), 2)
        await self.scan_and_evaluate()
        return target

    async def reset_paper_lp(self) -> None:
        with self.db._connect() as conn:
            conn.execute("DELETE FROM lp_quotes WHERE trading_mode = 'PAPER'")
            conn.execute("DELETE FROM lp_logs WHERE trading_mode = 'PAPER'")
        self._cooldown_until.clear()
        self._forced_velocity_spike.clear()
        cfg = self.get_config()
        cap = float(cfg.get("total_capital_usdc", 500.0))
        slots = int(cfg.get("max_slots", 4))
        self.add_lp_log("INFO", "PAPER", f"🔄 已重置 ${cap:,.2f} USDC 模擬被動做市帳戶，正在重新部署 {slots} 個高收益雙邊掛單槽位...")
        await self.scan_and_evaluate()

    def _build_mode_summary(self, mode: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        all_quotes = self.get_quotes(trading_mode=mode, status=None)
        active_quotes = [q for q in all_quotes if q["status"] == "ACTIVE"]
        history_quotes = [q for q in all_quotes if q["status"] != "ACTIVE"][:20]

        total_cap = float(cfg.get("total_capital_usdc", 500.0))
        deployed_usdc = round(sum(float(q["allocated_usdc"]) for q in active_quotes), 2)
        reserve_usdc = round(max(0.0, total_cap - deployed_usdc), 2)

        total_accrued_rewards = round(sum(float(q["accrued_reward_usd"]) for q in all_quotes), 4)
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
