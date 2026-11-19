import json
import math
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from app.config import CLOB_API_BASE, GAMMA_API_BASE, POLYGON_CHAIN_ID

SPORT_META = {
    "NBA": {"label": "NBA 籃球", "icon": "🏀"},
    "SOCCER": {"label": "足球賽事", "icon": "⚽"},
    "NFL": {"label": "NFL 美足", "icon": "🏈"},
    "MLB": {"label": "MLB 棒球", "icon": "⚾"},
    "NHL": {"label": "NHL 冰球", "icon": "🏒"},
    "TENNIS": {"label": "網球賽事", "icon": "🎾"},
    "UFC": {"label": "UFC 格鬥", "icon": "🥊"},
    "ESPORTS": {"label": "電競賽事", "icon": "🎮"},
    "OTHER_SPORTS": {"label": "綜合運動", "icon": "🏆"},
}

SPORT_KEYWORDS = {
    "NBA": [
        "nba", "basketball", "lakers", "celtics", "warriors", "knicks", "bucks",
        "nuggets", "suns", "mavericks", "thunder", "clippers", "76ers", "sixers",
        "heat", "cavaliers", "timberwolves", "pacers", "ncaa basketball", "euroleague",
    ],
    "SOCCER": [
        "soccer", "football", "premier league", "epl", "champions league", "ucl",
        "la liga", "serie a", "bundesliga", "mls", "world cup", "arsenal", "chelsea",
        "liverpool", "manchester city", "man city", "man united", "real madrid",
        "barcelona", "bayern", "psg", "inter milan", "juventus", "tottenham",
    ],
    "NFL": [
        "nfl", "super bowl", "chiefs", "eagles", "49ers", "ravens", "bills",
        "cowboys", "lions", "packers", "bengals", "texans", "jets", "dolphins",
        "touchdown", "quarterback",
    ],
    "MLB": [
        "mlb", "baseball", "world series", "yankees", "dodgers", "mets", "braves",
        "astros", "phillies", "red sox", "padres", "orioles", "cubs", "blue jays",
    ],
    "NHL": [
        "nhl", "hockey", "stanley cup", "oilers", "panthers", "rangers", "bruins",
        "maple leafs", "avalanche", "stars", "canucks", "golden knights", "devils",
    ],
    "TENNIS": [
        "tennis", "atp", "wta", "wimbledon", "us open", "roland garros", "french open",
        "australian open", "alcaraz", "sinner", "djokovic", "medvedev", "zverev",
        "swiatek", "sabalenka", "gauff",
    ],
    "UFC": [
        "ufc", "mma", "boxing", "fight night", "bellator", "pfl", "knockout",
        "heavyweight", "lightweight", "welterweight", "middleweight",
    ],
    "ESPORTS": [
        "esports", "league of legends", "lol", "cs2", "counter-strike", "valorant",
        "dota", "lck", "lpl", "worlds", "t1", "gen.g", "fnatic", "g2 esports",
    ],
}


def classify_sport(text: str, tags: Optional[List[str]] = None) -> str:
    combined = f"{text} {' '.join(tags or [])}".lower()
    for sport_code, keywords in SPORT_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                return sport_code
    return "OTHER_SPORTS"


def is_sports_related(text: str, tags: Optional[List[str]] = None) -> bool:
    combined = f"{text} {' '.join(tags or [])}".lower()
    if any(t in combined for t in ["sport", "sports", "game", "match", "vs.", "vs ", "championship", "league", "tournament"]):
        return True
    return classify_sport(text, tags) != "OTHER_SPORTS"


def _parse_json_list(val: Any) -> List[Any]:
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val.strip():
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return []


class SimulatedSportsFeed:
    """Realistic in-play and pre-match Polymarket Sports simulator for sandbox/testing."""

    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self._markets: List[Dict[str, Any]] = [
            {
                "market_id": "sim-nba-bos-nyk-101",
                "condition_id": "0x91a1b2c3d4e5f601",
                "sport": "NBA",
                "event_title": "NBA: Boston Celtics vs. New York Knicks (Live Q3)",
                "question": "Will the Boston Celtics beat the New York Knicks?",
                "market_slug": "nba-boston-celtics-vs-new-york-knicks",
                "outcomes": ["Boston Celtics", "New York Knicks"],
                "probs": [0.742, 0.258],
                "token_ids": ["7101001", "7101002"],
                "volume_usdc": 284500.0,
                "liquidity_usdc": 46200.0,
                "spread": 0.012,
                "end_date": (now + timedelta(hours=2)).isoformat(),
            },
            {
                "market_id": "sim-epl-ars-che-102",
                "condition_id": "0x91a1b2c3d4e5f602",
                "sport": "SOCCER",
                "event_title": "Premier League: Arsenal vs. Chelsea (62' 1-0)",
                "question": "Will Arsenal win vs. Chelsea in Regular Time?",
                "market_slug": "epl-arsenal-vs-chelsea-winner",
                "outcomes": ["Arsenal", "Chelsea / Draw"],
                "probs": [0.718, 0.282],
                "token_ids": ["7102001", "7102002"],
                "volume_usdc": 412900.0,
                "liquidity_usdc": 68400.0,
                "spread": 0.010,
                "end_date": (now + timedelta(hours=1)).isoformat(),
            },
            {
                "market_id": "sim-nba-lal-gsw-103",
                "condition_id": "0x91a1b2c3d4e5f603",
                "sport": "NBA",
                "event_title": "NBA: Los Angeles Lakers vs. Golden State Warriors (Live Q2)",
                "question": "Will the Los Angeles Lakers win against the Golden State Warriors?",
                "market_slug": "nba-lakers-vs-warriors",
                "outcomes": ["Los Angeles Lakers", "Golden State Warriors"],
                "probs": [0.684, 0.316],  # Just below 70% - can cross 70% dynamically!
                "token_ids": ["7103001", "7103002"],
                "volume_usdc": 359000.0,
                "liquidity_usdc": 52100.0,
                "spread": 0.014,
                "end_date": (now + timedelta(hours=3)).isoformat(),
            },
            {
                "market_id": "sim-nfl-kc-buf-104",
                "condition_id": "0x91a1b2c3d4e5f604",
                "sport": "NFL",
                "event_title": "NFL: Kansas City Chiefs vs. Buffalo Bills (Q4 24-17)",
                "question": "Will the Kansas City Chiefs defeat the Buffalo Bills?",
                "market_slug": "nfl-chiefs-vs-bills",
                "outcomes": ["Kansas City Chiefs", "Buffalo Bills"],
                "probs": [0.785, 0.215],
                "token_ids": ["7104001", "7104002"],
                "volume_usdc": 618200.0,
                "liquidity_usdc": 94500.0,
                "spread": 0.009,
                "end_date": (now + timedelta(hours=1, minutes=30)).isoformat(),
            },
            {
                "market_id": "sim-ucl-rma-bay-105",
                "condition_id": "0x91a1b2c3d4e5f605",
                "sport": "SOCCER",
                "event_title": "UEFA Champions League: Real Madrid vs. Bayern Munich",
                "question": "Will Real Madrid advance past Bayern Munich?",
                "market_slug": "ucl-real-madrid-vs-bayern-munich",
                "outcomes": ["Real Madrid", "Bayern Munich"],
                "probs": [0.672, 0.328],
                "token_ids": ["7105001", "7105002"],
                "volume_usdc": 524000.0,
                "liquidity_usdc": 81300.0,
                "spread": 0.011,
                "end_date": (now + timedelta(hours=5)).isoformat(),
            },
            {
                "market_id": "sim-tennis-alc-med-106",
                "condition_id": "0x91a1b2c3d4e5f606",
                "sport": "TENNIS",
                "event_title": "ATP Finals: Carlos Alcaraz vs. Daniil Medvedev (Set 2)",
                "question": "Will Carlos Alcaraz win the match vs. Daniil Medvedev?",
                "market_slug": "atp-alcaraz-vs-medvedev",
                "outcomes": ["Carlos Alcaraz", "Daniil Medvedev"],
                "probs": [0.812, 0.188],
                "token_ids": ["7106001", "7106002"],
                "volume_usdc": 148000.0,
                "liquidity_usdc": 27400.0,
                "spread": 0.015,
                "end_date": (now + timedelta(hours=2)).isoformat(),
            },
            {
                "market_id": "sim-mlb-lad-nyy-107",
                "condition_id": "0x91a1b2c3d4e5f607",
                "sport": "MLB",
                "event_title": "MLB: Los Angeles Dodgers vs. New York Yankees (7th Inning)",
                "question": "Will the Los Angeles Dodgers win vs. New York Yankees?",
                "market_slug": "mlb-dodgers-vs-yankees",
                "outcomes": ["Los Angeles Dodgers", "New York Yankees"],
                "probs": [0.726, 0.274],
                "token_ids": ["7107001", "7107002"],
                "volume_usdc": 227400.0,
                "liquidity_usdc": 38900.0,
                "spread": 0.013,
                "end_date": (now + timedelta(hours=2)).isoformat(),
            },
            {
                "market_id": "sim-ufc-main-108",
                "condition_id": "0x91a1b2c3d4e5f608",
                "sport": "UFC",
                "event_title": "UFC Main Event: Islam Makhachev vs. Arman Tsarukyan",
                "question": "Will Islam Makhachev defeat Arman Tsarukyan?",
                "market_slug": "ufc-makhachev-vs-tsarukyan",
                "outcomes": ["Islam Makhachev", "Arman Tsarukyan"],
                "probs": [0.754, 0.246],
                "token_ids": ["7108001", "7108002"],
                "volume_usdc": 319800.0,
                "liquidity_usdc": 44100.0,
                "spread": 0.016,
                "end_date": (now + timedelta(hours=6)).isoformat(),
            },
            {
                "market_id": "sim-nhl-edm-fla-109",
                "condition_id": "0x91a1b2c3d4e5f609",
                "sport": "NHL",
                "event_title": "NHL: Edmonton Oilers vs. Florida Panthers (P2 3-2)",
                "question": "Will the Edmonton Oilers beat the Florida Panthers?",
                "market_slug": "nhl-oilers-vs-panthers",
                "outcomes": ["Edmonton Oilers", "Florida Panthers"],
                "probs": [0.648, 0.352],
                "token_ids": ["7109001", "7109002"],
                "volume_usdc": 96500.0,
                "liquidity_usdc": 19200.0,
                "spread": 0.018,
                "end_date": (now + timedelta(hours=2, minutes=15)).isoformat(),
            },
            {
                "market_id": "sim-esports-t1-geng-110",
                "condition_id": "0x91a1b2c3d4e5f610",
                "sport": "ESPORTS",
                "event_title": "LoL LCK Showdown: T1 vs. Gen.G (Map 3)",
                "question": "Will T1 win the series against Gen.G?",
                "market_slug": "lol-t1-vs-geng-series",
                "outcomes": ["T1", "Gen.G"],
                "probs": [0.692, 0.308],
                "token_ids": ["7110001", "7110002"],
                "volume_usdc": 182300.0,
                "liquidity_usdc": 31500.0,
                "spread": 0.014,
                "end_date": (now + timedelta(hours=1, minutes=45)).isoformat(),
            },
            {
                "market_id": "sim-nba-den-phx-111",
                "condition_id": "0x91a1b2c3d4e5f611",
                "sport": "NBA",
                "event_title": "NBA: Denver Nuggets vs. Phoenix Suns (Q4 Blowout)",
                "question": "Will the Denver Nuggets beat the Phoenix Suns?",
                "market_slug": "nba-nuggets-vs-suns",
                "outcomes": ["Denver Nuggets", "Phoenix Suns"],
                "probs": [0.972, 0.028],  # Above 95% max cap -> tests upper bound risk control!
                "token_ids": ["7111001", "7111002"],
                "volume_usdc": 210400.0,
                "liquidity_usdc": 64000.0,
                "spread": 0.008,
                "end_date": (now + timedelta(minutes=35)).isoformat(),
            },
            {
                "market_id": "sim-soccer-lowliq-112",
                "condition_id": "0x91a1b2c3d4e5f612",
                "sport": "SOCCER",
                "event_title": "MLS Reserve Match: Austin FC II vs. Houston Dynamo 2",
                "question": "Will Austin FC II win vs. Houston Dynamo 2?",
                "market_slug": "mls-austin-vs-houston",
                "outcomes": ["Austin FC II", "Houston Dynamo 2"],
                "probs": [0.730, 0.270],
                "token_ids": ["7112001", "7112002"],
                "volume_usdc": 1200.0,
                "liquidity_usdc": 420.0,  # Low liquidity -> tests min liquidity risk guard!
                "spread": 0.068,
                "end_date": (now + timedelta(hours=4)).isoformat(),
            },
        ]

    def tick_and_get_markets(self) -> List[Dict[str, Any]]:
        result = []
        for m in self._markets:
            # Apply realistic subtle probability movement (-1.2% to +1.5%)
            delta = random.uniform(-0.011, 0.014)
            p0 = max(0.03, min(0.985, round(m["probs"][0] + delta, 3)))
            p1 = round(1.0 - p0, 3)
            m["probs"] = [p0, p1]
            m["volume_usdc"] = round(m["volume_usdc"] + random.uniform(50.0, 650.0), 2)

            outcomes_list = []
            half_spread = m["spread"] / 2.0
            for idx, label in enumerate(m["outcomes"]):
                prob = m["probs"][idx]
                outcomes_list.append(
                    {
                        "index": idx,
                        "label": label,
                        "prob": prob,
                        "best_bid": round(max(0.01, prob - half_spread), 3),
                        "best_ask": round(min(0.99, prob + half_spread), 3),
                        "token_id": m["token_ids"][idx],
                    }
                )

            top_outcome = max(outcomes_list, key=lambda x: x["prob"])
            sport_code = m["sport"]
            meta = SPORT_META.get(sport_code, SPORT_META["OTHER_SPORTS"])

            result.append(
                {
                    "market_id": m["market_id"],
                    "condition_id": m["condition_id"],
                    "sport": sport_code,
                    "sport_label": meta["label"],
                    "sport_icon": meta["icon"],
                    "event_title": m["event_title"],
                    "question": m["question"],
                    "market_slug": m["market_slug"],
                    "polymarket_url": f"https://polymarket.com/event/{m['market_slug']}",
                    "volume_usdc": m["volume_usdc"],
                    "liquidity_usdc": m["liquidity_usdc"],
                    "spread": m["spread"],
                    "end_date": m["end_date"],
                    "outcomes": outcomes_list,
                    "top_outcome": top_outcome,
                    "source": "SIMULATION",
                }
            )
        return result

    def trigger_breakout_surge(self) -> Dict[str, Any]:
        """Push a sub-70% market above 72% to demonstrate immediate auto-sniping."""
        candidates = [m for m in self._markets if 0.55 <= m["probs"][0] < 0.70]
        target = random.choice(candidates) if candidates else self._markets[2]
        new_p0 = round(random.uniform(0.725, 0.795), 3)
        target["probs"] = [new_p0, round(1.0 - new_p0, 3)]
        return {
            "market_id": target["market_id"],
            "event_title": target["event_title"],
            "outcome_label": target["outcomes"][0],
            "new_prob": new_p0,
        }


class PolymarketClient:
    def __init__(self) -> None:
        self.simulator = SimulatedSportsFeed()
        self.last_source_used = "SIMULATION"
        self.last_api_error: Optional[str] = None

    async def fetch_sports_markets(self, mode: str = "AUTO") -> Tuple[List[Dict[str, Any]], str]:
        """
        Fetch active Polymarket sports markets.
        mode: "AUTO" (try Live Gamma API, fallback to Simulation), "LIVE_API", or "SIMULATION".
        Returns (markets_list, active_source_name).
        """
        if mode == "SIMULATION":
            self.last_source_used = "SIMULATION"
            return self.simulator.tick_and_get_markets(), "SIMULATION"

        try:
            live_markets = await self._fetch_from_gamma_api()
            if live_markets:
                self.last_source_used = "LIVE_GAMMA_API"
                self.last_api_error = None
                return live_markets, "LIVE_GAMMA_API"
            if mode == "LIVE_API":
                return [], "LIVE_GAMMA_API"
        except Exception as exc:
            self.last_api_error = str(exc)
            if mode == "LIVE_API":
                raise

        # Fallback to realistic dynamic simulator in AUTO mode
        self.last_source_used = "SIMULATION_FALLBACK"
        return self.simulator.tick_and_get_markets(), "SIMULATION_FALLBACK"

    async def _fetch_from_gamma_api(self) -> List[Dict[str, Any]]:
        timeout = aiohttp.ClientTimeout(total=8.0)
        headers = {
            "Accept": "application/json",
            "User-Agent": "PolySports-AutoTrader/1.0",
        }
        markets_by_id: Dict[str, Dict[str, Any]] = {}

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            # 1. Fetch active sports events from Gamma API
            events_url = f"{GAMMA_API_BASE}/events"
            params_list = [
                {"tag_slug": "sports", "active": "true", "closed": "false", "limit": "60"},
                {"active": "true", "closed": "false", "limit": "80", "order": "volume24hr", "ascending": "false"},
            ]

            for params in params_list:
                try:
                    async with session.get(events_url, params=params) as resp:
                        if resp.status != 200:
                            continue
                        events = await resp.json()
                        if not isinstance(events, list):
                            continue
                        for ev in events:
                            self._extract_markets_from_event(ev, markets_by_id, force_sports=(params.get("tag_slug") == "sports"))
                except Exception:
                    continue

            # 2. If still empty, query /markets directly
            if not markets_by_id:
                markets_url = f"{GAMMA_API_BASE}/markets"
                m_params = {"active": "true", "closed": "false", "limit": "100", "order": "volume24hr", "ascending": "false"}
                async with session.get(markets_url, params=m_params) as resp:
                    if resp.status == 200:
                        raw_markets = await resp.json()
                        if isinstance(raw_markets, list):
                            for rm in raw_markets:
                                parsed = self._normalize_gamma_market(rm, event_obj=None, force_sports=False)
                                if parsed:
                                    markets_by_id[parsed["market_id"]] = parsed

        result = list(markets_by_id.values())
        result.sort(key=lambda x: x["top_outcome"]["prob"], reverse=True)
        return result

    def _extract_markets_from_event(
        self, ev: Dict[str, Any], out: Dict[str, Dict[str, Any]], force_sports: bool = False
    ) -> None:
        raw_tags = ev.get("tags") or []
        tag_strings = []
        for t in raw_tags:
            if isinstance(t, dict):
                tag_strings.append(str(t.get("slug") or t.get("label") or ""))
            else:
                tag_strings.append(str(t))

        ev_title = str(ev.get("title") or ev.get("ticker") or "")
        if not force_sports and not is_sports_related(ev_title, tag_strings):
            return

        for m in ev.get("markets") or []:
            if not isinstance(m, dict):
                continue
            parsed = self._normalize_gamma_market(m, event_obj=ev, force_sports=True, tag_strings=tag_strings)
            if parsed:
                out[parsed["market_id"]] = parsed

    def _normalize_gamma_market(
        self,
        m: Dict[str, Any],
        event_obj: Optional[Dict[str, Any]] = None,
        force_sports: bool = False,
        tag_strings: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        if m.get("closed") is True or m.get("active") is False:
            return None
        if m.get("acceptingOrders") is False:
            return None

        question = str(m.get("question") or m.get("title") or "").strip()
        event_title = str((event_obj or {}).get("title") or question).strip()
        if not question:
            return None

        if not force_sports and not is_sports_related(f"{event_title} {question}", tag_strings):
            return None

        outcomes_raw = _parse_json_list(m.get("outcomes"))
        prices_raw = _parse_json_list(m.get("outcomePrices"))
        tokens_raw = _parse_json_list(m.get("clobTokenIds"))

        if not outcomes_raw or not prices_raw or len(outcomes_raw) != len(prices_raw):
            return None

        spread_val = float(m.get("spread") or 0.015)
        best_bid_val = m.get("bestBid")
        best_ask_val = m.get("bestAsk")

        outcomes_list = []
        for idx, label in enumerate(outcomes_raw):
            try:
                prob = float(prices_raw[idx])
            except (ValueError, TypeError):
                prob = 0.0
            if prob <= 0.0 or prob >= 1.0:
                continue

            token_id = str(tokens_raw[idx]) if idx < len(tokens_raw) else ""
            half_sp = spread_val / 2.0
            b_bid = float(best_bid_val) if (idx == 0 and best_bid_val is not None) else max(0.01, prob - half_sp)
            b_ask = float(best_ask_val) if (idx == 0 and best_ask_val is not None) else min(0.99, prob + half_sp)

            outcomes_list.append(
                {
                    "index": idx,
                    "label": str(label),
                    "prob": round(prob, 4),
                    "best_bid": round(b_bid, 4),
                    "best_ask": round(b_ask, 4),
                    "token_id": token_id,
                }
            )

        if not outcomes_list:
            return None

        top_outcome = max(outcomes_list, key=lambda x: x["prob"])
        sport_code = classify_sport(f"{event_title} {question}", tag_strings)
        meta = SPORT_META.get(sport_code, SPORT_META["OTHER_SPORTS"])

        market_slug = str((event_obj or {}).get("slug") or m.get("slug") or "")
        vol = float(m.get("volumeNum") or m.get("volume") or (event_obj or {}).get("volume") or 0.0)
        liq = float(m.get("liquidityNum") or m.get("liquidity") or (event_obj or {}).get("liquidity") or 0.0)

        return {
            "market_id": str(m.get("id") or m.get("conditionId")),
            "condition_id": str(m.get("conditionId") or ""),
            "sport": sport_code,
            "sport_label": meta["label"],
            "sport_icon": meta["icon"],
            "event_title": event_title,
            "question": question,
            "market_slug": market_slug,
            "polymarket_url": f"https://polymarket.com/event/{market_slug}" if market_slug else "https://polymarket.com/sports",
            "volume_usdc": round(vol, 2),
            "liquidity_usdc": round(liq, 2),
            "spread": round(spread_val, 4),
            "end_date": str(m.get("endDate") or (event_obj or {}).get("endDate") or ""),
            "outcomes": outcomes_list,
            "top_outcome": top_outcome,
            "source": "LIVE_GAMMA_API",
        }

    async def verify_clob_credentials(self, creds: Dict[str, Any]) -> Dict[str, Any]:
        """Verify Polymarket CLOB configuration and test connectivity."""
        pk = creds.get("private_key", "").strip()
        if not pk:
            return {
                "ok": False,
                "message": "尚未設定 Polygon 錢包私鑰 (POLY_PRIVATE_KEY)，目前僅能使用模擬交易 (Paper Mode)。",
                "clob_sdk_installed": self._is_clob_sdk_installed(),
            }

        sdk_installed = self._is_clob_sdk_installed()
        if sdk_installed:
            try:
                from py_clob_client.client import ClobClient
                from py_clob_client.clob_types import ApiCreds

                client = ClobClient(
                    host=CLOB_API_BASE,
                    key=pk,
                    chain_id=POLYGON_CHAIN_ID,
                    signature_type=int(creds.get("signature_type", 1)),
                    funder=creds.get("funder_address") or None,
                )
                if creds.get("api_key") and creds.get("api_secret") and creds.get("api_passphrase"):
                    client.set_api_creds(
                        ApiCreds(
                            api_key=creds["api_key"],
                            api_secret=creds["api_secret"],
                            api_passphrase=creds["api_passphrase"],
                        )
                    )
                else:
                    derived = client.create_or_derive_api_creds()
                    client.set_api_creds(derived)

                ok_resp = client.get_ok()
                return {
                    "ok": True,
                    "message": f"Polymarket CLOB 驗證成功 (Server: {ok_resp})，可隨時開啟 LIVE 真實自動投注！",
                    "clob_sdk_installed": True,
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "message": f"CLOB 連線或憑證驗證失敗: {exc}",
                    "clob_sdk_installed": True,
                }
        else:
            # Check key format (64 hex chars with or without 0x)
            clean_pk = pk[2:] if pk.startswith("0x") else pk
            valid_hex = len(clean_pk) == 64 and all(c in "0123456789abcdefABCDEF" for c in clean_pk)
            if not valid_hex:
                return {
                    "ok": False,
                    "message": "私鑰格式不正確（需為 64 位元 Hex 字串）。",
                    "clob_sdk_installed": False,
                }
            return {
                "ok": True,
                "message": "私鑰格式驗證通過（部署至 Railway 時將透過 requirements.txt 中的 py-clob-client 執行 EIP-712 簽名下單）。",
                "clob_sdk_installed": False,
            }

    def _is_clob_sdk_installed(self) -> bool:
        try:
            import py_clob_client  # noqa: F401
            return True
        except ImportError:
            return False

    async def place_live_clob_order(
        self,
        creds: Dict[str, Any],
        token_id: str,
        price: float,
        amount_usdc: float,
    ) -> Dict[str, Any]:
        """Place a real BUY order on Polymarket CLOB using py-clob-client."""
        pk = creds.get("private_key", "").strip()
        if not pk:
            raise RuntimeError("未設定 POLY_PRIVATE_KEY，無法執行真實下單")
        if not token_id:
            raise RuntimeError("該市場選項缺少 CLOB Token ID，無法下單")

        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        client = ClobClient(
            host=CLOB_API_BASE,
            key=pk,
            chain_id=POLYGON_CHAIN_ID,
            signature_type=int(creds.get("signature_type", 1)),
            funder=creds.get("funder_address") or None,
        )
        if creds.get("api_key") and creds.get("api_secret") and creds.get("api_passphrase"):
            client.set_api_creds(
                ApiCreds(
                    api_key=creds["api_key"],
                    api_secret=creds["api_secret"],
                    api_passphrase=creds["api_passphrase"],
                )
            )
        else:
            client.set_api_creds(client.create_or_derive_api_creds())

        # Slight marketable buffer (+0.5%) capped at 0.99 for immediate execution
        limit_price = round(min(0.99, price + 0.005), 2)
        shares = round(amount_usdc / limit_price, 2)

        order_args = OrderArgs(
            price=limit_price,
            size=shares,
            side=BUY,
            token_id=token_id,
        )
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)
        order_id = ""
        if isinstance(resp, dict):
            order_id = str(resp.get("orderID") or resp.get("id") or "")
        return {
            "ok": True,
            "order_id": order_id or f"clob-{int(time.time())}",
            "executed_price": limit_price,
            "shares": shares,
            "raw_response": resp,
        }
