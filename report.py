"""Report outputs: results/<run_id>/{trades.csv, equity.csv, summary.md, equity_drawdown.png,
run_metadata.json}; market comparison for --market all."""
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from metrics import by_quarter, by_symbol


def make_run_id():
    """Unique run id: UTC timestamp + short random suffix."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_run_metadata(out_dir, cfg, summary, metrics, data_sources, cli_note):
    """Reproducibility: config snapshot + data fingerprints + assumptions."""
    meta = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cli": cli_note,
        "config": cfg,
        "summary": {k: str(v) for k, v in summary.items()},
        "metrics": {k: str(v) if isinstance(v, float) else v for k, v in metrics.items()},
        "data_fingerprints": [{"file": str(p), "sha256": sha256_file(p)}
                              for p in data_sources],
        "assumptions": [
            "fill_policy: next_bar_open (signal on closed bar t -> fill at open of bar t+1)",
            "intrabar rule: if stop and tp are both inside one candle, STOP assumed first",
            "open below stop fills at the open (slippage applied)",
            "fee and slippage applied to both entry and exit fills",
            "positions open at end of data are force-closed at the last close (end_of_data)",
            "futures: simplified isolated margin, single-tier MMR, liquidation fill at liq price",
            "futures: funding applied only within OKX funding history (~3 months back)",
            "futures: mark price at funding time = close of the 1H bar closing at funding ts",
        ],
    }
    (Path(out_dir) / "run_metadata.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return out_dir / "run_metadata.json"


def compare_markets(m_spot, m_fut):
    keys = ["net_return_pct", "final_equity", "n_trades", "win_rate_pct",
            "profit_factor", "expectancy_usdt", "max_drawdown_pct",
            "total_costs", "total_funding"]
    labels = ["lợi nhuận ròng (%)", "vốn cuối kỳ (USDT)", "số lệnh", "tỷ lệ thắng (%)",
              "profit factor", "expectancy (USDT)", "drawdown tối đa (%)",
              "tổng chi phí (USDT)", "tổng funding (USDT)"]
    rows = {"spot": [m_spot[c] for c in keys], "futures": [m_fut[c] for c in keys]}
    return pd.DataFrame(rows, index=labels).T.rename_axis("thị trường").reset_index()


def write_compare(out_dir, table):
    lines = ["# So sánh spot vs futures (cùng chiến lược, chỉ long, cùng config)", "",
             "Lưu ý: spot dùng giá spot, futures dùng giá swap; futures có thêm funding "
             "(chỉ ~3 tháng dữ liệu) và mô hình isolated margin đơn giản hóa.", "",
             _md_table(table)]
    (Path(out_dir) / "compare.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_dir / "compare.md"


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _md_table(df):
    if len(df) == 0:
        return "_không có lệnh_"
    head = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep = "|" + "|".join(["---"] * len(df.columns)) + "|"
    rows = ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join([head, sep] + rows)


METRIC_LABELS = {
    "initial_capital": "Vốn ban đầu (USDT)",
    "final_equity": "Vốn cuối kỳ (USDT)",
    "net_profit": "Lợi nhuận ròng (USDT)",
    "net_return_pct": "Lợi nhuận ròng (%)",
    "max_drawdown_usdt": "Drawdown tối đa (USDT)",
    "max_drawdown_pct": "Drawdown tối đa (%)",
    "sharpe_ratio": "Sharpe ratio",
    "sortino_ratio": "Sortino ratio",
    "exposure_time_pct": "Thời gian có vị thế (%)",
    "avg_exposure_pct": "Exposure trung bình (%)",
    "n_trades": "Số lệnh",
    "n_wins": "Lệnh thắng",
    "n_losses": "Lệnh thua",
    "win_rate_pct": "Tỷ lệ thắng (%)",
    "avg_win": "Lợi nhuận TB lệnh thắng (USDT)",
    "avg_loss": "Thua TB lệnh thua (USDT)",
    "largest_win": "Lệnh thắng lớn nhất (USDT)",
    "largest_loss": "Lệnh thua lớn nhất (USDT)",
    "profit_factor": "Profit factor",
    "expectancy_usdt": "Expectancy (USDT/lệnh)",
    "expectancy_r": "Expectancy (R)",
    "expectancy_ci_90_lo": "CI 90% expectancy (cận dưới)",
    "expectancy_ci_90_hi": "CI 90% expectancy (cận trên)",
    "avg_holding_hours": "Thời gian giữ lệnh TB (giờ)",
    "max_single_loss_pct_of_equity": "Lệnh thua lớn nhất (% vốn ban đầu)",
    "realized_vs_planned_risk": "Thua thực tế / rủi ro dự kiến (lệnh thua)",
    "total_fees": "Tổng phí (USDT)",
    "total_slippage": "Tổng slippage (USDT)",
    "total_costs": "Tổng chi phí (USDT)",
    "costs_share_of_gross_profit_pct": "Chi phí / lợi nhuận gộp (%)",
    "total_funding": "Tổng funding (USDT)",
    "funding_assumed_payments": "Funding giả định - thiếu dữ liệu (kỳ)",
    "funding_assumed_usdt": "Funding giả định - thiếu dữ liệu (USDT)",
}

EXIT_REASONS = {
    "ema_exit": "Thoát EMA (EMA20 < EMA50)",
    "stop": "Chạm stop (ATR)",
    "stop_first": "Stop chạm trước (stop và TP cùng trong nến)",
    "gap_open": "Gap mở cửa dưới stop",
    "tp": "Chạm take-profit",
    "time_stop": "Hết thời gian (time stop)",
    "trailing": "Trailing stop",
    "liquidation": "Thanh lý (liquidation)",
    "end_of_data": "Hết dữ liệu (đóng cưỡng bức)",
}

SKIP_REASONS = {
    "cost_ratio": "Tỷ lệ chi phí/stop quá cao",
    "slot_full": "Hết slot vị thế",
    "size_too_small": "Khối lượng quá nhỏ",
    "no_stop_reference": "Không có stop tham chiếu",
}


def write_report(cfg, trades, orders, equity, skipped, summary, metrics, benchmarks, out_dir="results"):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out / "trades.csv", index=False)
    equity.to_csv(out / "equity.csv", index=False)

    if len(equity):
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        ax1.plot(equity["timestamp"], equity["equity"], lw=1)
        ax1.set_title("Equity (USDT)")
        dd = equity["equity"] - equity["equity"].cummax()
        ax2.fill_between(equity["timestamp"], dd, 0, color="red", alpha=0.5)
        ax2.set_title("Drawdown (USDT)")
        fig.tight_layout()
        fig.savefig(out / "equity_drawdown.png", dpi=110)
        plt.close(fig)

    exit_counts = trades["exit_reason"].value_counts().to_dict() if len(trades) else {}
    skip_counts = skipped["reason"].value_counts().to_dict() if len(skipped) else {}

    market_vi = "futures (swap)" if summary.get("market_type") == "futures" else "spot"
    lines = [f"# Tổng kết Backtest ({market_vi})", "",
             f"- Sinh lúc: {pd.Timestamp.now(tz='UTC').isoformat(timespec='seconds')}",
             f"- Cặp giao dịch: {', '.join(cfg['data']['symbols'])}",
             f"- Phí: {cfg['backtest']['fee_pct']}% mỗi chiều (giá trị tạm), "
             f"Slippage: {cfg['backtest']['slippage_pct']}% mỗi chiều (giá trị tạm)",
             f"- Chính sách khớp lệnh: {cfg['backtest']['fill_policy']}",
             f"- Size: rủi ro {cfg['risk']['risk_pct']}% vốn mỗi lệnh, tối đa "
             f"{cfg['risk']['notional_cap']} USDT/vị thế, nhiều nhất "
             f"{cfg['risk']['max_positions']} vị thế đồng thời", "",
             "## Chỉ số danh mục", "",
             "| chỉ số | giá trị |", "|---|---|"]
    for k in ["initial_capital", "final_equity", "net_profit", "net_return_pct",
              "max_drawdown_usdt", "max_drawdown_pct", "sharpe_ratio", "sortino_ratio",
              "exposure_time_pct", "avg_exposure_pct"]:
        lines.append(f"| {METRIC_LABELS[k]} | {_fmt(metrics[k])} |")
    lines += ["", "## Chỉ số giao dịch", "", "| chỉ số | giá trị |", "|---|---|"]
    for k in ["n_trades", "n_wins", "n_losses", "win_rate_pct", "avg_win", "avg_loss",
              "largest_win", "largest_loss", "profit_factor", "expectancy_usdt",
              "expectancy_r", "expectancy_ci_90_lo", "expectancy_ci_90_hi",
              "avg_holding_hours", "max_single_loss_pct_of_equity",
              "realized_vs_planned_risk"]:
        lines.append(f"| {METRIC_LABELS[k]} | {_fmt(metrics[k])} |")
    lines += ["", "## Chi phí", "", "| chỉ số | giá trị |", "|---|---|"]
    for k in ["total_fees", "total_slippage", "total_costs",
              "costs_share_of_gross_profit_pct"]:
        lines.append(f"| {METRIC_LABELS[k]} | {_fmt(metrics[k])} |")
    if summary.get("market_type") == "futures":
        lines.append(f"| {METRIC_LABELS['total_funding']} | "
                     f"{_fmt(metrics.get('total_funding', 0.0))} |")
        if summary.get("funding_included"):
            lines.append(f"| {METRIC_LABELS['funding_assumed_payments']} | "
                         f"{summary.get('n_funding_fallback', 0)} kỳ |")
            lines.append(f"| {METRIC_LABELS['funding_assumed_usdt']} | "
                         f"{summary.get('funding_assumed_usdt', 0.0):.4f} |")
            lines.append("\nGhi chú funding: OKX chỉ cung cấp ~3 tháng lịch sử funding rate; "
                         "phí funding CHỈ được tính bằng dữ liệu thật trong khoảng có sẵn. "
                         "Các kỳ funding (00/08/16 UTC) không có dữ liệu thật được áp dụng "
                         "mức giả định fallback và ghi rõ là 'funding giả định - thiếu dữ "
                         "liệu'. Giá mark tại thời điểm funding được xấp xỉ bằng giá đóng "
                         "của cây nến 1H đóng đúng lúc funding.")
        else:
            lines.append("\nCẢNH BÁO: funding bị tắt - đây KHÔNG phải là backtest futures "
                         "đầy đủ (chưa bao gồm funding).")
    lines += ["", "## Benchmark (mua và giữ, đã trừ 1 vòng phí)", "",
              "| cặp | lợi nhuận % |", "|---|---|"]
    for _, r in benchmarks.iterrows():
        lines.append(f"| {r['symbol']} | {r['buy_hold_ret_pct']:.2f} |")
    lines += ["", "## Theo cặp giao dịch", ""]
    if len(trades):
        bs = by_symbol(trades).rename(columns={
            "symbol": "cặp", "n_trades": "số lệnh",
            "net_pnl": "PnL ròng (USDT)", "win_rate_pct": "tỷ lệ thắng (%)"})
        lines.append(_md_table(bs))
    else:
        lines.append("_không có lệnh_")
    lines += ["", "## Theo quý (tính theo thời điểm thoát lệnh)", ""]
    if len(trades):
        bq = by_quarter(trades).rename(columns={
            "quarter": "quý", "n_trades": "số lệnh",
            "net_pnl": "PnL ròng (USDT)", "win_rate_pct": "tỷ lệ thắng (%)"})
        lines.append(_md_table(bq))
    else:
        lines.append("_không có lệnh_")
    lines += ["", "## Lý do thoát lệnh", ""]
    for k, v in exit_counts.items():
        label = EXIT_REASONS.get(k)
        if label is None and k.endswith("_eod"):
            label = EXIT_REASONS.get(k[:-4], k) + " (hết dữ liệu)"
        lines.append(f"- {label or k}: {v}")
    lines += ["", "## Tín hiệu bị bỏ qua", ""]
    for k, v in skip_counts.items():
        lines.append(f"- {SKIP_REASONS.get(k, k)}: {v}")
    if summary.get("n_forced_close"):
        lines.append(f"\nGhi chú: {summary['n_forced_close']} vị thế bị đóng tại cây nến cuối "
                     f"(exit_reason=end_of_data).")
    lines += ["", "## Giả định mô phỏng", "",
              "- Chỉ có dữ liệu OHLCV: nếu stop và take-profit cùng nằm trong một cây nến, "
              "giả định STOP chạm trước (bảo thủ).",
              "- Giá mở cửa nằm dưới stop thì khớp tại giá mở cửa (kèm slippage).",
              "- Vào lệnh: tín hiệu trên nến t đã đóng -> khớp tại giá mở của nến t+1, có slippage.",
              "- Phí và slippage áp dụng cho cả hai chiều vào và ra.",
              "- Chỉ hỗ trợ market order khớp đủ; chưa có limit order, partial fill.",
              "- Vị thế còn mở khi hết dữ liệu bị đóng cưỡng bức tại giá đóng cuối "
              "(exit_reason=end_of_data).",
              "- Không có funding (spot). Funding futures: dữ liệu thật chỉ ~3 tháng gần nhất, "
              "các kỳ còn lại dùng mức giả định fallback (00/08/16 UTC).",
              "- fee_pct / slippage_pct là giá trị tạm: hãy kiểm tra mức phí OKX thực tế.",
              "- Quy tắc bỏ tín hiệu: bỏ khi chi phí round-trip % / khoảng cách stop % > "
              f"{cfg['backtest']['skip_cost_ratio']}."]
    if summary.get("market_type") == "futures":
        lines += ["", "## Giới hạn mô hình Futures", "",
                  "- Mô hình isolated margin đơn giản hóa với maintenance margin rate đơn tầng "
                  f"({cfg['futures']['maintenance_margin_rate']}, giá trị tạm) - OKX thật dùng "
                  "tỷ lệ theo tier; giá thanh lý ở đây KHÔNG tương đương OKX.",
                  "- Kiểm tra thanh lý trước stop/tp trong cây nến và khớp tại giá thanh lý "
                  "(bảo thủ).",
                  "- Margin không bị trừ khỏi cash; margin khả dụng khi vào lệnh = "
                  "cash - tổng initial margin đang mở.",
                  "- Khối lượng làm tròn xuống theo hợp đồng nguyên (ctVal)."]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
