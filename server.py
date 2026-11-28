import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Set

from aiohttp import WSMsgType, web

from app.config import BASE_DIR
from app.database import Database
from app.polymarket_client import PolymarketClient
from app.trading_engine import TradingEngine

STATIC_DIR = BASE_DIR / "static"
ADMIN_COOKIE_NAME = "poly_admin_token"


class PolySportsServer:
    def __init__(self) -> None:
        self.db = Database()
        self.poly_client = PolymarketClient()
        self.engine = TradingEngine(self.db, self.poly_client)
        self.ws_clients: Set[web.WebSocketResponse] = set()
        self.ws_tokens: Dict[web.WebSocketResponse, Optional[str]] = {}
        self.engine.set_broadcast_callback(self.broadcast_ws)

    def _extract_token(self, request: web.Request) -> Optional[str]:
        return (
            request.headers.get("X-Admin-Token")
            or request.cookies.get(ADMIN_COOKIE_NAME)
            or request.query.get("token")
        )

    def _is_admin_request(self, request: web.Request) -> bool:
        token = self._extract_token(request)
        return self.db.verify_admin_token(token)

    def _state_for_request(self, request: web.Request, raw_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        st = raw_state if raw_state is not None else self.engine.get_full_state()
        return self.engine.sanitize_state_for_client(st, is_admin=self._is_admin_request(request))

    async def broadcast_ws(self, payload: Dict[str, Any]) -> None:
        if not self.ws_clients:
            return
        raw_state = payload.get("data") if payload.get("type") == "STATE_UPDATE" else None
        admin_msg = None
        public_msg = None

        stale = []
        for ws in list(self.ws_clients):
            if ws.closed:
                stale.append(ws)
                continue
            try:
                if raw_state is not None:
                    is_admin = self.db.verify_admin_token(self.ws_tokens.get(ws))
                    if is_admin:
                        if admin_msg is None:
                            admin_state = self.engine.sanitize_state_for_client(raw_state, is_admin=True)
                            admin_msg = json.dumps({"type": "STATE_UPDATE", "data": admin_state}, ensure_ascii=False)
                        await ws.send_str(admin_msg)
                    else:
                        if public_msg is None:
                            pub_state = self.engine.sanitize_state_for_client(raw_state, is_admin=False)
                            public_msg = json.dumps({"type": "STATE_UPDATE", "data": pub_state}, ensure_ascii=False)
                        await ws.send_str(public_msg)
                else:
                    await ws.send_str(json.dumps(payload, ensure_ascii=False))
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.ws_clients.discard(ws)
            self.ws_tokens.pop(ws, None)

    async def on_startup(self, app: web.Application) -> None:
        await self.engine.start()

    async def on_cleanup(self, app: web.Application) -> None:
        await self.engine.stop()
        for ws in list(self.ws_clients):
            await ws.close()
        self.ws_clients.clear()
        self.ws_tokens.clear()

    # --- Auth Handlers (Protecting LIVE Trading & Credentials) ---
    async def handle_auth_login(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            password = str(body.get("password", "")).strip()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        if not self.db.verify_admin_password(password):
            return web.json_response(
                {"ok": False, "error": "❌ 實盤管理員密碼不正確，請重新輸入"},
                status=401,
            )

        token = self.db.create_admin_token()
        state = self.engine.sanitize_state_for_client(self.engine.get_full_state(), is_admin=True)
        resp = web.json_response({"ok": True, "token": token, "state": state})
        resp.set_cookie(
            ADMIN_COOKIE_NAME,
            token,
            max_age=86400 * 30,
            httponly=True,
            samesite="Lax",
            path="/",
        )
        return resp

    async def handle_auth_logout(self, request: web.Request) -> web.Response:
        state = self.engine.sanitize_state_for_client(self.engine.get_full_state(), is_admin=False)
        resp = web.json_response({"ok": True, "state": state})
        resp.del_cookie(ADMIN_COOKIE_NAME, path="/")
        return resp

    async def handle_auth_change_password(self, request: web.Request) -> web.Response:
        if not self._is_admin_request(request):
            return web.json_response(
                {"ok": False, "auth_required": True, "error": "🔒 請先登入實盤管理員密碼"},
                status=401,
            )
        try:
            body = await request.json()
            curr_pw = str(body.get("current_password", "")).strip()
            new_pw = str(body.get("new_password", "")).strip()
            ok = self.db.update_admin_password(curr_pw, new_pw)
            if not ok:
                return web.json_response({"ok": False, "error": "目前密碼不正確"}, status=400)
            new_token = self.db.create_admin_token()
            state = self.engine.sanitize_state_for_client(self.engine.get_full_state(), is_admin=True)
            resp = web.json_response({"ok": True, "token": new_token, "state": state})
            resp.set_cookie(
                ADMIN_COOKIE_NAME,
                new_token,
                max_age=86400 * 30,
                httponly=True,
                samesite="Lax",
                path="/",
            )
            return resp
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    # --- Core Handlers ---
    async def handle_index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "index.html")

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "service": "PolySports-70-AutoTrader",
                "bot_enabled": self.db.get_config().get("bot_enabled", True),
                "scan_count": self.engine.scan_count,
                "active_data_source": self.engine.active_data_source,
            }
        )

    async def handle_get_state(self, request: web.Request) -> web.Response:
        return web.json_response(self._state_for_request(request))

    async def handle_update_config(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

        is_admin = self._is_admin_request(request)
        old_cfg = self.db.get_config()

        # Require admin login before switching to LIVE mode or modifying config while in LIVE mode
        if str(body.get("trading_mode", "")).upper() == "LIVE":
            if not is_admin:
                return web.json_response(
                    {
                        "ok": False,
                        "auth_required": True,
                        "error": "🔒 切換至「實盤 (LIVE)」需要管理員密碼，請先輸入密碼解鎖實盤專區。",
                    },
                    status=401,
                )
            clob_status = self.db.get_masked_clob_credentials()
            if not clob_status.get("ready_for_live"):
                return web.json_response(
                    {
                        "ok": False,
                        "error": "切換至「真實下單 (LIVE)」前，請先在右上角「CLOB 錢包與 API 設定」填入 Polygon 錢包私鑰。",
                    },
                    status=400,
                )

        updated = self.db.update_config(body)

        # Log significant state changes
        if "bot_enabled" in body and bool(body["bot_enabled"]) != bool(old_cfg.get("bot_enabled")):
            state_str = "已啟動 (ACTIVE)" if updated["bot_enabled"] else "已暫停 (PAUSED)"
            self.db.add_log(
                level="INFO",
                trading_mode=updated["trading_mode"],
                message=f"🤖 自動交易機器人狀態變更：{state_str}",
            )
        elif "trading_mode" in body and body["trading_mode"] != old_cfg.get("trading_mode"):
            self.db.add_log(
                level="INFO",
                trading_mode=updated["trading_mode"],
                message=f"🔄 交易執行模式已切換為：{updated['trading_mode']}",
            )
        else:
            min_p = int(updated["min_win_prob"] * 100)
            max_p = int(updated["max_win_prob"] * 100)
            self.db.add_log(
                level="INFO",
                trading_mode=updated["trading_mode"],
                message=(
                    f"⚙️ 策略與風控規則已更新：勝率區間 [{min_p}% ~ {max_p}%] | "
                    f"單筆 ${updated['bet_amount_usdc']:.0f} USDC | 每日上限 ${updated['daily_budget_usdc']:.0f}"
                ),
            )

        raw_state = await self.engine.run_single_scan()
        return web.json_response({"ok": True, "config": updated, "state": self._state_for_request(request, raw_state)})

    async def handle_manual_scan(self, request: web.Request) -> web.Response:
        raw_state = await self.engine.run_single_scan()
        return web.json_response({"ok": True, "state": self._state_for_request(request, raw_state)})

    async def handle_simulate_breakout(self, request: web.Request) -> web.Response:
        """Push a market above 70% in simulation feed and run immediate scan."""
        surge = self.poly_client.simulator.trigger_breakout_surge()
        self.db.add_log(
            level="INFO",
            trading_mode="PAPER",
            event_title=surge["event_title"],
            outcome_label=surge["outcome_label"],
            prob=surge["new_prob"],
            message=(
                f"📈 盤口異動突破：{surge['event_title']} — 「{surge['outcome_label']}」"
                f"勝率瞬間拉升至 {surge['new_prob'] * 100:.1f}% (>= 70%)！"
            ),
        )
        raw_state = await self.engine.run_single_scan()
        return web.json_response(
            {"ok": True, "surge": surge, "state": self._state_for_request(request, raw_state)}
        )

    async def handle_manual_bet(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            market_id = str(body["market_id"])
            outcome_index = int(body.get("outcome_index", -1))
            amount_usdc = body.get("amount_usdc")
            req_mode = str(body.get("trading_mode") or "PAPER").upper()
            if req_mode == "LIVE" and not self._is_admin_request(request):
                return web.json_response(
                    {"ok": False, "auth_required": True, "error": "🔒 實盤下單需先登入實盤管理員密碼"},
                    status=401,
                )
            pos = await self.engine.execute_manual_bet(
                market_id=market_id,
                outcome_index=outcome_index,
                amount_usdc=float(amount_usdc) if amount_usdc is not None else None,
                trading_mode=req_mode if self._is_admin_request(request) else "PAPER",
            )
            return web.json_response(
                {"ok": True, "position": pos, "state": self._state_for_request(request)}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_close_position(self, request: web.Request) -> web.Response:
        pos_id = int(request.match_info["id"])
        try:
            body = await request.json()
        except Exception:
            body = {}

        action = body.get("action", "MARKET_CLOSE")  # MARKET_CLOSE, SETTLE_WIN, SETTLE_LOSS
        open_positions = self.db.get_positions(status="OPEN", limit=200)
        target = next((p for p in open_positions if int(p["id"]) == pos_id), None)
        if not target:
            return web.json_response({"ok": False, "error": "找不到該持倉或已平倉"}, status=404)

        if target.get("trading_mode") == "LIVE" and not self._is_admin_request(request):
            return web.json_response(
                {"ok": False, "auth_required": True, "error": "🔒 實盤持倉操作需先登入實盤管理員密碼"},
                status=401,
            )

        if action == "SETTLE_WIN":
            exit_prob = 1.0
            close_status = "SETTLED_WIN"
            action_desc = "🏆 賽事結算 (勝出 100¢)"
        elif action == "SETTLE_LOSS":
            exit_prob = 0.0
            close_status = "SETTLED_LOSS"
            action_desc = "🏳️ 賽事結算 (落敗 0¢)"
        else:
            exit_prob = float(target["current_prob"])
            close_status = "CLOSED_MANUAL"
            action_desc = f"💼 手動市價平倉 (@ {exit_prob * 100:.1f}%)"

        closed = self.db.close_position(pos_id, exit_prob=exit_prob, close_status=close_status)
        if closed:
            pnl = float(closed["realized_pnl_usdc"])
            sign = "+" if pnl >= 0 else ""
            self.db.add_log(
                level="EXIT",
                trading_mode=closed["trading_mode"],
                sport=closed["sport"],
                market_id=closed["market_id"],
                event_title=closed["event_title"],
                outcome_label=closed["outcome_label"],
                prob=exit_prob,
                amount_usdc=float(closed["amount_usdc"]),
                message=(
                    f"{action_desc}：{closed['event_title']} [{closed['outcome_label']}] "
                    f"| 實現損益 {sign}${pnl:,.2f} USDC"
                ),
            )

        raw_state = self.engine.get_full_state()
        await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
        return web.json_response(
            {"ok": True, "closed": closed, "state": self._state_for_request(request, raw_state)}
        )

    async def handle_reset_portfolio(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            initial = float(body.get("initial_balance", 1000.0))
        except Exception:
            initial = 1000.0
        self.db.reset_paper_portfolio(initial_balance=initial)
        raw_state = await self.engine.run_single_scan()
        return web.json_response({"ok": True, "state": self._state_for_request(request, raw_state)})

    async def handle_save_clob_credentials(self, request: web.Request) -> web.Response:
        if not self._is_admin_request(request):
            return web.json_response(
                {"ok": False, "auth_required": True, "error": "🔒 設定 CLOB 私鑰需先登入實盤管理員密碼"},
                status=401,
            )
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        masked = self.db.update_clob_credentials(body)
        self.db.add_log(
            level="INFO",
            trading_mode="LIVE",
            message="🔐 Polymarket CLOB API 與 Polygon 錢包憑證已更新",
        )
        raw_state = self.engine.get_full_state()
        await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
        return web.json_response(
            {"ok": True, "clob_status": masked, "state": self._state_for_request(request, raw_state)}
        )

    async def handle_verify_clob(self, request: web.Request) -> web.Response:
        if not self._is_admin_request(request):
            return web.json_response(
                {"ok": False, "auth_required": True, "message": "🔒 驗證 CLOB 憑證需先登入實盤管理員密碼"},
                status=401,
            )
        creds = self.db.get_clob_credentials()
        result = await self.poly_client.verify_clob_credentials(creds)
        return web.json_response(result)

    # --- Hong Kong Daily Temperature 70%+ Auto-Trading Handlers ---
    async def handle_hk_temp_state(self, request: web.Request) -> web.Response:
        await self.engine.hk_temp.scan_and_evaluate()
        st = self._state_for_request(request)
        return web.json_response({"ok": True, "hk_temp": st.get("hk_temp")})

    async def handle_hk_temp_config(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        if str(body.get("mode", "")).upper() == "LIVE":
            if not self._is_admin_request(request):
                return web.json_response(
                    {
                        "ok": False,
                        "auth_required": True,
                        "error": "🔒 切換至「實盤 (LIVE)」需要管理員密碼，請先輸入密碼解鎖實盤專區。",
                    },
                    status=401,
                )
            clob_status = self.db.get_masked_clob_credentials()
            if not clob_status.get("ready_for_live"):
                return web.json_response(
                    {
                        "ok": False,
                        "error": "切換至「實盤 (LIVE)」前，請先在右上角「CLOB 錢包設定」填入 Polygon 私鑰。",
                    },
                    status=400,
                )

        cfg = self.engine.hk_temp.update_config(body)
        await self.engine.hk_temp.scan_and_evaluate()
        raw_state = self.engine.get_full_state()
        await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
        return web.json_response(
            {"ok": True, "config": cfg, "state": self._state_for_request(request, raw_state)}
        )

    async def handle_hk_temp_bet(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            outcome_name = str(body["outcome_name"])
            stake = body.get("stake_usdc")
            req_mode = str(body.get("mode") or "PAPER").upper()
            if req_mode == "LIVE" and not self._is_admin_request(request):
                return web.json_response(
                    {"ok": False, "auth_required": True, "error": "🔒 實盤下單需先登入實盤管理員密碼"},
                    status=401,
                )
            trade = await self.engine.hk_temp.execute_manual_hk_bet(
                outcome_name=outcome_name,
                stake_usdc=float(stake) if stake is not None else None,
                mode=req_mode if self._is_admin_request(request) else "PAPER",
            )
            await self.engine.hk_temp.scan_and_evaluate()
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "trade": trade, "state": self._state_for_request(request, raw_state)}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    # --- Passive Market Making & Liquidity Rewards ($500 LP Engine) Handlers ---
    async def handle_lp_config(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        if str(body.get("mode", "")).upper() == "LIVE":
            if not self._is_admin_request(request):
                return web.json_response(
                    {"ok": False, "auth_required": True, "error": "🔒 切換至「實盤做市 (LIVE)」需要管理員密碼"},
                    status=401,
                )
            clob_status = self.db.get_masked_clob_credentials()
            if not clob_status.get("ready_for_live"):
                return web.json_response(
                    {"ok": False, "error": "切換至「實盤做市 (LIVE)」前，請先於「🔐 錢包設定」填入 Polygon 私鑰"},
                    status=400,
                )

        cfg = self.engine.lp_mm.update_config(body)
        await self.engine.lp_mm.scan_and_evaluate()
        raw_state = self.engine.get_full_state()
        await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
        return web.json_response(
            {"ok": True, "config": cfg, "state": self._state_for_request(request, raw_state)}
        )

    async def handle_lp_deploy(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            market_id = str(body["market_id"])
            req_mode = str(body.get("mode") or "PAPER").upper()
            if req_mode == "LIVE" and not self._is_admin_request(request):
                return web.json_response(
                    {"ok": False, "auth_required": True, "error": "🔒 實盤做市需先登入實盤管理員密碼"},
                    status=401,
                )
            slot = await self.engine.lp_mm.deploy_manual_slot(
                market_id=market_id,
                mode=req_mode if self._is_admin_request(request) else "PAPER",
            )
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "slot": slot, "state": self._state_for_request(request, raw_state)}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_lp_cancel(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            slot_id = int(body["slot_id"])
            req_mode = str(body.get("mode") or "PAPER").upper()
            if req_mode == "LIVE" and not self._is_admin_request(request):
                return web.json_response(
                    {"ok": False, "auth_required": True, "error": "🔒 實盤撤單需先登入實盤管理員密碼"},
                    status=401,
                )
            cancelled = await self.engine.lp_mm.cancel_slot(
                slot_id=slot_id,
                mode=req_mode if self._is_admin_request(request) else "PAPER",
            )
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "cancelled": cancelled, "state": self._state_for_request(request, raw_state)}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_lp_simulate_killswitch(self, request: web.Request) -> web.Response:
        try:
            try:
                body = await request.json()
            except Exception:
                body = {}
            req_mode = str(body.get("mode") or "PAPER").upper()
            target_mode = req_mode if (req_mode == "LIVE" and self._is_admin_request(request)) else "PAPER"
            target = await self.engine.lp_mm.simulate_volatility_killswitch(mode=target_mode)
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "target": target, "state": self._state_for_request(request, raw_state)}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_lp_reset(self, request: web.Request) -> web.Response:
        await self.engine.lp_mm.reset_paper_lp()
        raw_state = self.engine.get_full_state()
        await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
        return web.json_response(
            {"ok": True, "state": self._state_for_request(request, raw_state)}
        )

    # --- HKJC Gu Minggao & Bill Benter 2.0 Self-Evolving Quant & Paper Betting Handlers ---
    async def handle_hkjc_state(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "hkjc": self.engine.hkjc.get_state()})

    async def handle_hkjc_config(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": False,
                "error": "🔒 策略與風控參數已由系統鎖定保護，僅由賽後自進化引擎自動優化，禁止人為修改。",
            },
            status=403,
        )

    async def handle_hkjc_bankroll(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            raw_val = str(body.get("equity_hkd") or body.get("initial_bankroll_hkd") or "").replace(",", "").replace("$", "").replace("HK", "").strip()
            target_equity = float(raw_val)
            self.engine.hkjc.update_bankroll_hkd(target_equity)
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "hkjc": raw_state["hkjc"], "state": self._state_for_request(request, raw_state)}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_hkjc_telegram_config(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            tg_cfg = self.engine.hkjc.update_telegram_config(body)
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "telegram": tg_cfg, "hkjc": raw_state["hkjc"]}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_hkjc_telegram_push(self, request: web.Request) -> web.Response:
        try:
            try:
                body = await request.json()
            except Exception:
                body = {}
            race_id = body.get("race_id")
            push_res = await self.engine.hkjc.push_race_strategy_to_telegram(race_id=race_id)
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "push": push_res, "hkjc": raw_state["hkjc"]}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_hkjc_triple_trio_push(self, request: web.Request) -> web.Response:
        try:
            try:
                body = await request.json()
            except Exception:
                body = {}
            pool_id = body.get("pool_id")
            push_res = await self.engine.hkjc.push_triple_trio_to_telegram(pool_id=pool_id)
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "push": push_res, "hkjc": raw_state["hkjc"]}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_hkjc_bet(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            race_id = str(body.get("race_id", ""))
            pool_type = str(body.get("pool_type", "WIN"))
            selection_key = str(body.get("selection_key", ""))
            stake_hkd = float(body.get("stake_hkd", 200.0))
            bet = self.engine.hkjc.place_manual_paper_bet(
                race_id=race_id,
                pool_type=pool_type,
                selection_key=selection_key,
                stake_hkd=stake_hkd,
            )
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "bet": bet, "hkjc": raw_state["hkjc"], "state": self._state_for_request(request, raw_state)}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_hkjc_auto_bet(self, request: web.Request) -> web.Response:
        try:
            try:
                body = await request.json()
            except Exception:
                body = {}
            force_manual = bool(body.get("force_manual", False))
            placed = self.engine.hkjc.auto_place_paper_bets_for_open_races(force_manual=force_manual)
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "placed_count": len(placed), "hkjc": raw_state["hkjc"], "state": self._state_for_request(request, raw_state)}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_hkjc_settle(self, request: web.Request) -> web.Response:
        try:
            try:
                body = await request.json()
            except Exception:
                body = {}
            race_id = body.get("race_id")
            result = self.engine.hkjc.settle_race_and_evolve(race_id=race_id)
            tg_push = await self.engine.hkjc.auto_push_after_settlement(result)
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {
                    "ok": True,
                    "result": result,
                    "telegram_push": tg_push,
                    "hkjc": raw_state["hkjc"],
                    "state": self._state_for_request(request, raw_state),
                }
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_hkjc_settle_all(self, request: web.Request) -> web.Response:
        try:
            results = []
            for r in list(self.engine.hkjc.races):
                has_res = any(ru.get("_actual_final_pos") == 1 for ru in r.get("runners", []))
                if r["status"] != "SETTLED" and has_res:
                    res = self.engine.hkjc.settle_race_and_evolve(race_id=r["race_id"])
                    await self.engine.hkjc.auto_push_after_settlement(res)
                    results.append(res)
            if not results:
                raise ValueError("目前尚無已公佈正式賽果之待結算場次（系統將於完賽後自動結算並進化）。")
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {
                    "ok": True,
                    "settled_count": len(results),
                    "results": results,
                    "hkjc": raw_state["hkjc"],
                    "state": self._state_for_request(request, raw_state),
                }
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_hkjc_reset(self, request: web.Request) -> web.Response:
        try:
            try:
                body = await request.json()
            except Exception:
                body = {}
            reset_weights = bool(body.get("reset_model_weights", False))
            self.engine.hkjc.reset_meeting(reset_model_weights=reset_weights)
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "hkjc": raw_state["hkjc"], "state": self._state_for_request(request, raw_state)}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_hkjc_sync(self, request: web.Request) -> web.Response:
        try:
            await self.engine.hkjc.sync_live_hkjc_data(force_refresh_cards=False)
            raw_state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": raw_state})
            return web.json_response(
                {"ok": True, "hkjc": raw_state["hkjc"], "state": self._state_for_request(request, raw_state)}
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=25.0)
        await ws.prepare(request)
        token = self._extract_token(request)
        self.ws_clients.add(ws)
        self.ws_tokens[ws] = token
        try:
            is_admin = self.db.verify_admin_token(token)
            init_state = self.engine.sanitize_state_for_client(self.engine.get_full_state(), is_admin=is_admin)
            await ws.send_str(json.dumps({"type": "STATE_UPDATE", "data": init_state}, ensure_ascii=False))
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    if msg.data == "ping":
                        await ws.send_str(json.dumps({"type": "PONG"}))
                    else:
                        try:
                            parsed = json.loads(msg.data)
                            if parsed.get("type") == "AUTH":
                                new_tok = parsed.get("token")
                                self.ws_tokens[ws] = new_tok
                                adm = self.db.verify_admin_token(new_tok)
                                st = self.engine.sanitize_state_for_client(self.engine.get_full_state(), is_admin=adm)
                                await ws.send_str(json.dumps({"type": "STATE_UPDATE", "data": st}, ensure_ascii=False))
                        except Exception:
                            pass
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.ws_clients.discard(ws)
            self.ws_tokens.pop(ws, None)
        return ws


@web.middleware
async def no_cache_middleware(request: web.Request, handler):
    resp = await handler(request)
    if isinstance(resp, web.StreamResponse):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


def create_app() -> web.Application:
    srv = PolySportsServer()
    app = web.Application(middlewares=[no_cache_middleware])
    app.on_startup.append(srv.on_startup)
    app.on_cleanup.append(srv.on_cleanup)

    app.router.add_get("/", srv.handle_index)
    app.router.add_get("/hkjc", srv.handle_index)
    app.router.add_get("/api/health", srv.handle_health)
    app.router.add_post("/api/auth/login", srv.handle_auth_login)
    app.router.add_post("/api/auth/logout", srv.handle_auth_logout)
    app.router.add_post("/api/auth/password", srv.handle_auth_change_password)
    app.router.add_get("/api/state", srv.handle_get_state)
    app.router.add_post("/api/config", srv.handle_update_config)
    app.router.add_post("/api/scan", srv.handle_manual_scan)
    app.router.add_post("/api/simulate-breakout", srv.handle_simulate_breakout)
    app.router.add_post("/api/bet", srv.handle_manual_bet)
    app.router.add_post("/api/positions/{id}/close", srv.handle_close_position)
    app.router.add_post("/api/portfolio/reset", srv.handle_reset_portfolio)
    app.router.add_post("/api/clob/credentials", srv.handle_save_clob_credentials)
    app.router.add_post("/api/clob/verify", srv.handle_verify_clob)
    app.router.add_get("/api/hk-temp/state", srv.handle_hk_temp_state)
    app.router.add_post("/api/hk-temp/config", srv.handle_hk_temp_config)
    app.router.add_post("/api/hk-temp/bet", srv.handle_hk_temp_bet)
    app.router.add_post("/api/lp/config", srv.handle_lp_config)
    app.router.add_post("/api/lp/deploy", srv.handle_lp_deploy)
    app.router.add_post("/api/lp/cancel", srv.handle_lp_cancel)
    app.router.add_post("/api/lp/simulate-killswitch", srv.handle_lp_simulate_killswitch)
    app.router.add_post("/api/lp/reset", srv.handle_lp_reset)
    app.router.add_get("/api/hkjc/state", srv.handle_hkjc_state)
    app.router.add_post("/api/hkjc/config", srv.handle_hkjc_config)
    app.router.add_post("/api/hkjc/bankroll", srv.handle_hkjc_bankroll)
    app.router.add_post("/api/hkjc/bet", srv.handle_hkjc_bet)
    app.router.add_post("/api/hkjc/auto-bet", srv.handle_hkjc_auto_bet)
    app.router.add_post("/api/hkjc/settle", srv.handle_hkjc_settle)
    app.router.add_post("/api/hkjc/settle-all", srv.handle_hkjc_settle_all)
    app.router.add_post("/api/hkjc/reset", srv.handle_hkjc_reset)
    app.router.add_post("/api/hkjc/sync", srv.handle_hkjc_sync)
    app.router.add_post("/api/hkjc/telegram/config", srv.handle_hkjc_telegram_config)
    app.router.add_post("/api/hkjc/telegram/push", srv.handle_hkjc_telegram_push)
    app.router.add_post("/api/hkjc/triple-trio/push", srv.handle_hkjc_triple_trio_push)
    app.router.add_get("/ws", srv.handle_ws)
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static")
    return app


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"🚀 PolySports 70%+ Auto-Trading System starting on http://{host}:{port}")
    web.run_app(create_app(), host=host, port=port, print=None)
