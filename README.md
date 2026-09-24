# Intelligent Trading Agent - DCA & Portfolio Rebalancer (Base L2)

Agente autonomo di **Dollar-Cost Averaging (DCA)** e **Ribilanciamento di Portafoglio** attivo su rete **Base (Chain ID 8453)** con integrazione **Uniswap V3**, modulazione dinamica tramite **Crypto Fear & Greed Index** e ragionamento assistito da **LLM (OpenRouter)**.

Parte della suite di trading agent modulari per Base (`intelligent-trading-agent`, `intelligent-trading-agent-neutral`, `intelligent-trading-agent-degen`, `intelligent-trading-agent-yield`, `intelligent-trading-agent-dca`, `intelligent-trading-agent-lp`).

---

## Caratteristiche Principali

1. **DCA Dinamico Guidato dal Sentiment (Fear & Greed)**:
   - Interroga l'API ufficiale di Alternative.me Crypto Fear & Greed.
   - **Extreme Fear ($\le 25$)**: Aumenta l'acquisto a **$1.50\times$** (compra il panico / dip).
   - **Fear ($26-45$)**: Acquisto a **$1.25\times$**.
   - **Neutral ($46-55$)**: Acquisto standard a **$1.00\times$**.
   - **Greed ($56-75$)**: Riduzione a **$0.75\times$**.
   - **Extreme Greed ($\ge 76$)**: Riduzione a **$0.50\times$** (cautela nei top di mercato).

2. **Ribilanciamento Multi-Asset con Soglia di Scostamento (Drift)**:
   - Configurazione pesi flessibile via variabile d'ambiente (es. `TARGET_WEIGHTS=WETH:0.50,CBBTC:0.30,USDC:0.20`).
   - Monitora la deviazione di ciascun asset rispetto al target.
   - Se lo scostamento supera la soglia (default $\pm 5\%$) e il controvalore minimo (default $\$10$), calcola la quota ottimale da vendere dagli asset sovrappesati e da acquistare per quelli sottopesati.
   - Protezione anti-churn: tempo minimo di attesa fra ribilanciamenti (default 12 ore).

3. **Integrazione On-Chain Uniswap V3**:
   - Routing diretto o via WETH per il miglior prezzo tra i pool V3 su Base (fee tier 0.01%, 0.05%, 0.3%, 1%).
   - Auto-refuel USDC automatico partendo da riserve ETH native.
   - Calcolo e verifica dello slippage on-chain con scadenze temporali strette (deadline e gas cap).

4. **Sicurezza Operativa a Tre Livelli**:
   - `PAPER_TRADING=true`: Simula completamente saldi, esecuzioni di swap, impatto di slippage e fee di gas senza interagire con la blockchain.
   - `DRY_RUN=true`: Valuta i prezzi on-chain in tempo reale e produce log e decisioni dettagliate, senza firmare alcuna transazione.
   - `LIVE`: Firma ed invia transazioni reali su Base tramite `BaseClient`.

5. **Interfaccia Web Dashboard & Telegram Bot**:
   - Dashboard HTTP standalone nativa (nessuna dipendenza da framework pesanti):
     - Grafico equity in tempo reale.
     - Gauge visivo Crypto Fear & Greed.
     - Barre di scostamento (Target vs Current Weight).
     - Storico acquisti DCA e rebalancing eseguiti.
     - Pulsante "Esegui ciclo ora" con autenticazione via admin token.
   - Bot Telegram con comandi `/status`, `/weights`, `/run`, `/help`.

---

## Architettura del Sistema

```
                            ┌────────────────────────┐
                            │   Fear & Greed Index   │
                            │  (Alternative.me API)  │
                            └───────────┬────────────┘
                                        │ Multiplier (0.5x - 1.5x)
                                        ▼
┌────────────────────────┐   ┌────────────────────────┐   ┌────────────────────────┐
│   Portfolio Tracker    │──>│       DCA Manager      │<──│     OpenRouter LLM     │
│  (Target vs Actual)    │   │  (Pianificazione Swap) │   │ (Reasoning + Fallback) │
└────────────────────────┘   └───────────┬────────────┘   └────────────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
     ┌───────────────────────┐                       ┌───────────────────────┐
     │      Paper Book       │                       │      Uniswap V3       │
     │   (Simulazione USD)   │                       │    (Esecuzione Live)  │
     └───────────────────────┘                       └───────────────────────┘
```

---

## Installazione e Configurazione

### 1. Prerequisiti
- Python 3.10+
- Un nodo RPC Base (default `https://mainnet.base.org`)
- Un wallet con fondi USDC / ETH su Base (opzionale per Paper Trading)

### 2. Configurazione Ambiente
```bash
cp .env.example .env
```

Modifica `.env`:
```ini
BASE_RPC_URL=https://mainnet.base.org
WALLET_ADDRESS=0x...
PRIVATE_KEY=...
DRY_RUN=false
PAPER_TRADING=false

# Pesi target di portafoglio
TARGET_WEIGHTS=WETH:0.50,CBBTC:0.30,USDC:0.20

# Importo base per acquisto DCA
DCA_BASE_AMOUNT_USD=50.0
DCA_INTERVAL_HOURS=24.0
```

### 3. Installazione Dipendenze
```bash
pip install -r requirements.txt
```

### 4. Avvio
```bash
python main.py
```

La dashboard web sarà accessibile all'indirizzo `http://localhost:8080`.

---

## Esecuzione con Docker e CapRover

```bash
docker build -t intelligent-trading-agent-dca .
docker run -d --name dca-agent -p 8080:8080 --env-file .env intelligent-trading-agent-dca
```

Supporto out-of-the-box per il deployment su **CapRover** tramite `captain-definition`.

---

## Test della Suite

```bash
python -m unittest discover tests
```

---

## Licenza
MIT
