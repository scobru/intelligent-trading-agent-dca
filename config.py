"""
Configurazione del bot DCA e Ribilanciatore di portafoglio su Base (chain id 8453).

Il bot mantiene i pesi obiettivo (es. 50% WETH, 30% CBBTC, 20% USDC),
effettua acquisti ricorrenti (DCA) modulati dal sentiment di mercato (Crypto Fear & Greed)
e ribilancia automaticamente quando un asset si discosta dal peso target oltre la soglia.
"""

import os
from typing import Dict
from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return int(default)


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on", "si")


# ---------------------------------------------------------------- rete
CHAIN_ID = 8453
CHAIN_NAME = "Base"
BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")

# ---------------------------------------------------------------- contratti Base
WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
CBBTC = "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"
LINK = "0x88Fb150BDc53A65fe94Dea0c9BA0a6dAf8C6e196"
UNI = "0xc3De830EA07524a0761646a6a4e4be0e114a3C83"
AERO = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
WSTETH = "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452"
CBETH = "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22"

# Uniswap V3 su Base
UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
UNISWAP_V3_QUOTER_V2 = "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"
UNISWAP_V3_SWAP_ROUTER_02 = "0x2626664c2603336E57B271c5C0b26F421741e481"
FEE_TIERS = (100, 500, 3000, 10000)

KNOWN_ASSETS = {
    "USDC": {"address": USDC, "decimals": 6, "stable": True, "category": "Stablecoin"},
    "CBBTC": {"address": CBBTC, "decimals": 8, "stable": False, "category": "Store of Value"},
    "WETH": {"address": WETH, "decimals": 18, "stable": False, "category": "L1/L2 Infrastructure"},
    "LINK": {"address": LINK, "decimals": 18, "stable": False, "category": "Oracles / RWA"},
    "UNI": {"address": UNI, "decimals": 18, "stable": False, "category": "DeFi / DEX"},
    "AERO": {"address": AERO, "decimals": 18, "stable": False, "category": "Base DEX"},
}

BASE_CURRENCY = "USDC"

# ---------------------------------------------------------------- pesi obiettivo portafoglio
# Formato env: TARGET_WEIGHTS="WETH:0.25,CBBTC:0.25,LINK:0.15,UNI:0.10,AERO:0.10,USDC:0.15"
DEFAULT_TARGET_WEIGHTS = {
    "WETH": 0.25,
    "CBBTC": 0.25,
    "LINK": 0.15,
    "UNI": 0.10,
    "AERO": 0.10,
    "USDC": 0.15,
}

def _parse_target_weights(raw: str) -> Dict[str, float]:
    weights = {}
    total = 0.0
    aliases = {
        "ETH": "WETH",
        "BTC": "CBBTC",
    }
    for part in raw.split(","):
        if ":" in part:
            sym, w = part.split(":", 1)
            sym = sym.strip().upper()
            sym = aliases.get(sym, sym)
            try:
                val = float(w.strip())
                if val > 0:
                    weights[sym] = val
                    total += val
            except ValueError:
                continue
    if not weights or total <= 0:
        return dict(DEFAULT_TARGET_WEIGHTS)
    # Normalizza a somma 1.0
    return {k: round(v / total, 4) for k, v in weights.items()}


TARGET_WEIGHTS = _parse_target_weights(
    os.getenv("TARGET_WEIGHTS", "WETH:0.25,CBBTC:0.25,LINK:0.15,UNI:0.10,AERO:0.10,USDC:0.15")
)

# ---------------------------------------------------------------- allocazione tattica AI
AI_TACTICAL_WEIGHTS_ENABLED = _b("AI_TACTICAL_WEIGHTS_ENABLED", True)
AI_RECALIBRATE_INTERVAL_HOURS = _f("AI_RECALIBRATE_INTERVAL_HOURS", 24.0)
MIN_ASSET_WEIGHT = _f("MIN_ASSET_WEIGHT", 0.05)
MAX_ASSET_WEIGHT = _f("MAX_ASSET_WEIGHT", 0.45)

# ---------------------------------------------------------------- DCA dinamico (Fear & Greed)
DCA_ENABLED = _b("DCA_ENABLED", True)
DCA_BASE_AMOUNT_USD = _f("DCA_BASE_AMOUNT_USD", 50.0)         # Importo base di acquisto per ciclo DCA
DCA_INTERVAL_HOURS = _f("DCA_INTERVAL_HOURS", 24.0)          # Intervallo minimo fra acquisti DCA regolari
FEAR_GREED_ENABLED = _b("FEAR_GREED_ENABLED", True)
FEAR_GREED_API_URL = "https://api.alternative.me/fng/?limit=1"

# Moltiplicatori DCA in base al sentiment
MULTIPLIER_EXTREME_FEAR = _f("MULTIPLIER_EXTREME_FEAR", 1.50)  # F&G <= 25: compra 50% in piu'
MULTIPLIER_FEAR = _f("MULTIPLIER_FEAR", 1.25)                  # F&G 26-45: compra 25% in piu'
MULTIPLIER_NEUTRAL = _f("MULTIPLIER_NEUTRAL", 1.00)            # F&G 46-55: acquisto standard
MULTIPLIER_GREED = _f("MULTIPLIER_GREED", 0.75)                # F&G 56-75: riduci acquisto del 25%
MULTIPLIER_EXTREME_GREED = _f("MULTIPLIER_EXTREME_GREED", 0.50) # F&G >= 76: riduci acquisto del 50%

# ---------------------------------------------------------------- ribilanciamento
REBALANCE_ENABLED = _b("REBALANCE_ENABLED", True)
REBALANCE_THRESHOLD_PCT = _f("REBALANCE_THRESHOLD_PCT", 5.0)   # Scostamento minimo (es. +/- 5%) per attivare ribilanciamento
MIN_REBALANCE_USD = _f("MIN_REBALANCE_USD", 10.0)              # Evita micro-swap insignificanti
MAX_REBALANCE_USD = _f("MAX_REBALANCE_USD", 1500.0)            # Limite massimo di swap singolo per ribilanciamento
MIN_HOLD_HOURS_BETWEEN_REBALANCE = _f("MIN_HOLD_HOURS_BETWEEN_REBALANCE", 12.0)

# ---------------------------------------------------------------- sicurezza operativa
DRY_RUN = _b("DRY_RUN", True)
PAPER_TRADING = _b("PAPER_TRADING", False)
PAPER_START_USDC = _f("PAPER_START_USDC", 1000.0)
PAPER_START_ETH = _f("PAPER_START_ETH", 0.05)
PAPER_GAS_USD = _f("PAPER_GAS_USD", 0.02)

if PAPER_TRADING:
    DRY_RUN = True

MIN_ETH_RESERVE = _f("MIN_ETH_RESERVE", 0.001)
DEFAULT_SLIPPAGE_BPS = _i("DEFAULT_SLIPPAGE_BPS", 50)          # 0.50%
MAX_SLIPPAGE_BPS = _i("MAX_SLIPPAGE_BPS", 100)                  # 1.00%
MAX_GAS_PRICE_GWEI = _f("MAX_GAS_PRICE_GWEI", 0.5)
TX_DEADLINE_SECONDS = _i("TX_DEADLINE_SECONDS", 300)
TX_TIMEOUT_SECONDS = _i("TX_TIMEOUT_SECONDS", 180)
HTTP_TIMEOUT = _i("HTTP_TIMEOUT", 30)

# ---------------------------------------------------------------- limiti RPC
# Retry con backoff esponenziale su 429 / errori di rete (4 tentativi: attese 0.5, 1, 2 s)
RPC_RETRIES = _i("RPC_RETRIES", 4)
RPC_BACKOFF_FACTOR = _f("RPC_BACKOFF_FACTOR", 0.5)
# Multicall3 (stesso indirizzo su tutte le chain EVM, Base inclusa)
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
# La dashboard ricalcola /api/status al massimo ogni N secondi (saldi) e i prezzi ogni M
DASHBOARD_STATUS_TTL = _i("DASHBOARD_STATUS_TTL", 60)
DASHBOARD_PRICES_TTL = _i("DASHBOARD_PRICES_TTL", 300)

# ---------------------------------------------------------------- auto-refuel USDC da ETH
AUTO_SWAP_ETH_TO_USDC = _b("AUTO_SWAP_ETH_TO_USDC", True)
ETH_GAS_RESERVE = _f("ETH_GAS_RESERVE", 0.003)
MIN_ETH_SWAP_AMOUNT = _f("MIN_ETH_SWAP_AMOUNT", 0.002)
USDC_AUTO_SWAP_THRESHOLD = _f("USDC_AUTO_SWAP_THRESHOLD", 5.0)

# ---------------------------------------------------------------- percorsi persistenti
def persistent_path(filename: str) -> str:
    project_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(project_dir, "data")
    if os.path.isdir(data_dir) and os.access(data_dir, os.W_OK):
        return os.path.join(data_dir, filename)
    return os.path.join(project_dir, filename)


SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH") or persistent_path("dca_agent.db")
PORTFOLIO_PATH = os.getenv("PORTFOLIO_PATH") or persistent_path("portfolio_state.json")
PAPER_STATE_PATH = os.getenv("PAPER_STATE_PATH") or persistent_path("paper_portfolio.json")
