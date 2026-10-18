# alobo-xeve — tìm xé vé pickleball ALOBO gần Hapulico

Tool chạy tay khi muốn đi chơi (không schedule). Lọc slot xé vé trên https://datlich.alobo.vn theo:

- trình độ **newbie / dưới 3 tháng** (keyword: newbie, người mới, mới chơi, dưới 3 tháng, 0-3 tháng, beginner, cơ bản…)
- slot **có máy bắn bóng** (keyword: máy bắn bóng, ball machine, máy phát bóng…)
- khoảng cách tới **Hapulico, 1 Nguyễn Huy Tưởng, Thanh Xuân** (mặc định 5 km)
- ngày / khung giờ

## Cài (1 lần, trên máy cá nhân)

```bash
pip install -r requirements.txt
playwright install chromium
```

## Dùng

```bash
python alobo_xeve.py                                  # hôm nay, 5 km, newbie HOẶC máy bắn bóng
python alobo_xeve.py --date 2026-10-20 --from 18:00 --to 21:00
python alobo_xeve.py --days 3 --radius 7              # 3 ngày tới, bán kính 7 km
python alobo_xeve.py --both                           # chỉ slot vừa newbie vừa có máy (⭐)
python alobo_xeve.py --interactive                    # tự bấm vào mục Xé vé trên browser rồi Enter
python alobo_xeve.py --dump dump.json                 # lưu dữ liệu thô để lọc lại / debug
python alobo_xeve.py --from-dump dump.json --both     # lọc lại offline, không mở browser
```

## Cơ chế

datlich.alobo.vn là Flutter web, API mã hoá payload và đứng sau Cloudflare. Tool không gọi API trực tiếp,
không giả chữ ký: nó mở Chromium thật (profile lưu ở `~/.alobo-xeve-profile`, giữ cookie/đăng nhập),
để app tự tải dữ liệu, rồi bắt object JSON app đã giải mã (hook `JSON.parse` trong trang) và lọc offline.

## Lưu ý

- Lần đầu nên chạy không `--headless`; nếu Cloudflare hỏi xác minh thì bấm tay một lần.
- Nếu ra 0 vé: chạy `--interactive --dump dump.json`, vào tab Xé vé, chọn ngày, cuộn hết danh sách,
  rồi gửi `dump.json` để chỉnh lại tên field trong parser.
- Toạ độ Hapulico đặt sẵn trong `HOME` (≈20.99887, 105.80745) — sửa trong file nếu cần.
