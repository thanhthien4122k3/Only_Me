# Luồng paper trading (chỉ đọc đối với skill)

```
OKX public API (ccxt, không key)
   │ fetch_ohlcv_live (HistoryCandles, chỉ nến đã đóng)
   ▼
paper.py --loop  ──giữ──▶ paper\paper.lock   (một tiến trình duy nhất)
   │ mỗi giờ, sau khi nến 1H đóng + 60s
   ▼
build_window_frames  ──▶ save_candle_store ──▶ paper\candles\<SYM>_<tf>.parquet
   │ data gate (CRITICAL dừng chu kỳ)
   ▼
run_backtest(execution_start=state.start_ts, execution_end=nến đóng mới nhất)
   │
   ├──▶ paper\trades.csv, equity.csv, orders.csv, signals.csv   (ghi đè mỗi chu kỳ)
   └──▶ paper\state.json  (start_ts, last_ts, config_hash, n_cycles)

paper.py --once     : cùng luồng, một chu kỳ, cũng giữ khóa
paper.py backfill   : chỉ tải nến vào store, không đổi state/trades
paper.py replay     : chạy trên store (--data-dir) và so với backtest, ghi vào thư mục riêng
paper.py report     : số liệu từ trades/equity, so với backtest cùng cửa sổ
```

## Thứ tự phụ thuộc (quan trọng khi tự sửa)

1. Store phải có trước replay. Store chỉ được ghi khi có chu kỳ mới đi qua data gate, hoặc khi chạy `backfill`.
2. `state.json` là nguồn sự thật cho cửa sổ. Thiếu `trades.csv` khi đã có state là dấu hiệu lỗi ghi file.
3. Chỉ một tiến trình được giữ khóa. Không bao giờ chạy `--once` khi loop đang chạy (sẽ bị từ chối, đúng chủ ý).
4. Cửa sổ một nến không có ý nghĩa đối chiếu: replay và backtest đều ra 0/0.

## Ranh giới

- `engine.py`, `strategy.py`, `metrics.py`, `validation.py`: logic chiến lược. Skill không sửa.
- `paper.py`: chỉ sửa khi bạn duyệt qua `memory\pending_review.md`.
- `paper\PREREGISTERED.md`: tiêu chí. Skill không sửa, chỉ đọc.
