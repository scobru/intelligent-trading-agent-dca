# Intelligent Trading Agent - DCA & Portfolio Rebalancer (Base L2)

**English** · [Italiano](README.it.md)

> ⚠️ **Experimental software, not financial advice.** The bot trades real money on Base and can lose some or all of the capital you give it. Start with paper trading or dry-run; when you go live, use a dedicated wallet and only amounts you can afford to lose. See the **Disclaimer** section at the bottom.

An autonomous **Dollar-Cost Averaging (DCA)** and **portfolio rebalancing**
agent on **Base (chain ID 8453)**, with **Uniswap V3** execution, sizing
modulated by the **Crypto Fear & Greed Index** and reasoning assisted by an
**LLM (OpenRouter)**.

It is part of the [Intelligent Trading](https://github.com/scobru/intelligent-trading)
suite of agents for Base.

---

## Main features

1. **Sentiment-driven dynamic DCA (Fear & Greed)**:
   - Queries the official Alternative.me Crypto Fear & Greed API.
   - **Extreme Fear ($\le 25$)**: raises the buy to **$1.50\times$** (buy the panic / dip).
   - **Fear ($26-45$)**: buys at **$1.25\times$**.
   - **Neutral ($46-55$)**: standard buy at **$1.00\times$**.
   - **Greed ($56-75$)**: reduces to **$0.75\times$**.
   - **Extreme Greed ($\ge 76$)**: reduces to **$0.50\times$** (caution near market tops).

2. **Multi-asset rebalancing with a drift threshold**:
   - Target weights configurable through an environment variable
     (e.g. `TARGET_WEIGHTS=WETH:0.25,CBBTC:0.25,LINK:0.15,UNI:0.10,AERO:0.10,USDC:0.15`).
   - Tracks each asset's deviation from its target.
   - When the drift exceeds the threshold (default $\pm 5\%$) and the minimum
     value (default $\$10$), it computes how much to sell from overweight
     assets and buy for underweight ones.
   - Anti-churn protection: minimum waiting time between rebalances
     (default 12 hours).

3. **On-chain Uniswap V3 integration**:
   - Direct or WETH-routed swaps for the best price across V3 pools on Base
     (fee tiers 0.01%, 0.05%, 0.3%, 1%).
   - Automatic USDC refuel from native ETH reserves.
   - On-chain slippage checks with tight deadlines and a gas cap.

4. **Three safety levels**:
   - `PAPER_TRADING=true`: fully simulates balances, swap execution, slippage
     impact and gas fees without touching the blockchain.
   - `DRY_RUN=true`: reads on-chain prices in real time and produces detailed
     logs and decisions, without signing any transaction.
   - `LIVE`: signs and sends real transactions on Base through `BaseClient`.

5. **Web dashboard & Telegram bot**:
   - Standalone native HTTP dashboard (no heavy frameworks):
     - real-time equity chart;
     - Crypto Fear & Greed gauge;
     - drift bars (target vs current weight);
     - history of DCA buys and rebalances;
     - "Run cycle now" button, authenticated with `DASHBOARD_RUN_TOKEN`.
   - Telegram bot with `/status`, `/weights`, `/run`, `/help` commands.

---

## Architecture

```
                            ┌────────────────────────┐
                            │   Fear & Greed Index   │
                            │  (Alternative.me API)  │
                            └───────────┬────────────┘
                                        │ Multiplier (0.5x - 1.5x)
                                        ▼
┌────────────────────────┐   ┌────────────────────────┐   ┌────────────────────────┐
│   Portfolio Tracker    │──>│       DCA Manager      │<──│     OpenRouter LLM     │
│  (Target vs Actual)    │   │    (Swap planning)     │   │ (Reasoning + Fallback) │
└────────────────────────┘   └───────────┬────────────┘   └────────────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
     ┌───────────────────────┐                       ┌───────────────────────┐
     │      Paper Book       │                       │      Uniswap V3       │
     │   (USD simulation)    │                       │   (Live execution)    │
     └───────────────────────┘                       └───────────────────────┘
```

---

## Installation and configuration

### 1. Requirements
- Python 3.10+
- A Base RPC node (default `https://mainnet.base.org`)
- A wallet with USDC / ETH on Base (optional for paper trading)

### 2. Environment
```bash
cp .env.example .env
```

Edit `.env`:
```ini
BASE_RPC_URL=https://mainnet.base.org
WALLET_ADDRESS=0x...
PRIVATE_KEY=...
# start safe: set both to false only when you are ready to go live
DRY_RUN=true
PAPER_TRADING=true

# Target portfolio weights
TARGET_WEIGHTS=WETH:0.25,CBBTC:0.25,LINK:0.15,UNI:0.10,AERO:0.10,USDC:0.15

# Base amount per DCA buy
DCA_BASE_AMOUNT_USD=50.0
DCA_INTERVAL_HOURS=24.0
```

### 3. Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run
```bash
python main.py
```

The web dashboard listens on `http://localhost:3000` by default (or on
`DASHBOARD_PORT` if set; the `.env.example` uses 8080).

---

## Docker and CapRover

```bash
docker build -t intelligent-trading-agent-dca .
docker run -d --name dca-agent -p 3000:3000 --env-file .env intelligent-trading-agent-dca
```

Out-of-the-box support for **CapRover** deployment through
`captain-definition`.

---

## Tests

```bash
python -m unittest discover tests
```

---

## ⚠️ Disclaimer

This software is experimental and provided "as is", without warranty of any
kind (see the MIT license). It is not financial advice nor an invitation to
invest.

- **You can lose money.** Bugs, wrong model decisions, slippage, protocol
  exploits, manipulated oracles and liquidations can cause the loss of some or
  all of your capital.
- **Decisions are made by an LLM.** It can be wrong or behave unpredictably:
  the executor's limits reduce the damage, they do not eliminate it. Past
  results, paper ones included, do not guarantee future ones.
- **Start with paper or dry-run.** When live, use a wallet dedicated to the
  bot, with amounts you can afford to lose, and never reuse that private key
  elsewhere.
- **Protect your keys.** The private key belongs only in the deployment's
  environment variables: never commit it. Without `DASHBOARD_RUN_TOKEN` the
  dashboard commands stay disabled: set it to a long random value before
  exposing the dashboard to the Internet.
- **Laws and taxes.** You are responsible for complying with the rules and tax
  obligations of your country.
- **Market exposure.** DCA accumulates volatile assets (ETH, cbBTC and
  others): the portfolio's value follows the market and can fall for a long
  time.

## License
MIT
