import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "polysports.db"

GAMMA_API_BASE = os.getenv("GAMMA_API_BASE", "https://gamma-api.polymarket.com")
CLOB_API_BASE = os.getenv("CLOB_API_BASE", "https://clob.polymarket.com")
POLYGON_CHAIN_ID = int(os.getenv("POLYGON_CHAIN_ID", "137"))

DEFAULT_BOT_CONFIG = {
    # Master switch & modes
    "bot_enabled": True,
    "trading_mode": os.getenv("TRADING_MODE", "PAPER"),  # "PAPER" or "LIVE"
    "data_source_mode": os.getenv("DATA_SOURCE_MODE", "AUTO"),  # "AUTO", "LIVE_API", "SIMULATION"
    "scan_interval_sec": int(os.getenv("SCAN_INTERVAL_SEC", "5")),

    # Core 70%+ Win-Probability Rule & Risk Controls
    "min_win_prob": float(os.getenv("MIN_WIN_PROB", "0.70")),       # 70% minimum probability trigger
    "max_win_prob": float(os.getenv("MAX_WIN_PROB", "0.95")),       # 95% max cap to avoid zero-ROI 99c markets
    "bet_amount_usdc": float(os.getenv("BET_AMOUNT_USDC", "25.0")), # Fixed USDC per trade
    "min_liquidity_usdc": float(os.getenv("MIN_LIQUIDITY_USDC", "1500.0")),
    "min_volume_usdc": float(os.getenv("MIN_VOLUME_USDC", "5000.0")),
    "max_spread": float(os.getenv("MAX_SPREAD", "0.05")),           # Max 5% bid-ask spread
    "prevent_duplicate_market": True,                               # One bet per game/conditionId
    "daily_budget_usdc": float(os.getenv("DAILY_BUDGET_USDC", "300.0")),
    "max_open_positions": int(os.getenv("MAX_OPEN_POSITIONS", "15")),

    # Optional Position Exit Rules (Take-Profit / Stop-Loss)
    "auto_take_profit_enabled": True,
    "take_profit_prob": float(os.getenv("TAKE_PROFIT_PROB", "0.96")),  # Auto-close when prob reaches 96%
    "auto_stop_loss_enabled": True,
    "stop_loss_prob": float(os.getenv("STOP_LOSS_PROB", "0.52")),      # Cut loss if team drops to 52%
    "stop_loss_minutes_before_end": int(os.getenv("STOP_LOSS_MINUTES_BEFORE_END", "20")),  # Only trigger SL when <= N mins before end (0 = anytime)

    # Sport Category Filter ("ALL" or comma-separated e.g. "NBA,SOCCER,NFL,MLB,NHL,TENNIS,UFC,ESPORTS")
    "allowed_sports": os.getenv("ALLOWED_SPORTS", "ALL"),

    # Paper Trading Virtual Bankroll
    "paper_initial_balance": float(os.getenv("PAPER_INITIAL_BALANCE", "1000.0")),
    "paper_cash_balance": float(os.getenv("PAPER_INITIAL_BALANCE", "1000.0")),
}


def get_env_clob_credentials() -> dict:
    """Return Polymarket CLOB credentials from environment variables if set."""
    return {
        "private_key": os.getenv("POLY_PRIVATE_KEY", "").strip(),
        "funder_address": os.getenv("POLY_FUNDER_ADDRESS", "").strip(),
        "api_key": os.getenv("POLY_API_KEY", "").strip(),
        "api_secret": os.getenv("POLY_API_SECRET", "").strip(),
        "api_passphrase": os.getenv("POLY_API_PASSPHRASE", "").strip(),
        "signature_type": int(os.getenv("POLY_SIGNATURE_TYPE", "1")),  # 0=EOA, 1=Poly Proxy/Email, 2=Gnosis Safe
    }
