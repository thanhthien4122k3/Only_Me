# Paper Trading - Tiêu chí đăng ký TRƯỚC (PREREGISTERED)

Ngày đóng băng tiêu chí: 2026-11-25. KHÔNG được sửa các tiêu chí này sau khi đã
xem kết quả live.

Chế độ: GIẢ LẬP hoàn toàn (paper). Chỉ dùng public OKX data, không API key,
không đặt lệnh thật, không ghi thông tin cá nhân.

## Tiêu chí đánh giá (BẢN GỐC - đã thay thế bởi "Bổ sung 2026-11-25" bên dưới, giữ nguyên để đối chiếu)

1. Thời gian chạy tối thiểu: 6 tháng kể từ nến bắt đầu paper.
2. Mục tiêu (ghi rõ nếu không đạt, không được đổi mục tiêu):
   - Số lệnh >= 100.
   - Max drawdown <= 5% vốn.
   - Expectancy > 0 và cận dưới CI 90% (bootstrap) > 0.
3. Ngưỡng cảnh báo: nếu expectancy live thấp hơn cận dưới CI 90% của backtest
   trong 3 tháng liên tiếp -> DỪNG đánh giá và báo cáo.

## Cấu hình khởi đầu

- risk_pct = 1.0, notional_cap = 300 USDT, max_positions = 3.
- Futures: leverage = 2.0 (không đặt 5x).
- Bản chính: ATR stop BẬT (initial_stop_enabled: true).
- Bản so sánh: ATR stop TẮT (paper_config_nostop.yaml).
- Phí 0.10% + slippage 0.05% mỗi chiều là GIẢ ĐỊNH cho tới khi xác nhận tier OKX thật.

## An toàn

- KHÔNG có hàm đặt lệnh / endpoint private / đọc API key trong paper.py.
- Funding futures: chỉ dùng fallback rate (public API không có lịch sử funding live).
- Nến thiếu/không qua data gate -> dừng và ghi log, không tự suy đoán giá.

## Bổ sung 2026-11-25 (thay thế mục "Tiêu chí đánh giá" bản gốc)

**Lý do.** Backtest hiện tại cho spot khoảng 58 lệnh trong ~25 tháng (~2.3 lệnh/tháng),
futures 47 lệnh. Với 6 tháng, kỳ vọng chỉ khoảng 11-14 lệnh. Mục tiêu "≥ 100 lệnh" là
không thể đạt, và với ~14 lệnh khoảng CI 90% của expectancy quá rộng để có ý nghĩa. Tiêu
chí gốc vì vậy không thể cho ra kết luận có thông tin. Bổ sung này được ghi lại trước khi có
bất kỳ kết quả live nào.

**Mục đích được xác định lại.** Paper trading trước hết kiểm chứng ĐỘ TRUNG THỰC của việc
triển khai: live có tái hiện đúng hành vi backtest không. Việc chứng minh lợi nhuận là mục
tiêu RIÊNG, cần dữ liệu dài hơn, không được đánh giá bằng chỉ vài chục lệnh.

### Tiêu chí mới

1. **Thời gian tối thiểu**: 12 tháng liên tục kể từ nến bắt đầu paper. Báo cáo mỗi quý.
2. **Độ trung thực (tiêu chí PASS/FAIL chính)**:
   - Replay (`paper.py replay`) trên đúng cửa sổ live đã lưu phải khớp 100% lệnh với
     live (0 lệch). Chạy kiểm tra này ít nhất mỗi quý.
   - Số lệnh live trong mỗi cửa sổ nằm trong ±30% so với backtest chạy trên cùng cửa sổ
     và cùng dữ liệu.
   - Mọi lệnh live phải có tín hiệu tương ứng trong backtest; không có lệnh "không giải thích được".
3. **Hiệu suất (báo cáo, KHÔNG là PASS/FAIL cho đến khi có ≥ 50 lệnh live đã đóng)**:
   expectancy, PF, max drawdown, kèm CI 90% và so sánh với backtest trên cùng cửa sổ.
   Mục tiêu gốc "maxDD ≤ 5%" được giữ làm tham chiếu, không phải điều kiện dừng.
4. **Ngưỡng cảnh báo (đã điều chỉnh)**: đánh giá theo QUÝ thay vì 3 tháng liên tiếp. Nếu
   một quý có ≥ 5 lệnh đã đóng mà expectancy live thấp hơn cận dưới CI 90% của backtest
   cùng cửa sổ, thì DỪNG đánh giá và báo cáo. Quý có < 5 lệnh được ghi là "không đủ dữ liệu",
   không tính là cảnh báo.

### Ghi chú

- Các cấu hình khởi đầu, mục An toàn ở trên không thay đổi.
- Bổ sung này chỉ thay đổi tiêu chí đánh giá. Nếu đổi cấu hình chiến lược, phải lập
  bổ sung mới trước khi chạy.
