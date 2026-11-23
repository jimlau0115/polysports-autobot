import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

from app.database import Database, utc_now_iso
from app.hk_temp_engine import HkTempEngine
from app.hkjc_engine import HkjcQuantEngine
from app.lp_mm_engine import LpMarketMakerEngine
from app.polymarket_client import PolymarketClient, parse_end_date_ts


class TradingEngine:
    def __init__(self, db: Database, poly_client: PolymarketClient) -> None:
        self.db = db
        self.poly_client = poly_client
        self.hk_temp = HkTempEngine(db, poly_client)
        self.lp_mm = LpMarketMakerEngine(db, poly_client)
        self.hkjc = HkjcQuantEngine(db)
        self.latest_markets: List[Dict[str, Any]] = []
        self.active_data_source: str = "INITIALIZING"
        self.last_scan_at: Optional[str] = None
        self.scan_count: int = 0
        self.last_scan_duration_ms: int = 0
        self.is_scanning: bool = False
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._ws_broadcast_cb: Optional[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]] = None
        self._lock = asyncio.Lock()

    def set_broadcast_callback(
        self, cb: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        self._ws_broadcast_cb = cb

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.db.add_log(
            level="INFO",
            trading_mode=self.db.get_config().get("trading_mode", "PAPER"),
            message="PolySports 24/7 自動監測與交易引擎已啟動，正在掃描運動項目勝率 >= 70% 標的...",
        )
        # Perform an immediate initial scan so dashboard has instant data
        await self.run_single_scan()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            cfg = self.db.get_config()
            interval = max(2, int(cfg.get("scan_interval_sec", 5)))
            await asyncio.sleep(interval)
            try:
                await self.run_single_scan()
            except Exception as exc:
                self.db.add_log(
                    level="ERROR",
                    trading_mode=cfg.get("trading_mode", "PAPER"),
                    message=f"背景掃描發生異常: {exc}",
                )

    async def run_single_scan(self) -> Dict[str, Any]:
        async with self._lock:
            self.is_scanning = True
            t0 = time.perf_counter()
            cfg = self.db.get_config()
            data_mode = cfg.get("data_source_mode", "AUTO")
            trading_mode = cfg.get("trading_mode", "PAPER")

            try:
                markets, source = await self.poly_client.fetch_sports_markets(mode=data_mode)
                self.active_data_source = source
            except Exception as exc:
                self.active_data_source = "ERROR"
                self.db.add_log(
                    level="WARN",
                    trading_mode=trading_mode,
                    message=f"取得 Polymarket 運動盤口失敗 ({data_mode}): {exc}",
                )
                markets = self.latest_markets

            # 1. Update open positions mark-to-market & evaluate TP/SL
            await self._update_and_check_open_positions(markets, cfg)

            # 2. Evaluate each market against the 70%+ rule & risk controls
            annotated_markets = []
            qualified_candidates = []

            for m in markets:
                paper_eval = self._evaluate_market(m, cfg, trading_mode="PAPER")
                live_eval = self._evaluate_market(m, cfg, trading_mode="LIVE")
                eval_info = live_eval if trading_mode == "LIVE" else paper_eval
                m_copy = dict(m)
                m_copy["paper_rule_evaluation"] = paper_eval
                m_copy["live_rule_evaluation"] = live_eval
                m_copy["rule_evaluation"] = eval_info
                annotated_markets.append(m_copy)
                if eval_info["eligible"]:
                    qualified_candidates.append((m_copy, eval_info))

            # Sort markets strictly from soonest ending to latest ending (由最快結束到最遲結束排序)
            annotated_markets.sort(
                key=lambda x: (
                    parse_end_date_ts(x.get("end_date", "")),
                    -x["top_outcome"]["prob"],
                )
            )
            self.latest_markets = annotated_markets

            # 3. If bot is enabled, prioritize betting on the soonest-ending events first (最接近結束的賽事優先投注)
            new_bets = []
            if cfg.get("bot_enabled", True) and qualified_candidates:
                qualified_candidates.sort(
                    key=lambda pair: (
                        parse_end_date_ts(pair[0].get("end_date", "")),
                        -pair[0]["top_outcome"]["prob"],
                    )
                )
                for m_cand, eval_info in qualified_candidates:
                    placed = await self._attempt_auto_bet(m_cand, eval_info, cfg)
                    if placed:
                        new_bets.append(placed)
                        # Refresh config & evaluation badge after bet
                        cfg = self.db.get_config()
                        m_cand["paper_rule_evaluation"] = self._evaluate_market(m_cand, cfg, trading_mode="PAPER")
                        m_cand["live_rule_evaluation"] = self._evaluate_market(m_cand, cfg, trading_mode="LIVE")
                        m_cand["rule_evaluation"] = (
                            m_cand["live_rule_evaluation"]
                            if cfg.get("trading_mode", "PAPER") == "LIVE"
                            else m_cand["paper_rule_evaluation"]
                        )

            # 4. Also scan and evaluate Hong Kong Daily Temperature 70%+ Auto-Trading
            try:
                await self.hk_temp.scan_and_evaluate()
            except Exception as exc:
                self.db.add_log(
                    level="WARN",
                    trading_mode=trading_mode,
                    message=f"香港氣溫市場掃描異常: {exc}",
                )

            # 5. Scan and evaluate Passive Market Making & Liquidity Rewards ($500 LP Engine)
            try:
                await self.lp_mm.scan_and_evaluate()
            except Exception as exc:
                self.db.add_log(
                    level="WARN",
                    trading_mode=trading_mode,
                    message=f"被動做市引擎掃描異常: {exc}",
                )

            # 6. Check HKJC Self-Evolving Quant Engine auto-progression loop
            try:
                await self.hkjc.maybe_auto_progress()
            except Exception as exc:
                self.db.add_log(
                    level="WARN",
                    trading_mode=trading_mode,
                    message=f"HKJC 賽馬自進化引擎更新異常: {exc}",
                )

            self.scan_count += 1
            self.last_scan_at = utc_now_iso()
            self.last_scan_duration_ms = int((time.perf_counter() - t0) * 1000)
            self.is_scanning = False

        state = self.get_full_state()
        if self._ws_broadcast_cb:
            await self._ws_broadcast_cb({"type": "STATE_UPDATE", "data": state})
        return state

    def _evaluate_market(
        self,
        market: Dict[str, Any],
        cfg: Dict[str, Any],
        trading_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode_to_check = str(trading_mode or cfg.get("trading_mode", "PAPER")).upper()
        min_prob = float(cfg.get("min_win_prob", 0.70))
        max_prob = float(cfg.get("max_win_prob", 0.95))
        min_liq = float(cfg.get("min_liquidity_usdc", 1500.0))
        min_vol = float(cfg.get("min_volume_usdc", 5000.0))
        max_spread = float(cfg.get("max_spread", 0.04))
        prevent_dup = bool(cfg.get("prevent_duplicate_market", True))
        exclude_upset = bool(cfg.get("exclude_high_upset_sports", True))
        allowed_sports_raw = str(cfg.get("allowed_sports", "ALL")).strip().upper()
        allowed_sports = (
            None
            if allowed_sports_raw in ("", "ALL", "*")
            else {s.strip() for s in allowed_sports_raw.split(",") if s.strip()}
        )

        top = market["top_outcome"]
        prob = float(top["prob"])
        liq = float(market.get("liquidity_usdc", 0.0))
        vol = float(market.get("volume_usdc", 0.0))
        spread = float(market.get("spread", 0.0))
        sport = market.get("sport", "OTHER_SPORTS")

        # Check duplicate first (scoped by trading_mode so PAPER trial runs don't block LIVE bets)
        already_bet = self.db.has_bet_on_market(
            market["market_id"],
            market.get("condition_id", ""),
            market.get("market_slug", ""),
            market.get("event_title", ""),
            trading_mode=mode_to_check,
        )
        if prevent_dup and already_bet:
            return {
                "eligible": False,
                "already_bet": True,
                "status_code": "ALREADY_BET",
                "status_label": "✅ 同項目已投注鎖定",
                "reason": "同一個運動項目只進行一次投注（已完成該賽事下單）",
                "target_outcome": top,
            }

        if allowed_sports and sport not in allowed_sports:
            return {
                "eligible": False,
                "already_bet": already_bet,
                "status_code": "SPORT_FILTERED",
                "status_label": "🚫 非目標運動分類",
                "reason": f"運動類別 {sport} 不在目前篩選清單中",
                "target_outcome": top,
            }

        if exclude_upset:
            title_low = f"{market.get('event_title', '')} {market.get('market_slug', '')}".lower()
            is_high_upset = sport in ("TENNIS", "ESPORTS") or any(
                kw in title_low
                for kw in (
                    "challenger",
                    "playford",
                    "yokohama",
                    "kobe:",
                    "sydney:",
                    "drummondville",
                    "counter-strike",
                    "valorant",
                    "dota",
                    "itf",
                )
            )
            if is_high_upset:
                return {
                    "eligible": False,
                    "already_bet": already_bet,
                    "status_code": "HIGH_UPSET_FILTERED",
                    "status_label": "🛡️ 高爆冷賽事過濾",
                    "reason": "已啟用 +EV 防爆冷過濾（自動跳過網球挑戰賽 / 電競等易逆轉盤口）",
                    "target_outcome": top,
                }

        if prob < min_prob:
            gap_pct = round((min_prob - prob) * 100, 1)
            return {
                "eligible": False,
                "already_bet": already_bet,
                "status_code": "PROB_TOO_LOW",
                "status_label": f"⏳ 勝率未達 {int(min_prob * 100)}% (差 {gap_pct}%)",
                "reason": f"最高選項「{top['label']}」勝率 {prob * 100:.1f}% < 門檻 {min_prob * 100:.1f}%",
                "target_outcome": top,
            }

        if prob > max_prob:
            return {
                "eligible": False,
                "already_bet": already_bet,
                "status_code": "PROB_TOO_HIGH",
                "status_label": f"⚠️ 超過上限 {int(max_prob * 100)}%",
                "reason": f"勝率 {prob * 100:.1f}% 超過風控上限 {max_prob * 100:.1f}%（過高賠率無利潤空間）",
                "target_outcome": top,
            }

        if liq < min_liq:
            return {
                "eligible": False,
                "already_bet": already_bet,
                "status_code": "LOW_LIQUIDITY",
                "status_label": "💧 流動性不足",
                "reason": f"流動性 ${liq:,.0f} < 最低門檻 ${min_liq:,.0f}",
                "target_outcome": top,
            }

        if vol < min_vol:
            return {
                "eligible": False,
                "already_bet": already_bet,
                "status_code": "LOW_VOLUME",
                "status_label": "📊 成交量不足",
                "reason": f"總成交量 ${vol:,.0f} < 最低門檻 ${min_vol:,.0f}",
                "target_outcome": top,
            }

        if spread > max_spread:
            return {
                "eligible": False,
                "already_bet": already_bet,
                "status_code": "WIDE_SPREAD",
                "status_label": "📏 買賣點差過大",
                "reason": f"盤口點差 {spread * 100:.1f}% > 上限 {max_spread * 100:.1f}%",
                "target_outcome": top,
            }

        return {
            "eligible": True,
            "already_bet": already_bet,
            "status_code": "QUALIFIED",
            "status_label": f"🔥 達標立即投注 ({prob * 100:.1f}%)",
            "reason": (
                f"勝率 {prob * 100:.1f}% ∈ [{min_prob * 100:.0f}%, {max_prob * 100:.0f}%] | "
                f"流動性 ${liq:,.0f} | 點差 {spread * 100:.1f}%"
            ),
            "target_outcome": top,
        }

    @staticmethod
    def _minutes_until_end(end_date_str: str) -> Optional[float]:
        if not end_date_str:
            return None
        try:
            clean = str(end_date_str).strip()
            if clean.endswith("Z"):
                clean = clean[:-1] + "+00:00"
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt - datetime.now(timezone.utc)).total_seconds() / 60.0
        except Exception:
            return None

    async def _update_and_check_open_positions(
        self, markets: List[Dict[str, Any]], cfg: Dict[str, Any]
    ) -> None:
        open_positions = self.db.get_positions(status="OPEN", limit=200)
        if not open_positions:
            return

        market_map: Dict[str, Dict[str, Any]] = {str(m["market_id"]): m for m in markets}

        # Fetch any open positions' markets that are no longer in the active open list (e.g., finished/closed games)
        missing_ids = [
            str(pos["market_id"])
            for pos in open_positions
            if str(pos["market_id"]) not in market_map and not str(pos["market_id"]).startswith("sim-")
        ]
        if missing_ids:
            try:
                extra_markets = await self.poly_client.fetch_markets_by_ids(missing_ids)
                market_map.update(extra_markets)
            except Exception:
                pass

        tp_enabled = bool(cfg.get("auto_take_profit_enabled", True))
        tp_prob = float(cfg.get("take_profit_prob", 0.96))
        sl_enabled = bool(cfg.get("auto_stop_loss_enabled", True))
        sl_prob = float(cfg.get("stop_loss_prob", 0.52))
        sl_min_before_end = int(cfg.get("stop_loss_minutes_before_end", 20))

        for pos in open_positions:
            m = market_map.get(str(pos["market_id"]))
            if not m:
                continue

            # Find matching outcome probability (prefer exact label match, fallback to outcome_index)
            idx = int(pos.get("outcome_index", 0))
            target_lbl = str(pos.get("outcome_label", "")).strip().lower()
            outcomes = m.get("outcomes", [])
            matched_prob = None
            for o in outcomes:
                o_lbl = str(o.get("label", "")).strip().lower()
                o_raw = str(o.get("raw_label", "")).strip().lower()
                if o_lbl == target_lbl or (o_raw and o_raw == target_lbl):
                    matched_prob = float(o["prob"])
                    break
            if matched_prob is None:
                for o in outcomes:
                    if int(o.get("index", -1)) == idx:
                        matched_prob = float(o["prob"])
                        break
            if matched_prob is None:
                continue

            is_closed_on_poly = bool(m.get("closed"))
            effective_end_date = str(m.get("end_date") or pos.get("end_date") or "")
            self.db.update_open_position_mark(pos["id"], matched_prob, end_date=effective_end_date)

            # 1. Check Official Match Result / Settlement First (Closed on Polymarket or converged to 100c / 0c)
            if (is_closed_on_poly and matched_prob >= 0.95) or matched_prob >= 0.995:
                closed = self.db.close_position(pos["id"], 1.0, close_status="SETTLED_WIN")
                if closed:
                    pnl = float(closed["realized_pnl_usdc"])
                    self.db.add_log(
                        level="EXIT",
                        trading_mode=closed["trading_mode"],
                        sport=closed["sport"],
                        market_id=closed["market_id"],
                        event_title=closed["event_title"],
                        outcome_label=closed["outcome_label"],
                        prob=1.0,
                        amount_usdc=float(closed["amount_usdc"]),
                        message=(
                            f"🏆 賽事結果出爐自動結算 (勝出 100¢)：{closed['event_title']} [{closed['outcome_label']}] "
                            f"| 實現獲利 +${pnl:,.2f} USDC"
                        ),
                    )
                continue

            if (is_closed_on_poly and matched_prob <= 0.05) or matched_prob <= 0.01:
                closed = self.db.close_position(pos["id"], 0.0, close_status="SETTLED_LOSS")
                if closed:
                    pnl = float(closed["realized_pnl_usdc"])
                    self.db.add_log(
                        level="EXIT",
                        trading_mode=closed["trading_mode"],
                        sport=closed["sport"],
                        market_id=closed["market_id"],
                        event_title=closed["event_title"],
                        outcome_label=closed["outcome_label"],
                        prob=0.0,
                        amount_usdc=float(closed["amount_usdc"]),
                        message=(
                            f"🏳️ 賽事結果出爐自動結算 (落敗 0¢)：{closed['event_title']} [{closed['outcome_label']}] "
                            f"| 實現損益 ${pnl:,.2f} USDC"
                        ),
                    )
                continue

            # 2. Check Auto Take-Profit (In-Play: must be >= tp_prob AND strictly profitable vs entry_prob)
            if tp_enabled and matched_prob >= tp_prob and matched_prob > float(pos.get("entry_prob", 0.70)) + 0.01:
                closed = self.db.close_position(pos["id"], matched_prob, close_status="CLOSED_TP")
                if closed:
                    pnl = float(closed["realized_pnl_usdc"])
                    self.db.add_log(
                        level="EXIT",
                        trading_mode=closed["trading_mode"],
                        sport=closed["sport"],
                        market_id=closed["market_id"],
                        event_title=closed["event_title"],
                        outcome_label=closed["outcome_label"],
                        prob=matched_prob,
                        amount_usdc=float(closed["amount_usdc"]),
                        message=(
                            f"🎯 自動停利平倉：{closed['event_title']} [{closed['outcome_label']}] "
                            f"勝率升至 {matched_prob * 100:.1f}% (>= {tp_prob * 100:.0f}%)，實現損益 +${pnl:,.2f} USDC"
                        ),
                    )
            # 3. Check Auto Stop-Loss (with near-end time window prerequisite)
            elif sl_enabled and matched_prob <= sl_prob:
                rem_min = self._minutes_until_end(effective_end_date)
                if sl_min_before_end <= 0:
                    time_window_ok = True
                else:
                    time_window_ok = rem_min is not None and rem_min <= float(sl_min_before_end)

                if time_window_ok:
                    closed = self.db.close_position(pos["id"], matched_prob, close_status="CLOSED_SL")
                    if closed:
                        pnl = float(closed["realized_pnl_usdc"])
                        rem_desc = (
                            f"賽事剩餘 {max(0, round(rem_min))} 分鐘 (<= {sl_min_before_end} 分) 且"
                            if sl_min_before_end > 0 and rem_min is not None
                            else ""
                        )
                        self.db.add_log(
                            level="EXIT",
                            trading_mode=closed["trading_mode"],
                            sport=closed["sport"],
                            market_id=closed["market_id"],
                            event_title=closed["event_title"],
                            outcome_label=closed["outcome_label"],
                            prob=matched_prob,
                            amount_usdc=float(closed["amount_usdc"]),
                            message=(
                                f"🛡️ 自動停損平倉：{closed['event_title']} [{closed['outcome_label']}] "
                                f"{rem_desc}勝率跌至 {matched_prob * 100:.1f}% (<= {sl_prob * 100:.0f}%)，實現損益 ${pnl:,.2f} USDC"
                            ),
                        )

    async def _attempt_auto_bet(
        self,
        market: Dict[str, Any],
        eval_info: Dict[str, Any],
        cfg: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        trading_mode = str(cfg.get("trading_mode", "PAPER")).upper()
        # Double-check one-bet-per-event guard scoped to trading_mode right before order execution
        if self.db.has_bet_on_market(
            market["market_id"],
            market.get("condition_id", ""),
            market.get("market_slug", ""),
            market.get("event_title", ""),
            trading_mode=trading_mode,
        ):
            return None

        bet_amount = float(cfg.get("bet_amount_usdc", 25.0))
        daily_budget = float(cfg.get("daily_budget_usdc", 300.0))
        max_open = int(cfg.get("max_open_positions", 15))

        # Check open positions cap for this trading_mode
        open_cnt = self.db.count_open_positions(trading_mode=trading_mode)
        if open_cnt >= max_open:
            return None

        # Check daily budget cap for this trading_mode
        today_spend = self.db.get_today_spend_usdc(trading_mode=trading_mode)
        if today_spend + bet_amount > daily_budget:
            return None

        outcome = eval_info["target_outcome"]
        return await self._execute_order(
            market=market,
            outcome=outcome,
            amount_usdc=bet_amount,
            trading_mode=trading_mode,
            trigger_type="AUTO_70_RULE",
            trigger_reason=eval_info["reason"],
        )

    async def execute_manual_bet(
        self,
        market_id: str,
        outcome_index: int = -1,
        amount_usdc: Optional[float] = None,
        trading_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        cfg = self.db.get_config()
        mode_to_use = str(trading_mode or cfg.get("trading_mode", "PAPER")).upper()
        bet_amount = float(amount_usdc or cfg.get("bet_amount_usdc", 25.0))

        target_market = next((m for m in self.latest_markets if m["market_id"] == market_id), None)
        if not target_market:
            raise ValueError(f"找不到市場 ID: {market_id}")

        if bool(cfg.get("prevent_duplicate_market", True)) and self.db.has_bet_on_market(
            target_market["market_id"],
            target_market.get("condition_id", ""),
            target_market.get("market_slug", ""),
            target_market.get("event_title", ""),
            trading_mode=mode_to_use,
        ):
            raise ValueError(f"此運動項目在 {mode_to_use} 模式已進行過投注（同一個項目只進行一次投注）")

        if outcome_index >= 0 and outcome_index < len(target_market["outcomes"]):
            outcome = target_market["outcomes"][outcome_index]
        else:
            outcome = target_market["top_outcome"]

        pos = await self._execute_order(
            market=target_market,
            outcome=outcome,
            amount_usdc=bet_amount,
            trading_mode=mode_to_use,
            trigger_type="MANUAL",
            trigger_reason=f"手動點擊立即投注 (勝率 {outcome['prob'] * 100:.1f}%)",
        )
        if not pos:
            raise RuntimeError("下單未完成，請檢查模擬餘額或 CLOB 憑證設定")

        # Update evaluation badges
        refreshed_cfg = self.db.get_config()
        target_market["paper_rule_evaluation"] = self._evaluate_market(target_market, refreshed_cfg, trading_mode="PAPER")
        target_market["live_rule_evaluation"] = self._evaluate_market(target_market, refreshed_cfg, trading_mode="LIVE")
        target_market["rule_evaluation"] = (
            target_market["live_rule_evaluation"]
            if refreshed_cfg.get("trading_mode", "PAPER") == "LIVE"
            else target_market["paper_rule_evaluation"]
        )
        if self._ws_broadcast_cb:
            await self._ws_broadcast_cb({"type": "STATE_UPDATE", "data": self.get_full_state()})
        return pos

    async def _execute_order(
        self,
        market: Dict[str, Any],
        outcome: Dict[str, Any],
        amount_usdc: float,
        trading_mode: str,
        trigger_type: str,
        trigger_reason: str,
    ) -> Optional[Dict[str, Any]]:
        cfg = self.db.get_config()
        exec_price = float(outcome.get("best_ask") or outcome["prob"])
        exec_price = max(0.01, min(0.99, exec_price))
        shares = round(amount_usdc / exec_price, 2)
        order_id = ""

        if trading_mode == "LIVE":
            creds = self.db.get_clob_credentials()
            try:
                live_res = await self.poly_client.place_live_clob_order(
                    creds=creds,
                    token_id=str(outcome.get("token_id", "")),
                    price=exec_price,
                    amount_usdc=amount_usdc,
                )
                order_id = live_res["order_id"]
                exec_price = float(live_res["executed_price"])
                shares = float(live_res["shares"])
            except Exception as exc:
                self.db.add_log(
                    level="ERROR",
                    trading_mode="LIVE",
                    sport=market["sport"],
                    market_id=market["market_id"],
                    event_title=market["event_title"],
                    outcome_label=outcome["label"],
                    prob=outcome["prob"],
                    amount_usdc=amount_usdc,
                    message=f"❌ LIVE CLOB 下單失敗 [{market['event_title']} - {outcome['label']}]: {exc}",
                )
                return None
        else:
            # PAPER mode: check virtual cash balance
            cash = float(cfg.get("paper_cash_balance", 1000.0))
            if cash < amount_usdc:
                self.db.add_log(
                    level="WARN",
                    trading_mode="PAPER",
                    sport=market["sport"],
                    market_id=market["market_id"],
                    event_title=market["event_title"],
                    outcome_label=outcome["label"],
                    prob=outcome["prob"],
                    amount_usdc=amount_usdc,
                    message=f"⚠️ 模擬錢包餘額不足 (${cash:,.2f} < ${amount_usdc:,.2f})，跳過下單",
                )
                return None
            new_cash = round(cash - amount_usdc, 2)
            self.db.update_config({"paper_cash_balance": new_cash})
            order_id = f"paper-{int(time.time() * 1000)}"

        position = self.db.create_position(
            {
                "market_id": market["market_id"],
                "condition_id": market.get("condition_id", ""),
                "token_id": outcome.get("token_id", ""),
                "sport": market["sport"],
                "event_title": market["event_title"],
                "question": market["question"],
                "market_slug": market.get("market_slug", ""),
                "outcome_label": outcome["label"],
                "outcome_index": outcome.get("index", 0),
                "trading_mode": trading_mode,
                "trigger_type": trigger_type,
                "entry_prob": round(exec_price, 4),
                "current_prob": round(float(outcome["prob"]), 4),
                "shares": shares,
                "amount_usdc": amount_usdc,
                "order_id": order_id,
                "trigger_reason": trigger_reason,
                "end_date": market.get("end_date", ""),
            }
        )

        potential_payout = round(shares * 1.0, 2)
        potential_profit = round(potential_payout - amount_usdc, 2)
        mode_tag = "【真實 CLOB】" if trading_mode == "LIVE" else "【模擬交易】"
        trig_tag = "⚡ 70%+ 自動觸發" if trigger_type == "AUTO_70_RULE" else "👆 手動立即投注"

        self.db.add_log(
            level="EXEC",
            trading_mode=trading_mode,
            sport=market["sport"],
            market_id=market["market_id"],
            event_title=market["event_title"],
            outcome_label=outcome["label"],
            prob=exec_price,
            amount_usdc=amount_usdc,
            message=(
                f"{mode_tag} {trig_tag} 買入「{outcome['label']}」@ {exec_price * 100:.1f}% "
                f"| 投入 ${amount_usdc:,.2f} USDC ({shares:.2f} 股，勝出預計獲利 +${potential_profit:,.2f})"
            ),
            details={
                "order_id": order_id,
                "shares": shares,
                "potential_payout": potential_payout,
                "trigger_reason": trigger_reason,
            },
        )
        return position

    def get_full_state(self) -> Dict[str, Any]:
        cfg = self.db.get_config()
        paper_portfolio = self.db.get_portfolio_summary(trading_mode="PAPER")
        live_portfolio = self.db.get_portfolio_summary(trading_mode="LIVE")

        paper_open = self.db.get_positions(status="OPEN", limit=50, trading_mode="PAPER")
        paper_closed = [
            p for p in self.db.get_positions(limit=60, trading_mode="PAPER") if p["status"] != "OPEN"
        ][:30]
        paper_logs = self.db.get_logs(limit=60, trading_mode="PAPER")

        live_open = self.db.get_positions(status="OPEN", limit=50, trading_mode="LIVE")
        live_closed = [
            p for p in self.db.get_positions(limit=60, trading_mode="LIVE") if p["status"] != "OPEN"
        ][:30]
        live_logs = self.db.get_logs(limit=60, trading_mode="LIVE")

        clob_status = self.db.get_masked_clob_credentials()

        min_prob = float(cfg.get("min_win_prob", 0.70))
        above_70_count = sum(
            1 for m in self.latest_markets if float(m["top_outcome"]["prob"]) >= min_prob
        )
        qualified_now_count = sum(
            1 for m in self.latest_markets if m.get("rule_evaluation", {}).get("eligible")
        )

        return {
            "server_time": utc_now_iso(),
            "engine": {
                "bot_enabled": cfg.get("bot_enabled", True),
                "trading_mode": cfg.get("trading_mode", "PAPER"),
                "data_source_mode": cfg.get("data_source_mode", "AUTO"),
                "active_data_source": self.active_data_source,
                "last_api_error": self.poly_client.last_api_error,
                "scan_count": self.scan_count,
                "last_scan_at": self.last_scan_at,
                "last_scan_duration_ms": self.last_scan_duration_ms,
                "total_sports_markets": len(self.latest_markets),
                "above_threshold_count": above_70_count,
                "qualified_now_count": qualified_now_count,
            },
            "config": cfg,
            "clob_status": clob_status,
            "portfolio": paper_portfolio,
            "markets": self.latest_markets[:150],
            "open_positions": paper_open,
            "closed_positions": paper_closed,
            "logs": paper_logs,
            "paper_data": {
                "portfolio": paper_portfolio,
                "open_positions": paper_open,
                "closed_positions": paper_closed,
                "logs": paper_logs,
            },
            "live_data": {
                "portfolio": live_portfolio,
                "open_positions": live_open,
                "closed_positions": live_closed,
                "logs": live_logs,
            },
            "hk_temp": self.hk_temp.last_dashboard_data,
            "lp_mm": self.lp_mm.last_dashboard_data,
            "hkjc": self.hkjc.get_state(),
        }

    def sanitize_state_for_client(self, state: Dict[str, Any], is_admin: bool) -> Dict[str, Any]:
        out = dict(state)
        out["auth"] = {"is_admin": bool(is_admin)}
        if is_admin:
            return out

        # Unauthenticated public visitor: strictly strip ALL LIVE trading data & credentials
        out["live_data"] = None
        cfg_public = dict(state.get("config") or {})
        cfg_public["trading_mode"] = "PAPER"
        out["config"] = cfg_public

        eng_public = dict(state.get("engine") or {})
        eng_public["trading_mode"] = "PAPER"
        out["engine"] = eng_public

        out["clob_status"] = {
            "has_private_key": False,
            "private_key_masked": "",
            "funder_address": "",
            "has_api_key": False,
            "api_key_masked": "",
            "signature_type": 1,
            "ready_for_live": bool((state.get("clob_status") or {}).get("ready_for_live")),
        }

        # Ensure markets only expose PAPER evaluation
        sanitized_markets = []
        for m in state.get("markets") or []:
            mc = dict(m)
            mc["rule_evaluation"] = mc.get("paper_rule_evaluation") or mc.get("rule_evaluation")
            mc.pop("live_rule_evaluation", None)
            sanitized_markets.append(mc)
        out["markets"] = sanitized_markets

        # Strip LIVE trades/summary from hk_temp
        if state.get("hk_temp"):
            hk_pub = dict(state["hk_temp"])
            hk_eng = dict(hk_pub.get("engine") or {})
            hk_eng["mode"] = "paper"
            hk_pub["engine"] = hk_eng
            paper_sum = (hk_pub.get("summaryByMode") or {}).get("PAPER") or hk_pub.get("summary") or {}
            paper_tr = (hk_pub.get("tradesByMode") or {}).get("PAPER") or hk_pub.get("trades") or []
            hk_pub["summary"] = paper_sum
            hk_pub["trades"] = paper_tr
            hk_pub["summaryByMode"] = {"PAPER": paper_sum}
            hk_pub["tradesByMode"] = {"PAPER": paper_tr}
            out["hk_temp"] = hk_pub

        # Strip LIVE LP quotes/summary from lp_mm
        if state.get("lp_mm"):
            lp_pub = dict(state["lp_mm"])
            lp_cfg = dict(lp_pub.get("config") or {})
            lp_cfg["mode"] = "PAPER"
            lp_pub["config"] = lp_cfg
            lp_pub["live"] = None
            san_lp_markets = []
            for m in lp_pub.get("markets") or []:
                mc = dict(m)
                mc["eval"] = mc.get("paper_eval") or mc.get("eval")
                mc.pop("live_eval", None)
                san_lp_markets.append(mc)
            lp_pub["markets"] = san_lp_markets
            out["lp_mm"] = lp_pub

        return out
