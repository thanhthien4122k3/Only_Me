# Nhật ký sự cố paper trading

Chỉ ghi sự cố đã xảy ra thật, có bằng chứng (log hoặc file). Không ghi suy đoán.
Skill tự ghi thêm mục mới khi phát hiện vấn đề, nhưng KHÔNG tự đóng mục; người dùng đóng bằng dòng "Trạng thái: đã xử lý".

---

## INC-001 Hai loop chạy trùng trên cùng thư mục
- Ngày: 2026-11-25 (14:13 và 14:16 UTC)
- Triệu chứng: hai `paper.py --loop` cùng ghi `paper\`, log có dòng "loop mode" lặp.
- Nguyên nhân: khởi động loop từ hai nơi, không có khóa.
- Xử lý: dừng tiến trình trùng; thêm `InstanceLock` (flock/msvcrt) vào `paper.py`.
- Kiểm tra liên quan: `checks_to_run.md` K-01.
- Trạng thái: đã xử lý.

## INC-002 Báo cáo so maxDD backtest bằng equity của paper
- Ngày: 2026-11-25
- Triệu chứng: "maxDD% paper vs backtest" dùng cùng một equity, so sánh vô nghĩa.
- Nguyên nhân: `cmd_report` truyền `equity` (paper) vào `compute_metrics` của backtest.
- Xử lý: dùng `bt_equity` và `bt_orders` của chính backtest (`paper.py`, khoảng dòng 364).
- Kiểm tra liên quan: K-06.
- Trạng thái: đã xử lý (chưa kiểm chứng trên cửa sổ có nhiều lệnh, vì lúc sửa cửa sổ chỉ có 1 nến).

## INC-003 Replay tự so với chính nó
- Ngày: 2026-11-25
- Triệu chứng: `test_replay_matches_backtest` luôn pass.
- Nguyên nhân: cả hai phía đều gọi `run_backtest` trên cùng một hàm dựng khung dữ liệu.
- Xử lý: ghi nhận; phép kiểm tra thật là replay trên store live (K-04) và so với lịch sử đầy đủ (K-05).
- Trạng thái: đã ghi nhận, còn rủi ro về mặt thiết kế test.

## INC-004 Cửa sổ một nến cho kết quả 0/0 trông như OK
- Ngày: 2026-11-25 (báo cáo đầu tiên của skill)
- Triệu chứng: replay "0/0 khớp", backtest "0 lệnh", kết luận OK.
- Nguyên nhân: state chỉ có một nến, không có lệnh để đối chiếu.
- Xử lý: báo cáo ghi "chưa đủ dữ liệu để đối chiếu" (K-03).
- Trạng thái: đã xử lý.

## INC-005 `paper\` thiếu file kết quả dù `state.json` đã có chu kỳ
- Ngày: 2026-11-25
- Triệu chứng: `state.json` ghi 1 chu kỳ, nhưng không có `trades.csv`, `equity.csv`, `orders.csv`, `signals.csv`.
- Nguyên nhân: chưa rõ (có thể xóa tay hoặc ghi lỗi). Chưa xác định.
- Xử lý: skill kiểm tra sự hiện diện file (K-02). Nếu thiếu, báo "cần người xử lý".
- Trạng thái: chưa xác định nguyên nhân gốc.

## INC-006 Store nến trống trước chu kỳ đầu tiên
- Ngày: 2026-11-25
- Triệu chứng: `paper\candles\` không tồn tại sau khi khởi động lại loop, replay lỗi.
- Nguyên nhân: store chỉ được ghi khi có chu kỳ mới đi qua data gate.
- Xử lý: thêm lệnh `paper.py backfill`; skill báo khi store thiếu hoặc cũ hơn state (K-04).
- Trạng thái: đã xử lý.
