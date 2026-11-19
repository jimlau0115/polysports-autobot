import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Set

from aiohttp import WSMsgType, web

from app.config import BASE_DIR
from app.database import Database
from app.polymarket_client import PolymarketClient
from app.trading_engine import TradingEngine

STATIC_DIR = BASE_DIR / "static"


class PolySportsServer:
    def __init__(self) -> None:
        self.db = Database()
        self.poly_client = PolymarketClient()
        self.engine = TradingEngine(self.db, self.poly_client)
        self.ws_clients: Set[web.WebSocketResponse] = set()
        self.engine.set_broadcast_callback(self.broadcast_ws)

    async def broadcast_ws(self, payload: Dict[str, Any]) -> None:
        if not self.ws_clients:
            return
        msg = json.dumps(payload, ensure_ascii=False)
        stale = []
        for ws in list(self.ws_clients):
            if ws.closed:
                stale.append(ws)
                continue
            try:
                await ws.send_str(msg)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.ws_clients.discard(ws)

    async def on_startup(self, app: web.Application) -> None:
        await self.engine.start()

    async def on_cleanup(self, app: web.Application) -> None:
        await self.engine.stop()
        for ws in list(self.ws_clients):
            await ws.close()
        self.ws_clients.clear()

    # --- Handlers ---
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
        return web.json_response(self.engine.get_full_state())

    async def handle_update_config(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

        old_cfg = self.db.get_config()
        # Guard switching to LIVE mode if no private key configured
        if body.get("trading_mode") == "LIVE":
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

        state = await self.engine.run_single_scan()
        return web.json_response({"ok": True, "config": updated, "state": state})

    async def handle_manual_scan(self, request: web.Request) -> web.Response:
        state = await self.engine.run_single_scan()
        return web.json_response({"ok": True, "state": state})

    async def handle_simulate_breakout(self, request: web.Request) -> web.Response:
        """Push a market above 70% in simulation feed and run immediate scan."""
        surge = self.poly_client.simulator.trigger_breakout_surge()
        self.db.add_log(
            level="INFO",
            trading_mode=self.db.get_config().get("trading_mode", "PAPER"),
            event_title=surge["event_title"],
            outcome_label=surge["outcome_label"],
            prob=surge["new_prob"],
            message=(
                f"📈 盤口異動突破：{surge['event_title']} — 「{surge['outcome_label']}」"
                f"勝率瞬間拉升至 {surge['new_prob'] * 100:.1f}% (>= 70%)！"
            ),
        )
        state = await self.engine.run_single_scan()
        return web.json_response({"ok": True, "surge": surge, "state": state})

    async def handle_manual_bet(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            market_id = str(body["market_id"])
            outcome_index = int(body.get("outcome_index", -1))
            amount_usdc = body.get("amount_usdc")
            pos = await self.engine.execute_manual_bet(
                market_id=market_id,
                outcome_index=outcome_index,
                amount_usdc=float(amount_usdc) if amount_usdc is not None else None,
            )
            return web.json_response({"ok": True, "position": pos, "state": self.engine.get_full_state()})
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

        state = self.engine.get_full_state()
        await self.broadcast_ws({"type": "STATE_UPDATE", "data": state})
        return web.json_response({"ok": True, "closed": closed, "state": state})

    async def handle_reset_portfolio(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            initial = float(body.get("initial_balance", 1000.0))
        except Exception:
            initial = 1000.0
        self.db.reset_paper_portfolio(initial_balance=initial)
        state = await self.engine.run_single_scan()
        return web.json_response({"ok": True, "state": state})

    async def handle_save_clob_credentials(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        masked = self.db.update_clob_credentials(body)
        self.db.add_log(
            level="INFO",
            trading_mode=self.db.get_config().get("trading_mode", "PAPER"),
            message="🔐 Polymarket CLOB API 與 Polygon 錢包憑證已更新",
        )
        state = self.engine.get_full_state()
        await self.broadcast_ws({"type": "STATE_UPDATE", "data": state})
        return web.json_response({"ok": True, "clob_status": masked, "state": state})

    async def handle_verify_clob(self, request: web.Request) -> web.Response:
        creds = self.db.get_clob_credentials()
        result = await self.poly_client.verify_clob_credentials(creds)
        return web.json_response(result)

    # --- Hong Kong Daily Temperature 75%+ Auto-Trading Handlers ---
    async def handle_hk_temp_state(self, request: web.Request) -> web.Response:
        data = await self.engine.hk_temp.scan_and_evaluate()
        return web.json_response({"ok": True, "hk_temp": data})

    async def handle_hk_temp_config(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        if str(body.get("mode", "")).upper() == "LIVE":
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
        state = self.engine.get_full_state()
        await self.broadcast_ws({"type": "STATE_UPDATE", "data": state})
        return web.json_response({"ok": True, "config": cfg, "state": state})

    async def handle_hk_temp_bet(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            outcome_name = str(body["outcome_name"])
            stake = body.get("stake_usdc")
            trade = await self.engine.hk_temp.execute_manual_hk_bet(
                outcome_name=outcome_name,
                stake_usdc=float(stake) if stake is not None else None,
            )
            await self.engine.hk_temp.scan_and_evaluate()
            state = self.engine.get_full_state()
            await self.broadcast_ws({"type": "STATE_UPDATE", "data": state})
            return web.json_response({"ok": True, "trade": trade, "state": state})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=25.0)
        await ws.prepare(request)
        self.ws_clients.add(ws)
        try:
            await ws.send_str(
                json.dumps({"type": "STATE_UPDATE", "data": self.engine.get_full_state()}, ensure_ascii=False)
            )
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    if msg.data == "ping":
                        await ws.send_str(json.dumps({"type": "PONG"}))
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.ws_clients.discard(ws)
        return ws


def create_app() -> web.Application:
    srv = PolySportsServer()
    app = web.Application()
    app.on_startup.append(srv.on_startup)
    app.on_cleanup.append(srv.on_cleanup)

    app.router.add_get("/", srv.handle_index)
    app.router.add_get("/api/health", srv.handle_health)
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
    app.router.add_get("/ws", srv.handle_ws)
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static")
    return app


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"🚀 PolySports 70%+ Auto-Trading System starting on http://{host}:{port}")
    web.run_app(create_app(), host=host, port=port, print=None)
