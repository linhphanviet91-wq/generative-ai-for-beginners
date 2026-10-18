#!/usr/bin/env python3
"""Tra cứu slot xé vé pickleball trên Alobo: lọc trình độ newbie (<3 tháng),
slot có máy bắn bóng, và sân gần nhà (Hapulico, 1 Nguyễn Huy Tưởng).

Chạy khi cần, không schedule:
    python alobo_pickle.py "tối mai"
    python alobo_pickle.py "thứ 7" --radius 5
    python alobo_pickle.py "hôm nay 17h-21h" --machine-only
    python alobo_pickle.py --from-file dumps/   # lọc lại dữ liệu đã dump

Cơ chế: Playwright mở trang Alobo, bắt toàn bộ JSON response (API nội bộ của
web), nhận diện object giống "slot/trận" theo heuristic rồi lọc. Nếu Alobo đổi
schema, chạy --dump để lưu JSON thô và chỉnh FIELD_KEYS bên dưới.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"

# Tên key thường gặp trong API booking VN -> trường chuẩn hoá.
FIELD_KEYS = {
    "name": r"^(title|name|match_?name|event_?name|session_?name|ten.*)$",
    "venue": r"^(venue|venue_?name|court_?name|club_?name|yard_?name|san|ten_?san|location_?name|place_?name|shop_?name|store_?name)$",
    "address": r"^(address|full_?address|dia_?chi|location|addr)$",
    "lat": r"^(lat|latitude)$",
    "lng": r"^(lng|lon|long|longitude)$",
    "start": r"^(start|start_?time|start_?at|begin|from|from_?time|time_?start|gio_?bat_?dau|start_?date)$",
    "end": r"^(end|end_?time|end_?at|to|to_?time|time_?end|gio_?ket_?thuc|end_?date)$",
    "date": r"^(date|day|play_?date|booking_?date|ngay)$",
    "level": r"^(level|levels|skill|skill_?level|trinh_?do|rank|min_?level|max_?level|level_?name)$",
    "desc": r"^(desc|description|note|notes|content|detail|details|mo_?ta|ghi_?chu|rule|rules|tags?|services?|amenities|utilities)$",
    "price": r"^(price|fee|amount|cost|gia|ticket_?price|price_?per_?person)$",
    "slots": r"^(slot|slots|remain|remaining|available|available_?slots|empty_?slots|slot_?left|so_?cho)$",
    "url": r"^(url|link|share_?url|deeplink|slug)$",
}

LEVEL_POS = [
    r"newbie", r"new bie", r"\bnewb\b", r"beginner", r"moi choi", r"moi tap",
    r"nguoi moi", r"co ban", r"nhap mon", r"tap su",
    r"(duoi|<|it hon|chua toi|chua den)\s*3\s*thang", r"\b[0-2]\s*[-–]\s*3\s*thang",
    r"\b[1-2]\s*thang", r"trinh\s*(do\s*)?(1[.,]0|1[.,]5|2[.,]0|2[.,]5)\b",
    r"\b(1[.,]0|1[.,]5|2[.,]0)\s*[-–]\s*(2[.,]0|2[.,]5|3[.,]0)\b",
]
LEVEL_NEG = [
    r"khong (nhan|tiep|danh cho) (newbie|nguoi moi)", r"no newbie",
    r"(tren|>|tu)\s*([3-9]|1\d)\s*thang\s*(tro len)?", r"\b\d+\s*nam\b",
    r"trung binh kha", r"\bkha\b", r"\bgioi\b", r"pro\b", r"\b(3[.,][0-9]|[4-5][.,][0-9])\+?\b",
]
MACHINE_POS = [
    r"may ban bong", r"may ban", r"may nem bong", r"may phat bong", r"ball machine",
    r"ball launcher", r"pickleball machine", r"tennis machine", r"robot",
]

TIME_OF_DAY = {"sang": (5, 11), "trua": (11, 14), "chieu": (14, 18), "toi": (18, 23)}
WEEKDAYS = {"2": 0, "hai": 0, "3": 1, "ba": 1, "4": 2, "tu": 2, "5": 3, "nam": 3,
            "6": 4, "sau": 4, "7": 5, "bay": 5, "cn": 6, "chu nhat": 6}


def norm(s: str) -> str:
    """Bỏ dấu tiếng Việt, lower-case để match keyword."""
    s = unicodedata.normalize("NFD", str(s)).replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def snake(k: str) -> str:
    return norm(re.sub(r"(?<!^)(?=[A-Z])", "_", k)).replace("-", "_")


@dataclass
class Window:
    day: date | None
    start: time = time(0, 0)
    end: time = time(23, 59)
    days: list[date] = field(default_factory=list)  # cho "cuối tuần"

    def covers(self, dt: datetime | None) -> bool:
        if dt is None:
            return True
        allowed = self.days or ([self.day] if self.day else [])
        if allowed and dt.date() not in allowed:
            return False
        return self.start <= dt.time() <= self.end


def parse_when(text: str, today: date | None = None) -> Window:
    """'tối mai', 'thứ 7', 'cuối tuần', 'hôm nay 17h-21h', '25/10', '2026-10-25'."""
    today = today or date.today()
    t = norm(text or "")
    w = Window(day=None)
    if not t.strip():
        w.day = today
    if re.search(r"hom nay|nay\b", t):
        w.day = today
    if re.search(r"\bmai\b", t):
        w.day = today + timedelta(days=1)
    if re.search(r"ngay kia|\bkia\b", t):
        w.day = today + timedelta(days=2)
    if "cuoi tuan" in t:
        sat = today + timedelta(days=(5 - today.weekday()) % 7)
        w.days = [sat, sat + timedelta(days=1)]
    m = re.search(r"(thu\s*(2|3|4|5|6|7|hai|ba|tu|nam|sau|bay)|chu nhat|\bcn\b)", t)
    if m:
        key = m.group(2) or ("cn" if "cn" in m.group(0) else "chu nhat")
        wd = WEEKDAYS[key]
        w.day = today + timedelta(days=(wd - today.weekday()) % 7)
    if m := re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
        w.day = date(int(m[1]), int(m[2]), int(m[3]))
    elif m := re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        y = int(m[3]) if m[3] else today.year
        y += 2000 if y < 100 else 0
        w.day = date(y, int(m[2]), int(m[1]))
    for k, (a, b) in TIME_OF_DAY.items():
        if re.search(rf"\b{k}\b", t):
            w.start, w.end = time(a), time(b, 59) if b < 24 else time(23, 59)
    if m := re.search(r"(\d{1,2})(?:h|:)(\d{2})?\s*[-–den]+\s*(\d{1,2})(?:h|:)?(\d{2})?", t):
        w.start = time(int(m[1]), int(m[2] or 0))
        w.end = time(min(int(m[3]), 23), int(m[4] or 0))
    if w.day is None and not w.days:
        w.day = today
    return w


def parse_dt(v, ref_day: date | None = None) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        ts = v / 1000 if v > 1e11 else v
        try:
            return datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M", "%Y/%m/%d %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    if m := re.fullmatch(r"(\d{1,2})[:h](\d{2})?(?::\d{2})?", s):
        return datetime.combine(ref_day or date.today(), time(int(m[1]) % 24, int(m[2] or 0)))
    return None


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class Slot:
    name: str = ""
    venue: str = ""
    address: str = ""
    lat: float | None = None
    lng: float | None = None
    start: datetime | None = None
    end: datetime | None = None
    level: str = ""
    desc: str = ""
    price: str = ""
    slots: str = ""
    url: str = ""
    raw_text: str = ""
    source: str = ""
    distance_km: float | None = None
    near_by_street: bool = False
    is_newbie: bool = False
    has_machine: bool = False

    def key(self):
        return (norm(self.venue or self.name), self.start, norm(self.name))


def _flatten_text(obj, depth=0) -> list[str]:
    if depth > 4:
        return []
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return []
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _flatten_text(v, depth + 1)]
    if isinstance(obj, list):
        return [s for v in obj[:30] for s in _flatten_text(v, depth + 1)]
    return []


def _pick(d: dict, field_name: str):
    rx = re.compile(FIELD_KEYS[field_name])
    for k, v in d.items():
        if rx.match(snake(k)):
            if isinstance(v, dict):  # venue: {name, address, lat...}
                for kk in ("name", "title", "address", "fullAddress"):
                    if isinstance(v.get(kk), str):
                        return v[kk]
                continue
            if isinstance(v, list):
                v = ", ".join(str(x.get("name", x)) if isinstance(x, dict) else str(x) for x in v)
            return v
    return None


def _nested_geo(d: dict):
    """Tìm lat/lng ở chính dict hoặc trong venue/location/club lồng 1-2 cấp."""
    stack = [(d, 0)]
    while stack:
        cur, lvl = stack.pop()
        lat, lng = _pick(cur, "lat"), _pick(cur, "lng")
        try:
            if lat is not None and lng is not None:
                return float(lat), float(lng), cur
        except (TypeError, ValueError):
            pass
        if lvl < 2:
            stack += [(v, lvl + 1) for v in cur.values() if isinstance(v, dict)]
    return None, None, None


def looks_like_slot(d: dict) -> bool:
    keys = {snake(k) for k in d}
    has_time = any(re.match(FIELD_KEYS[f], k) for f in ("start", "date") for k in keys)
    has_ident = any(re.match(FIELD_KEYS[f], k) for f in ("name", "venue", "level") for k in keys)
    return has_time and has_ident


def extract_slots(payload, source="") -> list[Slot]:
    out: list[Slot] = []

    def walk(o, parent_geo=None):
        if isinstance(o, list):
            for x in o:
                walk(x, parent_geo)
            return
        if not isinstance(o, dict):
            return
        lat, lng, geo_holder = _nested_geo(o)
        geo = (lat, lng, geo_holder) if lat is not None else parent_geo
        if looks_like_slot(o):
            out.append(build_slot(o, geo, source))
        for v in o.values():
            if isinstance(v, (dict, list)):
                walk(v, geo)

    walk(payload)
    return out


def build_slot(d: dict, geo, source: str) -> Slot:
    s = Slot(source=source)
    s.name = str(_pick(d, "name") or "")
    s.venue = str(_pick(d, "venue") or "")
    s.address = str(_pick(d, "address") or "")
    if geo:
        s.lat, s.lng, holder = geo
        if holder is not None and holder is not d:
            s.venue = s.venue or str(_pick(holder, "name") or "")
            s.address = s.address or str(_pick(holder, "address") or "")
    day = parse_dt(_pick(d, "date"))
    s.start = parse_dt(_pick(d, "start"), day.date() if day else None) or day
    s.end = parse_dt(_pick(d, "end"), (s.start or day).date() if (s.start or day) else None)
    s.level = str(_pick(d, "level") or "")
    s.desc = str(_pick(d, "desc") or "")
    s.price = str(_pick(d, "price") or "")
    s.slots = str(_pick(d, "slots") or "")
    s.url = str(_pick(d, "url") or "")
    s.raw_text = " | ".join(_flatten_text(d))[:2000]
    return s


def classify(s: Slot, cfg: dict) -> Slot:
    lvl_text = norm(" ".join([s.level, s.name, s.desc, s.raw_text]))
    pos = any(re.search(p, lvl_text) for p in LEVEL_POS)
    # Chỉ xét negative trên level/name để tránh ăn nhầm mô tả chung của sân.
    neg_scope = norm(" ".join([s.level, s.name]))
    neg = any(re.search(p, neg_scope) for p in LEVEL_NEG) and not any(
        re.search(p, neg_scope) for p in LEVEL_POS)
    s.is_newbie = pos and not neg
    s.has_machine = any(re.search(p, lvl_text) for p in MACHINE_POS)
    home = cfg["home"]
    if s.lat is not None and s.lng is not None:
        s.distance_km = round(haversine_km(home["lat"], home["lng"], s.lat, s.lng), 2)
    addr = norm(" ".join([s.address, s.venue, s.name, s.raw_text]))
    s.near_by_street = any(norm(st) in addr for st in cfg["nearby_streets"])
    return s


def is_near(s: Slot, radius: float) -> bool:
    if s.distance_km is not None:
        return s.distance_km <= radius
    return s.near_by_street


def collect_live(cfg: dict, args) -> list[tuple[str, object]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Thiếu playwright: pip install playwright && playwright install chromium")

    captured: list[tuple[str, object]] = []
    urls = args.url or cfg["start_urls"]
    profile = HERE / ".browser-profile"
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile), headless=not args.headed, locale="vi-VN",
            geolocation={"latitude": cfg["home"]["lat"], "longitude": cfg["home"]["lng"]},
            permissions=["geolocation"], viewport={"width": 1280, "height": 900},
            executable_path=os.environ.get("ALOBO_CHROMIUM") or None,
        )

        def on_response(resp):
            ct = resp.headers.get("content-type", "")
            if "json" not in ct:
                return
            try:
                captured.append((resp.url, resp.json()))
            except Exception:
                pass

        ctx.on("response", on_response)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for u in urls:
            try:
                page.goto(u, wait_until="networkidle", timeout=45000)
            except Exception as e:
                print(f"[!] Không mở được {u}: {e}", file=sys.stderr)
                continue
            for _ in range(8):  # cuộn để trigger lazy-load / phân trang
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(700)
            # DOM fallback: text các card để lọc khi API không trả JSON rõ ràng
            try:
                texts = page.eval_on_selector_all(
                    "a, article, li, div[class*='card'], div[class*='item'], div[class*='match']",
                    "els => els.map(e => e.innerText).filter(t => t && t.length > 30 && t.length < 1500)")
                captured.append((u + "#dom", {"dom_cards": [{"title": t.split('\n')[0], "start": "",
                                                             "desc": t} for t in set(texts)]}))
            except Exception:
                pass
        if args.headed:
            print(f"[i] Cửa sổ mở {args.wait}s: tự đăng nhập/điều hướng tới trang xé vé, tool vẫn đang bắt dữ liệu…")
            page.wait_for_timeout(args.wait * 1000)
        ctx.close()
    return captured


def load_dumps(path: Path) -> list[tuple[str, object]]:
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    out = []
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "url" in data and "body" in data:
            out.append((data["url"], data["body"]))
        else:
            out.append((f.name, data))
    return out


def save_dumps(captured, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, (u, body) in enumerate(captured):
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", u.split("?")[0])[-60:]
        (out_dir / f"{i:03d}_{slug}.json").write_text(
            json.dumps({"url": u, "body": body}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[i] Đã dump {len(captured)} response vào {out_dir}")


def fmt(s: Slot) -> str:
    when = s.start.strftime("%a %d/%m %H:%M") if s.start else "?"
    if s.end:
        when += s.end.strftime("-%H:%M")
    dist = f"{s.distance_km} km" if s.distance_km is not None else ("gần (theo địa chỉ)" if s.near_by_street else "? km")
    tags = " ".join(t for t, ok in (("[NEWBIE]", s.is_newbie), ("[MÁY BẮN]", s.has_machine)) if ok)
    lines = [f"• {when} | {s.venue or s.name} | {dist} {tags}".rstrip()]
    if s.name and s.name != s.venue:
        lines.append(f"    {s.name}")
    meta = " · ".join(x for x in (f"Trình độ: {s.level}" if s.level else "", f"Giá: {s.price}" if s.price else "",
                                  f"Còn: {s.slots}" if s.slots else "", s.address) if x)
    if meta:
        lines.append(f"    {meta}")
    if s.url:
        lines.append(f"    {s.url}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tìm slot xé vé pickleball Alobo gần Hapulico")
    ap.add_argument("when", nargs="*", help='vd: "tối mai", "thứ 7", "cuối tuần", "hôm nay 17h-21h", "25/10"')
    ap.add_argument("--radius", type=float, help="bán kính km (mặc định theo config)")
    ap.add_argument("--newbie-only", action="store_true", help="chỉ slot trình độ newbie/<3 tháng")
    ap.add_argument("--machine-only", action="store_true", help="chỉ slot có máy bắn bóng")
    ap.add_argument("--all-distance", action="store_true", help="bỏ lọc khoảng cách")
    ap.add_argument("--url", action="append", help="URL trang Alobo cần quét (lặp được)")
    ap.add_argument("--headed", action="store_true", help="mở trình duyệt thật để đăng nhập/điều hướng")
    ap.add_argument("--wait", type=int, default=60, help="số giây giữ cửa sổ khi --headed")
    ap.add_argument("--dump", type=Path, help="lưu JSON thô vào thư mục này")
    ap.add_argument("--from-file", type=Path, help="lọc offline từ file/thư mục JSON đã dump")
    ap.add_argument("--json", action="store_true", help="xuất JSON thay vì text")
    args = ap.parse_args(argv)

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    radius = args.radius or cfg["radius_km"]
    win = parse_when(" ".join(args.when))

    captured = load_dumps(args.from_file) if args.from_file else collect_live(cfg, args)
    if args.dump:
        save_dumps(captured, args.dump)

    seen, slots = set(), []
    for src, body in captured:
        for s in extract_slots(body, src):
            classify(s, cfg)
            if s.key() in seen:
                continue
            seen.add(s.key())
            slots.append(s)

    def keep(s: Slot) -> bool:
        if not win.covers(s.start):
            return False
        if not args.all_distance and not is_near(s, radius):
            return False
        if args.newbie_only:
            return s.is_newbie
        if args.machine_only:
            return s.has_machine
        return s.is_newbie or s.has_machine

    hits = sorted((s for s in slots if keep(s)),
                  key=lambda s: (s.start or datetime.max, s.distance_km if s.distance_km is not None else 99))
    if args.json:
        print(json.dumps([{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in s.__dict__.items()
                           if k != "raw_text"} for s in hits], ensure_ascii=False, indent=2))
        return 0

    span = ", ".join(d.strftime("%a %d/%m") for d in (win.days or [win.day]))
    print(f"Khung: {span} {win.start:%H:%M}-{win.end:%H:%M} | bán kính {radius} km quanh {cfg['home']['label']}")
    print(f"Quét {len(captured)} response, nhận diện {len(slots)} slot, khớp {len(hits)}.\n")
    newbie = [s for s in hits if s.is_newbie]
    machine = [s for s in hits if s.has_machine]
    if not args.machine_only:
        print(f"== Xé vé trình newbie / <3 tháng ({len(newbie)}) ==")
        print("\n".join(map(fmt, newbie)) or "  (không có)")
        print()
    if not args.newbie_only:
        print(f"== Slot có máy bắn bóng ({len(machine)}) ==")
        print("\n".join(map(fmt, machine)) or "  (không có)")
    if not slots:
        print("\n[!] Không nhận diện được slot nào. Chạy lại với --headed --dump dumps/ rồi mở trang "
              "xé vé thủ công để bắt API, sau đó chỉnh FIELD_KEYS/start_urls.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
