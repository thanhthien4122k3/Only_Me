---
name: paper-daily-check
description: Kiểm tra paper trading OKX hằng ngày - loop còn chạy không, log có lỗi không, replay và backtest đối chiếu với live, và ghi báo cáo vào paper/daily/. Tự khởi động lại loop nếu đã dừng (tối đa 2 lần/ngày). Dùng khi người dùng yêu cầu kiểm tra paper, chạy kiểm tra hằng ngày, hoặc xem trạng thái loop.
---

# paper-daily-check

Kiểm tra trạng thái paper trading của project `okx-trend-backtest` và ghi báo cáo.

## Chạy

```powershell
.venv\Scripts\python.exe .claude\skills\paper-daily-check\scripts\daily_check.py
```

Mã thoát: `0` = OK, `1` = CẦN XEM, `2` = CẦN NGƯỜI XỬ LÝ.

Kết quả: `paper\daily\YYYY-MM-DD_HHMM.md`.

## Cấu trúc skill

- `references\` - tài liệu chỉ đọc: luồng paper, ghi chú dữ liệu OKX, liên kết tới tiêu chí. Skill đọc khi cần giải thích; không sửa.
- `assets\daily_report_template.md` - mẫu báo cáo. Script điền các chỗ `{...}`; không đổi cấu trúc mẫu.
- `memory\incidents.md` - nhật ký sự cố có bằng chứng. Skill đọc danh sách sự cố chưa xử lý để đưa vào báo cáo. Khi phát hiện vấn đề mới, skill tự ghi mục `AUTO-...` kèm đường dẫn báo cáo. Chỉ người dùng đóng mục (đổi `Trạng thái` thành `đã xử lý`).
- `memory\checks_to_run.md` - chỉ mục các kiểm tra K-01..K-10 (code trong `scripts\daily_check.py` là nguồn chính thức).
- `memory\pending_review.md` - đề xuất sửa code/tiêu chí. Skill chỉ ghi, không tự áp dụng.

Bài học từ sự cố chỉ có tác dụng khi được chuyển thành kiểm tra trong code. Ghi chú đơn thuần không ngăn được lỗi.

## Quy tắc cứng (không được vi phạm)

- **Không ghi** vào `references\` và `assets\`.
- **Không tự đóng** mục sự cố trong `memory\incidents.md`, và không tự áp dụng mục trong `memory\pending_review.md`.

- **Không sửa** `engine.py`, `strategy.py`, `metrics.py`, `validation.py`, `paper.py`, `data.py`, `config.yaml`, `paper\paper_config*.yaml`, `paper\PREREGISTERED.md`.
- **Không đổi tham số**, không quét tham số, không chọn cấu hình theo kết quả live.
- **Không đặt lệnh**, không đọc hay ghi API key.
- **Không kết luận lợi nhuận.** Chỉ đo số liệu và đối chiếu với tiêu chí đã đăng ký.
- Chỉ **ghi** vào `paper\daily\` và `paper\skill_state.json`. Mọi file khác chỉ đọc.

## Tự sửa được phép

Chỉ một việc: khởi động lại `paper.py --loop` khi loop đã dừng. Giới hạn: tối đa **2 lần mỗi ngày**. Nếu vượt, dừng tự sửa và báo cáo để người xem.

Không tự sửa lỗi code, lỗi dữ liệu, hay lệch khớp replay. Những việc đó chỉ được ghi vào báo cáo.

## Các bước script thực hiện

1. Kiểm tra loop có đang giữ khóa `paper\paper.lock` không. Không giữ được khóa nghĩa là loop đã dừng.
2. Đọc các dòng mới trong `paper\run.log` từ lần kiểm tra trước, đánh dấu `ERROR` và `CRITICAL`.
3. So `state.json.last_ts` với nến 1H đóng gần nhất. Trễ từ 2 nến trở lên khi loop đang chạy là cảnh báo.
4. Chạy `paper.py report` để lấy số liệu.
5. Chạy `paper.py replay` trên cửa sổ live, đọc từ `paper\candles\` (store nến live). Phải khớp 100%.
6. Chạy backtest trên lịch sử đầy đủ (`data\` + `paper\candles\`), cùng cửa sổ thời gian. So số lệnh trong ±30% và khớp từng lệnh.
7. Ghi báo cáo, cập nhật `skill_state.json`.

## Điều kiện "CẦN NGƯỜI XỬ LÝ"

- Loop dừng và không khởi động lại được, hoặc đã vượt 2 lần tự sửa trong ngày.
- Replay không khớp live.
- Số lệnh live nằm ngoài ±30% backtest cùng cửa sổ.
- Không có `paper\state.json`.

Khi gặp các trường hợp này, báo cáo nêu rõ vấn đề và dừng. Không thử sửa tiếp.

## Khi người dùng hỏi kết quả

Đọc báo cáo mới nhất trong `paper\daily\` và tóm tắt: kết luận, việc đã tự sửa, cảnh báo, và vấn đề cần người xử lý. Không diễn giải lợi nhuận nếu chưa đủ dữ liệu theo tiêu chí trong `paper\PREREGISTERED.md`.
