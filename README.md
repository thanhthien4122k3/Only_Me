# OKX Trend-Following Backtest

Long-only crypto trend-following strategy on OKX spot data (BTC/USDT, ETH/USDT, SOL/USDT).
Public market data only — no API keys, no live trading.

## Setup

```powershell
py -3.11 -m venv .venv
& ".venv\Scripts\Activate.ps1"
pip install -r requirements.txt
```

## Commands

```powershell
python run.py download   # stage 1: fetch OHLCV -> data/*.parquet
```

## Data notes (verified against OKX, Nov 2026)

- The OKX `Candles` endpoint cannot reach back > ~60 days (returns empty) — the downloader
  uses `HistoryCandles` explicitly, which reaches 800+ days back.
- OKX accepted `limit=300` per request on `HistoryCandles` (docs say 100); the pagination
  loop does not depend on the exact limit, so either value is safe.
- `since` returns candles with `open_time >= since`, ascending. Loop cursor =
  `last_ts + timeframe`.
- Only candles whose close time <= fetch time are kept (last, still-open candle is dropped).
- Volume is in base currency (BTC/ETH/SOL), not USDT.

Status: Stage 1 complete. Stages 2-3 in progress.
