import sys
from datetime import date, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import alobo_pickle as ap  # noqa: E402

TODAY = date(2026, 10, 18)  # Chủ nhật


def test_parse_when():
    w = ap.parse_when("tối mai", TODAY)
    assert w.day == date(2026, 10, 19) and w.start == time(18)
    assert ap.parse_when("thứ 7", TODAY).day == date(2026, 10, 24)
    assert ap.parse_when("cuối tuần", TODAY).days == [date(2026, 10, 24), date(2026, 10, 25)]
    w = ap.parse_when("25/10 17h-21h", TODAY)
    assert w.day == date(2026, 10, 25) and (w.start, w.end) == (time(17), time(21))


def test_filter_pipeline(capsys):
    fixture = Path(__file__).with_name("sample_api.json")
    ap.main(["19/10/2026", "tối", "--from-file", str(fixture), "--json"])
    import json
    hits = json.loads(capsys.readouterr().out)
    names = {h["name"] for h in hits}
    # 1: newbie gần nhà; 3: máy bắn bóng gần nhà
    assert names == {"Xé vé giao lưu newbie", "Tập máy bắn bóng 1 người"}, names
