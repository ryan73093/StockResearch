"""The YtSummary knowledge reader: what a decision at a moment may read (research/video_knowledge.py)."""

import json
from datetime import date, datetime

from quant_platform.research import video_knowledge as vk

TAIPEI = vk.TAIPEI


def record(id_: str, available: str, published: str, revision: int = 1, **extra) -> dict:
    base = {"schema_version": 1, "id": id_, "kind": "video", "topic": "財經", "topics": ["財經"],
            "title": f"影片 {id_}", "url": "https://www.youtube.com/watch?v=x", "source": "宏爺講股",
            "published_at": published, "available_at": available, "one_line": "台股反彈", "summary": "摘要",
            "tickers": [{"code": "2330", "name": "台積電", "stance": "bullish"}], "themes": ["半導體"],
            "revision": revision}
    return {**base, **extra}


def write(root, day: str, rows: list[dict], tail: str = "") -> None:
    folder = root / "knowledge"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows) + tail, encoding="utf-8")


def test_only_records_available_by_the_decision_moment_are_read(tmp_path):
    write(tmp_path, "2026-11-20", [
        record("a", "2026-11-20T06:00:00+00:00", "2026-11-20T05:30:00+00:00"),       # 14:00 Taipei
        record("b", "2026-11-20T08:00:00+00:00", "2026-11-20T07:00:00+00:00"),       # 16:00, after 15:40
        record("old", "2026-11-20T06:10:00+00:00", "2026-07-01T00:00:00+00:00"),     # old video summarised today
    ], tail='{"schema_version": 1, "id": "half')                                     # a line being written
    rows = vk.entries(vk.decision_moment(date(2026, 11, 20)), path=tmp_path)
    assert [row["id"] for row in rows] == ["a"]
    later = vk.entries(datetime(2026, 11, 20, 17, 0, tzinfo=TAIPEI), path=tmp_path)
    assert [row["id"] for row in later] == ["b", "a"]


def test_a_revision_is_read_as_it_was_known(tmp_path):
    write(tmp_path, "2026-11-19", [record("a", "2026-11-19T06:00:00+00:00", "2026-11-19T05:00:00+00:00")])
    write(tmp_path, "2026-11-20", [record("a", "2026-11-20T09:00:00+00:00", "2026-11-19T05:00:00+00:00", 2,
                                          one_line="改寫後")])
    before = vk.entries(vk.decision_moment(date(2026, 11, 20)), path=tmp_path)
    after = vk.entries(datetime(2026, 11, 20, 18, 0, tzinfo=TAIPEI), path=tmp_path)
    assert [(r["id"], r["revision"]) for r in before] == [("a", 1)]
    assert [(r["id"], r["revision"], r["one_line"]) for r in after] == [("a", 2, "改寫後")]


def test_filters_by_code_topic_kind_and_keyword(tmp_path):
    write(tmp_path, "2026-11-20", [
        record("tsmc", "2026-11-20T01:00:00+00:00", "2026-11-20T00:30:00+00:00"),
        record("ai", "2026-11-20T01:00:00+00:00", "2026-11-20T00:40:00+00:00", kind="news", topic="AI",
               topics=["AI", "科技"], tickers=[{"code": "NVDA", "name": "輝達", "stance": "bearish"}],
               title="新晶片禁令"),
    ])
    moment = vk.decision_moment(date(2026, 11, 20))
    assert [r["id"] for r in vk.entries(moment, codes=["2330"], path=tmp_path)] == ["tsmc"]
    assert [r["id"] for r in vk.entries(moment, codes=["輝達"], path=tmp_path)] == ["ai"]
    assert [r["id"] for r in vk.entries(moment, topics=["AI"], path=tmp_path)] == ["ai"]
    assert vk.entries(moment, topics=["科技"], path=tmp_path) == []      # 科技 is only a secondary topic there
    assert [r["id"] for r in vk.entries(moment, kinds=["video"], path=tmp_path)] == ["tsmc"]
    assert [r["id"] for r in vk.entries(moment, keyword="禁令", path=tmp_path)] == ["ai"]
    text = vk.brief(vk.entries(moment, path=tmp_path))
    assert "[新聞｜AI 技術]" in text and "[影片｜財經投資]" in text and "NVDA 輝達 看空" in text and "2330 台積電 看多" in text
    assert vk.brief([record("hn", "2026-11-20T01:00:00+00:00", "2026-11-20T00:00:00+00:00", kind="discussion",
                            source="Hacker News")]).startswith("[論壇｜財經投資]")
    assert vk.brief([record("arena", "2026-11-20T01:00:00+00:00", "2026-11-20T00:00:00+00:00", kind="trend",
                            source="Arena 模型排行")]).startswith("[AI 雷達｜")


def test_digest_is_read_only_after_it_was_written_and_a_missing_folder_is_empty(tmp_path):
    folder = tmp_path / "digests"
    folder.mkdir()
    (folder / "2026-11-20.json").write_text(json.dumps({
        "schema_version": 1, "digest_date": "2026-11-20", "available_at": "2026-11-19T23:31:00+00:00",
        "content_md": "# 每日知識摘要", "refs": []}), encoding="utf-8")
    assert vk.digest(date(2026, 11, 20), path=tmp_path)["content_md"] == "# 每日知識摘要"
    assert vk.digest(date(2026, 11, 20), datetime(2026, 11, 20, 7, 0, tzinfo=TAIPEI), path=tmp_path) is None
    assert vk.entries(path=tmp_path / "missing") == []
    assert vk.digest(date(2026, 11, 20), path=tmp_path / "missing") is None
