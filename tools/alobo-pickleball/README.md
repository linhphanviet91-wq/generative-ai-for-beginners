# Alobo Pickleball Finder

Tool để tự tra khi muốn chơi (không chạy theo lịch): tìm slot **xé vé trình newbie / chơi dưới 3 tháng** và **slot có máy bắn bóng** trên Alobo, trong bán kính quanh **Hapulico, 1 Nguyễn Huy Tưởng**.

## Cài đặt (máy cá nhân)
```bash
pip install -r requirements.txt
playwright install chromium
```

## Dùng
```bash
python alobo_pickle.py "tối mai"
python alobo_pickle.py "thứ 7 sáng" --radius 5
python alobo_pickle.py "cuối tuần" --newbie-only
python alobo_pickle.py "hôm nay 17h-21h" --machine-only
python alobo_pickle.py "25/10" --json
```
Cú pháp thời gian: `hôm nay`, `mai`, `ngày kia`, `thứ 2..7`, `CN`, `cuối tuần`, `sáng/trưa/chiều/tối`, `17h-21h`, `25/10`, `2026-10-25`.

## Lần đầu: chốt nguồn dữ liệu
Alobo không có API công khai, tool bắt JSON mà web Alobo tự gọi.
1. `python alobo_pickle.py --headed --wait 120 --dump dumps/` → cửa sổ Chromium mở ra, đăng nhập và vào trang xé vé/ghép kèo pickleball.
2. Copy URL trang đó vào `start_urls` trong `config.json`. Session login được giữ ở `.browser-profile/`.
3. Không ra slot nào → mở `dumps/*.json`, xem tên field thực tế rồi bổ sung regex vào `FIELD_KEYS` trong `alobo_pickle.py`. Lọc lại offline: `python alobo_pickle.py "tối mai" --from-file dumps/`.

Nếu xé vé chỉ có trong app mobile (không có trên web): bắt traffic app bằng mitmproxy, lưu JSON vào `dumps/`, rồi dùng `--from-file`.

## Logic lọc
- **Newbie**: newbie, người mới, mới chơi, beginner, cơ bản, dưới 3 tháng, 1–2 tháng, trình 1.0–2.5. Loại slot có level/tiêu đề ghi trên 3 tháng, khá, giỏi, 3.0+ hoặc "không nhận newbie".
- **Máy bắn bóng**: máy bắn bóng, máy ném/phát bóng, ball machine, launcher, robot.
- **Khoảng cách**: tính Haversine từ toạ độ nhà trong `config.json` (mặc định 3 km). Slot không có toạ độ thì đối chiếu tên đường trong `nearby_streets`.

Test: `python -m pytest -q tests`
