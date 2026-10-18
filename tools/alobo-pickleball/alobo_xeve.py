#!/usr/bin/env python3
"""Tìm slot xé vé pickleball trên ALOBO (datlich.alobo.vn) gần nhà.

Cách hoạt động:
  datlich.alobo.vn là app Flutter web, API trả dữ liệu đã mã hoá và nằm sau
  Cloudflare. Tool KHÔNG gọi API trực tiếp mà mở Chromium thật (Playwright),
  để app tự tải dữ liệu như người dùng bình thường, rồi bắt các object JSON
  mà app giải mã (hook JSON.parse trong trang). Sau đó lọc offline:
    - trình độ newbie / dưới 3 tháng
    - slot có máy bắn bóng
    - khoảng cách tới nhà (mặc định: Hapulico, 1 Nguyễn Huy Tưởng, Thanh Xuân)
    - ngày / khung giờ

Ví dụ:
  python alobo_xeve.py                       # hôm nay, bán kính 5 km
  python alobo_xeve.py --date 2026-10-20 --from 18:00 --to 21:00
  python alobo_xeve.py --days 3 --radius 7 --both
  python alobo_xeve.py --from-dump dump.json # lọc lại dữ liệu đã lưu, không mở browser
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable

APP_URL = "https://datlich.alobo.vn/"
HOME = {"name": "Hapulico, 1 Nguyễn Huy Tưởng, Thanh Xuân", "lat": 20.99887, "lng": 105.80745}
PICKLEBALL_SPORT_TYPE = 5  # /api/v1/public/sport-type: pickleball = 5
PROFILE_DIR = Path.home() / ".alobo-xeve-profile"

NEWBIE_PATTERNS = [
    r"\bnewbie\b", r"\bnew bie\b", r"\bbeginner", r"\bnguoi moi\b", r"\bmoi choi\b",
    r"\bmoi tap\b", r"\bmoi bat dau\b", r"\bnhap mon\b", r"\bco ban\b",
    r"(duoi|<|<=|under|it hon|chua (den|toi))\s*3\s*thang",
    r"\b0\s*[-–~]\s*3\s*thang", r"\b1\s*[-–~]\s*3\s*thang",
]
MACHINE_PATTERNS = [
    r"may ban bong", r"may ban\b", r"may nem bong", r"may phat bong", r"ball machine",
    r"robot (ban|phat) bong", r"\btap may\b",
]
NEWBIE_RE = re.compile("|".join(NEWBIE_PATTERNS))
MACHINE_RE = re.compile("|".join(MACHINE_PATTERNS))

# JS chạy trước app: bọc JSON.parse, giữ lại payload có vẻ là dữ liệu sân/vé.
CAPTURE_JS = r"""
(() => {
  const KEEP = /socialOneTime|branch|Branch|yard|core|booking|level|price/;
  const orig = JSON.parse;
  window.__aloboCaptured = [];
  let total = 0;
  JSON.parse = function (text, reviver) {
    const out = orig.call(this, text, reviver);
    try {
      if (typeof text === 'string' && text.length > 80 && text.length < 8e6 && KEEP.test(text)
          && total < 60e6) {
        window.__aloboCaptured.push(text);
        total += text.length;
      }
    } catch (e) {}
    return out;
  };
})();
"""


# ---------------------------------------------------------------- helpers
def fold(s: Any) -> str:
    """Bỏ dấu tiếng Việt + lowercase để so khớp keyword."""
    s = str(s or "").replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk(obj: Any) -> Iterable[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def pick(d: dict, *keys: str) -> Any:
    """Lấy giá trị theo tên key (không phân biệt hoa thường), thử lần lượt."""
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, "", [], {}):
            return v
    return None


def as_text(v: Any) -> str:
    if isinstance(v, dict):
        return str(pick(v, "name", "title", "label", "vi", "value") or "")
    if isinstance(v, list):
        return ", ".join(as_text(x) for x in v)
    return "" if v is None else str(v)


def coords(d: dict) -> tuple[float, float] | None:
    lat = pick(d, "lat", "latitude")
    lng = pick(d, "lng", "long", "lon", "longitude")
    if lat is not None and lng is not None:
        try:
            return float(lat), float(lng)
        except (TypeError, ValueError):
            pass
    for key in ("location", "position", "coordinate", "geo", "loc"):
        v = pick(d, key)
        if isinstance(v, dict):
            c = coords(v)
            if c:
                return c
            cc = pick(v, "coordinates")
            if isinstance(cc, list) and len(cc) == 2:  # GeoJSON [lng, lat]
                return float(cc[1]), float(cc[0])
        if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            a, b = float(v[0]), float(v[1])
            return (a, b) if abs(a) <= 90 and abs(b) > 90 else (b, a)
    return None


def parse_dt(v: Any) -> dt.datetime | None:
    if v is None:
        return None
    if isinstance(v, (int, float)) and v > 1e9:
        ts = v / 1000 if v > 1e12 else v
        return dt.datetime.fromtimestamp(ts)
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            t = dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
        if t.tzinfo is not None:  # đổi về giờ VN rồi bỏ tz
            t = t.astimezone(dt.timezone(dt.timedelta(hours=7))).replace(tzinfo=None)
        elif s.endswith("Z"):
            t = t + dt.timedelta(hours=7)
        return t
    return None


def parse_hm(v: Any) -> dt.time | None:
    m = re.search(r"(\d{1,2})[:hH](\d{2})?", str(v or ""))
    if not m:
        return None
    return dt.time(int(m.group(1)) % 24, int(m.group(2) or 0))


# ---------------------------------------------------------------- extraction
def load_payloads(raw: list[str]) -> list[Any]:
    out = []
    for t in raw:
        try:
            out.append(json.loads(t))
        except (json.JSONDecodeError, TypeError):
            pass
    return out


def collect_branches(payloads: list[Any]) -> dict[str, dict]:
    branches: dict[str, dict] = {}
    for p in payloads:
        for d in walk(p):
            bid = pick(d, "_id", "id", "branchId")
            name = pick(d, "name", "branchName")
            c = coords(d)
            if bid and name and c:
                branches[str(bid)] = {
                    "id": str(bid), "name": as_text(name),
                    "address": as_text(pick(d, "address", "fullAddress", "addressDetail")),
                    "lat": c[0], "lng": c[1],
                }
    return branches


def looks_like_ticket(d: dict) -> bool:
    keys = {k.lower() for k in d}
    has_time = bool(keys & {"start", "starttime", "start_time", "timestart", "fromtime", "time",
                            "date", "datestart", "startdate", "hours", "starthour"})
    has_branch = bool(keys & {"branch", "branchid", "branch_id", "branchname"})
    social = "socialonetime" in fold(json.dumps(d, ensure_ascii=False)[:400]) or bool(
        keys & {"level", "slot", "slots", "maxplayer", "maxplayers", "totalslot", "remainslot",
                "participants", "members", "players", "ticket", "tickets"})
    return has_time and (has_branch or social)


def normalize_ticket(d: dict, branches: dict[str, dict]) -> dict:
    branch = pick(d, "branch")
    bid = pick(d, "branchId", "branch_id")
    binfo: dict = {}
    if isinstance(branch, dict):
        bid = bid or pick(branch, "_id", "id")
        binfo = {"name": as_text(pick(branch, "name")),
                 "address": as_text(pick(branch, "address", "fullAddress"))}
        c = coords(branch)
        if c:
            binfo.update(lat=c[0], lng=c[1])
    elif isinstance(branch, str):
        bid = bid or branch
    if bid and str(bid) in branches:
        binfo = {**branches[str(bid)], **{k: v for k, v in binfo.items() if v}}
    c = coords(d)
    if c and "lat" not in binfo:
        binfo.update(lat=c[0], lng=c[1])

    start = parse_dt(pick(d, "start", "startTime", "start_time", "timeStart", "dateStart",
                          "startDate", "fromTime", "date"))
    end = parse_dt(pick(d, "end", "endTime", "end_time", "timeEnd", "dateEnd", "endDate", "toTime"))
    hours = pick(d, "hours", "time", "startHour")
    if start and start.time() == dt.time(0, 0) and hours:
        h = parse_hm(hours if not isinstance(hours, list) else hours[0])
        if h:
            start = dt.datetime.combine(start.date(), h)
        if isinstance(hours, list) and len(hours) > 1:
            h2 = parse_hm(hours[-1])
            if h2:
                end = dt.datetime.combine(start.date(), h2)

    title = as_text(pick(d, "name", "title", "socialName", "eventName"))
    level = as_text(pick(d, "level", "levelName", "skill", "skillLevel", "trinhDo"))
    desc = as_text(pick(d, "description", "desc", "note", "content", "detail"))
    yard = as_text(pick(d, "yard", "yardName", "core", "coreName", "court", "courtName"))
    blob = fold(" ".join([title, level, desc, yard, json.dumps(d, ensure_ascii=False)]))
    dist = None
    if "lat" in binfo:
        dist = haversine_km(HOME["lat"], HOME["lng"], binfo["lat"], binfo["lng"])
    return {
        "id": str(pick(d, "_id", "id") or ""),
        "title": title, "level": level, "desc": desc[:200], "yard": yard,
        "branch": binfo.get("name", ""), "address": binfo.get("address", ""),
        "distance_km": round(dist, 2) if dist is not None else None,
        "start": start, "end": end,
        "price": pick(d, "price", "priceOneTime", "ticketPrice", "amount", "fee"),
        "slots_left": pick(d, "remainSlot", "slotRemain", "available", "remaining", "emptySlot"),
        "slots_total": pick(d, "totalSlot", "maxPlayer", "maxPlayers", "slot", "slots", "quantity"),
        "sport_type": pick(d, "sportType", "sport_type"),
        "newbie": bool(NEWBIE_RE.search(blob)),
        "machine": bool(MACHINE_RE.search(blob)),
    }


def extract_tickets(payloads: list[Any]) -> list[dict]:
    branches = collect_branches(payloads)
    seen, out = set(), []
    for p in payloads:
        for d in walk(p):
            if not looks_like_ticket(d):
                continue
            t = normalize_ticket(d, branches)
            key = t["id"] or (t["branch"], t["start"], t["title"])
            if key in seen or t["start"] is None:
                continue
            seen.add(key)
            out.append(t)
    return out


def filter_tickets(tickets: list[dict], args: argparse.Namespace) -> list[dict]:
    d0 = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    dates = {d0 + dt.timedelta(days=i) for i in range(args.days)}
    t_from, t_to = parse_hm(args.from_), parse_hm(args.to)
    now = dt.datetime.now()
    res = []
    for t in tickets:
        s = t["start"]
        if s.date() not in dates or s < now - dt.timedelta(minutes=15):
            continue
        if t_from and s.time() < t_from:
            continue
        if t_to and s.time() > t_to:
            continue
        if t["sport_type"] not in (None, "") and str(t["sport_type"]) != str(PICKLEBALL_SPORT_TYPE):
            continue
        if t["distance_km"] is None:
            if not args.include_unknown_distance:
                continue
        elif t["distance_km"] > args.radius:
            continue
        if args.both and not (t["newbie"] and t["machine"]):
            continue
        if not args.both and not (t["newbie"] or t["machine"]):
            continue
        res.append(t)
    res.sort(key=lambda t: (t["start"], t["distance_km"] if t["distance_km"] is not None else 99))
    return res


# ---------------------------------------------------------------- browser
def capture(args: argparse.Namespace) -> list[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Thiếu Playwright: pip install playwright && playwright install chromium")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=args.headless, locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh", viewport={"width": 430, "height": 900},
            geolocation={"latitude": HOME["lat"], "longitude": HOME["lng"]},
            permissions=["geolocation"],
        )
        ctx.add_init_script(CAPTURE_JS)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(8_000)
        _try_open_xeve_tab(page)
        page.wait_for_timeout(args.wait * 1000)
        if args.interactive:
            input(">> Trên cửa sổ Chromium: vào mục Xé vé / Social, chọn ngày, cuộn danh sách."
                  "\n>> Xong bấm Enter ở đây... ")
        raw = page.evaluate("window.__aloboCaptured || []")
        ctx.close()
    return raw


def _try_open_xeve_tab(page) -> None:
    """Bật semantics của Flutter rồi thử bấm tab Xé vé. Không được thì thôi."""
    try:
        page.evaluate("""() => {
            const p = document.querySelector('flt-semantics-placeholder');
            if (p) p.click();
        }""")
        page.wait_for_timeout(1_500)
        for label in ("Xé vé", "Xe ve", "Social", "Vé lẻ"):
            loc = page.get_by_text(re.compile(label, re.I))
            if loc.count():
                loc.first.click(timeout=3_000)
                return
            loc = page.get_by_label(re.compile(label, re.I))
            if loc.count():
                loc.first.click(timeout=3_000)
                return
    except Exception:
        pass


# ---------------------------------------------------------------- output
def fmt(t: dict) -> str:
    tag = ("⭐ " if t["newbie"] and t["machine"] else "") + \
          ("[newbie] " if t["newbie"] else "") + ("[máy bắn bóng] " if t["machine"] else "")
    end = f"-{t['end']:%H:%M}" if t["end"] else ""
    dist = f"{t['distance_km']} km" if t["distance_km"] is not None else "? km"
    slots = ""
    if t["slots_left"] is not None or t["slots_total"] is not None:
        slots = f" | còn {t['slots_left'] if t['slots_left'] is not None else '?'}" \
                f"/{t['slots_total'] if t['slots_total'] is not None else '?'}"
    price = f" | {t['price']:,}đ" if isinstance(t["price"], (int, float)) else (
        f" | {t['price']}" if t["price"] else "")
    lines = [f"{t['start']:%a %d/%m %H:%M}{end} | {dist} | {t['branch'] or '(chưa rõ sân)'}"
             f"{price}{slots}",
             f"    {tag}{t['title']}" + (f" — trình độ: {t['level']}" if t["level"] else "")]
    if t["address"]:
        lines.append(f"    {t['address']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Tìm xé vé pickleball ALOBO gần Hapulico.")
    ap.add_argument("--date", help="YYYY-MM-DD, mặc định hôm nay")
    ap.add_argument("--days", type=int, default=1, help="số ngày tính từ --date (mặc định 1)")
    ap.add_argument("--from", dest="from_", help="giờ bắt đầu sớm nhất, vd 17:30")
    ap.add_argument("--to", help="giờ bắt đầu muộn nhất, vd 21:00")
    ap.add_argument("--radius", type=float, default=5.0, help="bán kính km (mặc định 5)")
    ap.add_argument("--both", action="store_true", help="chỉ lấy slot vừa newbie vừa có máy bắn bóng")
    ap.add_argument("--include-unknown-distance", action="store_true",
                    help="giữ cả slot không xác định được toạ độ sân")
    ap.add_argument("--headless", action="store_true", help="chạy ẩn (có thể bị Cloudflare chặn)")
    ap.add_argument("--interactive", action="store_true",
                    help="dừng để mày tự bấm vào mục Xé vé trên browser rồi mới đọc dữ liệu")
    ap.add_argument("--wait", type=int, default=10, help="giây chờ app tải dữ liệu (mặc định 10)")
    ap.add_argument("--dump", help="lưu payload bắt được ra file JSON (để debug / lọc lại)")
    ap.add_argument("--from-dump", help="đọc payload từ file dump, không mở browser")
    ap.add_argument("--json", action="store_true", help="in kết quả dạng JSON")
    args = ap.parse_args()

    if args.from_dump:
        raw = json.loads(Path(args.from_dump).read_text(encoding="utf-8"))
    else:
        raw = capture(args)
    if args.dump:
        Path(args.dump).write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    tickets = extract_tickets(load_payloads(raw))
    hits = filter_tickets(tickets, args)

    if args.json:
        print(json.dumps(hits, ensure_ascii=False, default=str, indent=2))
        return
    print(f"Bắt được {len(raw)} payload, {len(tickets)} slot/vé; khớp điều kiện: {len(hits)}"
          f" (bán kính {args.radius} km quanh {HOME['name']})\n")
    for t in hits:
        print(fmt(t) + "\n")
    if not tickets:
        print("Không nhận diện được vé nào. Chạy lại với --interactive --dump dump.json, "
              "tự vào mục Xé vé trên browser, rồi gửi file dump để chỉnh parser.")


if __name__ == "__main__":
    main()
