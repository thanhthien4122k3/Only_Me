# OKX Trend-Following Backtest (Only_Me)

Long-only crypto trend-following research system on OKX public data.
**Spot + USDT-margined perpetual swaps. No API keys, no live trading, no order placement.**

This is a research tool. Backtest profit is NOT evidence of future profit. All reported
numbers are net of the modelled costs (fee + slippage, and funding where data exists).

## Strategy (baseline, unchanged from Stage 2)

- Entry timeframe 1H, trend filter 4H.
- 4H trend filter: close > EMA200 AND EMA50 > EMA50 6 bars ago (merged causally: a 4H bar
  is only visible to 1H bars closing at/after it).
- 1H confirmation: EMA20 > EMA50, RSI14 > 50, ADX14 > 20.
- Entry signal on a CLOSED 1H bar: close > highest high of previous 20 bars AND
  volume > 1.2 x volume SMA20.
- Signal at bar t close -> fill at bar t+1 open.
- Baseline exit: 1H EMA20 < EMA50 (at bar close).
- Optional exits (config `stops`, off by default): ATR stop, take-profit (R-multiple),
  trailing stop (ATR), time stop (bars).

## Setup

```powershell
py -3.11 -m venv .venv
& ".venv\Scripts\Activate.ps1"
pip install -r requirements.txt
```

## Commands

```powershell
python run.py download                          # spot OHLCV -> data/*.parquet
python run.py download --market futures         # swap OHLCV + funding + metadata -> data/futures/
python run.py signals                           # features + signals -> data/features_*.parquet
python run.py backtest --market spot            # -> results/<run_id>/spot/
python run.py backtest --market futures         # -> results/<run_id>/futures/
python run.py backtest --market all             # both + comparison -> results/<run_id>/compare.md
python run.py backtest --market spot --symbol BTC/USDT
python run.py validate --market spot            # walk-forward + hold-out + Gate 1 -> results/gate1.md
python run.py sweep --market spot --param risk.notional_cap=200,300,500
python -m pytest tests/ -q                      # offline test suite
```

## Outputs per run (`results/<run_id>/`)

Tất cả output trình bày (CLI, summary.md, compare.md, gate1.md) bằng **tiếng Việt**.
Các file dữ liệu (trades.csv, equity.csv, walk_forward.csv, sweep.csv, run_metadata.json)
giữ cột tiếng Anh để dễ xử lý/tái lập.

```
spot/ or futures/
  trades.csv             full trade list (entry/exit, fees, funding, net_pnl, exit_reason)
  equity.csv             equity curve per 1H bar (equity, cash, exposure_pct)
  summary.md             portfolio/trade metrics, costs, benchmarks, breakdowns, assumptions
  equity_drawdown.png    equity + drawdown plot
  run_metadata.json      config snapshot + data file sha256 fingerprints + assumptions
compare.md               (only for --market all) side-by-side spot vs futures
```

## Key accounting rules

- Fee and slippage are config placeholders (0.10% / 0.05% per side). **Verify the real OKX
  fee tier before trusting results.**
- Intrabar rule: if stop and take-profit are both inside one candle, the STOP is assumed to
  hit first (conservative). An open beyond the stop fills at the open.
- Sizing: risk 0.5% of equity per trade based on the 1.5xATR stop distance, notional cap
  300 USDT, max 3 concurrent positions, one position per symbol, ADX ranking when slots are
  full. Skip a signal when round-trip cost % / stop distance % > 0.20.
- Positions open at the end of data are force-closed at the last close
  (`exit_reason=end_of_data`).
- Futures: isolated margin, simplified single-tier MMR (0.005 placeholder), liquidation
  fill at the liquidation price. NOT equivalent to OKX's tiered model.
- Funding: OKX serves only ~3 months of funding history (verified) - funding is applied
  only within that window; mark price is proxied by the 1H close at the funding time.

## Data notes (verified against OKX, Nov 2026)

- OKX `Candles` endpoint cannot reach back > ~60 days -> downloader uses `HistoryCandles`
  (verified to 800+ days back), paginated with `since`/`limit=300`.
- Only candles whose close time <= fetch time are kept; duplicates dropped; UTC timestamps.
- Volume is in base currency (spot) / contracts (swap); the volume condition is a ratio
  against its own SMA, so units do not matter.
- Funding history: ~3 months back only (277 x 8h payments downloaded per symbol).

## Current results (default config, 2024-10-24 .. 2026-11-24, net of costs)

| | spot | futures |
|---|---|---|
| net return | +5.95% | +2.29% |
| trades | 58 | 45 |
| win rate | 29.3% | 33.3% |
| profit factor | 1.15 | 1.09 |
| max drawdown | 13.9% | 14.6% |
| benchmark B&H (equal-weight) | -35.5% | -35.8% |

Gate 1 (spot): hold-out PF 2.14 PASS, expectancy +1.03 PASS, >=100 trades FAIL (58),
maxDD <= 5% FAIL (13.9%), PF > 1 in >= 60% WF windows FAIL (56%, 5/9). **The strategy
does not pass Gate 1 in its current configuration.**

## Layout

```
config.yaml            strategy + backtest + spot + risk + stops + futures parameters
data.py                OKX download/quality/loading (spot + futures + funding + metadata)
indicators.py          EMA / RSI / ATR / ADX (Wilder, NaN warm-up)
strategy.py            feature building (causal 4H merge) + signal funnel
engine.py              shared backtest engine (orders, positions, fills, equity, funding)
markets/spot.py        cash + asset accounting
markets/futures.py     contract rounding, margin, liquidation
metrics.py             portfolio/trade metrics, costs, bootstrap CI, benchmarks
report.py              run-scoped outputs, metadata, fingerprints, comparison
validation.py          walk-forward, hold-out, Gate 1, tiny parameter sweeps
run.py                 CLI
tests/                 57 offline tests (incl. no-look-ahead proofs)
```

## Status

Stages 1-2 and Phases 1-5 complete: shared engine for spot+futures, honest cost model,
metrics/reports, walk-forward + hold-out + Gate 1, reproducible run-IDs, CLI.
Not planned yet: paper trading, live trading, SHORT strategies, ML.
