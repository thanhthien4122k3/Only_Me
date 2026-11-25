# Danh sách kiểm tra (chỉ mục đọc được)

Mỗi mục là một kiểm tra đã được cài trong `scripts\daily_check.py`. Code là nguồn chính thức; file này chỉ để người đọc biết skill kiểm tra gì và vì sao.

| ID | Kiểm tra | Vì sao (incident) | Kết quả khi thất bại |
|---|---|---|---|
| K-01 | Đúng một loop giữ khóa `paper\paper.lock` | INC-001 | nếu không giữ khóa: tự khởi động lại (tối đa 2 lần/ngày) |
| K-02 | Có `trades.csv`, `equity.csv`, `orders.csv` khi `state.json` đã có | INC-005 | CẦN NGƯỜI XỬ LÝ |
| K-03 | Cửa sổ có ít nhất 2 nến mới đối chiếu replay/backtest | INC-004 | ghi chú "chưa đủ dữ liệu", không tính OK |
| K-04 | Store `paper\candles\` tồn tại và có nến đến `state.last_ts` | INC-006 | CẦN NGƯỜI XỬ LÝ (trước khi replay) |
| K-05 | Replay chạy trên store, không phải `data\` | INC-003 | ghi trong báo cáo |
| K-06 | Chỉ số backtest dùng equity của backtest | INC-002 | kiểm tra bằng test `test_report_bt_equity` (xem pending_review) |
| K-07 | Trễ nến so với nến đóng gần nhất | (vận hành) | CẦN XEM nếu trễ ≥ 2 nến khi loop đang chạy |
| K-08 | Số dòng ERROR/CRITICAL mới trong run.log | (vận hành) | CẦN XEM |
| K-09 | Số lệnh live trong ±30% backtest cùng cửa sổ | tiêu chí bổ sung | CẦN NGƯỜI XỬ LÝ |
| K-10 | Replay khớp 100% lệnh live | tiêu chí bổ sung | CẦN NGƯỜI XỬ LÝ |
