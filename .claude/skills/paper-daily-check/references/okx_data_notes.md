# Ghi chú dữ liệu OKX (chỉ đọc đối với skill)

- **Candles vs HistoryCandles:** endpoint `Candles` chỉ với lại khoảng 60 ngày; dự án dùng `HistoryCandles` (đã kiểm tra đến 800+ ngày). Pagination bằng `since`, `limit=300`.
- **Nến chưa đóng:** `candles_to_df` loại mọi nến có `ts + tf > now`. Nến 1H của giờ hiện tại không bao giờ được dùng.
- **Funding:** lịch sử ~3 tháng gần nhất. Public API không có funding live trong paper, nên paper chỉ dùng fallback 0.01%/8h (ghi rõ trong báo cáo).
- **Volume:** đơn vị base (spot) hoặc contract (swap). Điều kiện volume là tỷ lệ với SMA của chính nó, nên đơn vị không ảnh hưởng.
- **Kích thước hợp đồng swap** (`data\futures\instruments.json`): BTC 0.01 BTC/contract, ETH 0.1, SOL 1. Với vốn nhỏ, BTC có thể không mở được vị thế tối thiểu.
- **Giá spot và swap khác nhau** (basis). Không so sánh trực tiếp giá lệnh spot với swap.
- **Dữ liệu local** (`data\`) hiện chỉ đến 2026-11-24. Lịch sử mới hơn nằm trong `paper\candles\` (store live).
