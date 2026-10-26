"""2026-10-22 (使用者：我所有的失敗紀錄都要記起來，作為知識庫，讓後面的人可以用 AI 問他的決策、確認可不可行、回測狀況):
research/knowledge.py — the entries from every record, the search, the model's answer from the entries only, the daily
check that every finished experiment has its result written."""
import json

from quant_platform.research import knowledge
from quant_platform.research.registry import TrialRegistry

METHOD = """# 研究方法

## 7. 結論

### 最佳化紀錄（網站研究總覽「最佳化紀錄」分頁顯示；最新在上）

| 日期 | 家族 | 做了什麼 | 結果 | 判定 |
|---|---|---|---|---|
| 2026-10-20 | 強化學習 | 機器人 3.2：加跌市穩槽 | 2015-06 起輸 0050 | 沒過 |
| 2026-10-22 | 成交時點 | 開盤賣、收盤買 | 63% 錢變多 | 過 |

### 停損（2026-10-05，跑之前寫）

- 做法：趨勢規則加 15% 移動停損（`stoploss`）。
- 結果：交易筆數變兩倍，期末輸 0050，回撤也沒有變小，**沒過**。下一版：看持股本身的波動。

### 開盤賣做不做得到（2026-10-22，跑之前寫）

- 判定：09:10 賣的好處至少是開盤的一半。

**籌碼因子（R13，2026-10-04）**：26 個規則全部是 T3，籌碼每天變、前 20 名一直換。

## 8. 已淘汰的方向與原因

| 方向 | 原因 |
|---|---|
| 定期不定額（均線、回撤倍數） | 閒置現金拖累，3 年勝率 7%～65% |
"""

AGENTS = """# 入口

## 已核定方向（2026-09-30）

- 策略一律 100% 個股（使用者 2026-10-09）：0050 只當比較基準。
- 回覆一律繁體中文。

## 研究規範

- 不在這裡。
"""


def _root(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "research_method.md").write_text(METHOD, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(AGENTS, encoding="utf-8")
    (tmp_path / "DEVELOPMENT_HISTORY.md").write_text("# 歷程\n\n## 2026-10-22 上午 — 知識庫\n\n- 建了研究知識庫，失敗紀錄都進去（`kb`）。\n",
                                                    encoding="utf-8")
    (tmp_path / "REQUIREMENTS.md").write_text("# 需求\n\n## §7 研究\n\n- 2015-06 起挑規則，最終驗證期只能評估一次，這一段要夠長才會成為一筆。\n",
                                              encoding="utf-8")
    research = tmp_path / "instance" / "research"
    research.mkdir(parents=True)
    registry = TrialRegistry(research / "trials.jsonl")
    base = {"engine": "daily-1.5.0", "start": "2015-06-01", "end": "2026-09-30", "contributed": 1_660_000.0,
            "benchmark_final_value": 9_800_000.0, "benchmark_max_drawdown": -0.34, "since_2020_final_value": 2_000_000.0,
            "since_2020_benchmark_final_value": 3_700_000.0, "windows": {"1y": {"win_ratio": 0.4}, "3y": {"win_ratio": 0.3}}}
    registry.register(kind="candidate", period="recent", spec_hash="a" * 64, spec_name="每天 RSI（14 日）：前 20 名",
                      input_hash="1" * 64, data_fingerprint="d", report_file="r1.json",
                      metrics={**base, "final_value": 4_000_000.0, "max_drawdown": -0.55, "full_period_excess": -1.2,
                               "since_2020_excess": -0.8, "reasons": ["2015-06 起輸 0050（-120%）"]})
    return tmp_path, research


def test_the_records_become_entries(tmp_path):
    root, research = _root(tmp_path)
    built = knowledge.build(root, research)
    rows = knowledge.entries(research)
    kinds = built["kinds"]
    assert kinds["最佳化紀錄"] == 2 and kinds["淘汰方向"] == 1 and kinds["試驗"] == 1 and kinds["使用者決定"] == 2
    by_title = {row["title"]: row for row in rows}
    assert by_title["強化學習：機器人 3.2：加跌市穩槽"]["verdict"] == "失敗"
    assert by_title["成交時點：開盤賣、收盤買"]["verdict"] == "通過"
    stop = by_title["停損（2026-10-05，跑之前寫）"]
    assert stop["verdict"] == "失敗" and stop["date"] == "2026-10-05" and stop["judged"]
    assert by_title["開盤賣做不做得到（2026-10-22，跑之前寫）"]["verdict"] == "事先登錄"
    assert by_title["籌碼因子（R13，2026-10-04）"]["verdict"] == "失敗"            # a bold-headed paragraph
    trial = next(row for row in rows if row["kind"] == "試驗")
    assert trial["id"] == "T-1" and trial["verdict"] == "失敗" and "2015-06 起輸 0050" in trial["text"]
    assert "400 萬" in trial["text"] and "980 萬" in trial["text"]
    assert {row["title"] for row in knowledge.failures(research)} >= {"強化學習：機器人 3.2：加跌市穩槽", "定期不定額（均線、回撤倍數）"}
    assert not knowledge.stale(root, research)
    (root / "AGENTS.md").write_text(AGENTS + "\n", encoding="utf-8")
    assert knowledge.stale(root, research)


def test_the_search_finds_the_failure_and_a_trial_by_number(tmp_path):
    root, research = _root(tmp_path)
    knowledge.build(root, research)
    found = knowledge.search(research, "停損有沒有效")
    assert found[0]["title"].startswith("停損")
    assert knowledge.search(research, "#1 的回測")[0]["id"] == "T-1"
    assert knowledge.search(research, "RSI", kinds=("試驗",))[0]["id"] == "T-1"
    assert knowledge.search(research, "停損", verdicts=("通過",)) == []
    assert "期末輸 0050" in knowledge.gist(found[0])


class FakeClient:
    model = "fake-model"

    def __init__(self):
        self.calls = 0

    def json_call(self, instructions, user_input, operation, context=""):
        self.calls += 1
        assert operation == knowledge.OPERATION and "只能根據" in instructions and "[K1]" in user_input
        return {"conclusion": "試過而且失敗", "answer": "停損讓交易變兩倍、期末輸 0050 [K1]", "citations": ["K1"]}, {}


def test_the_model_answers_from_the_entries_and_a_repeat_costs_nothing(tmp_path):
    root, research = _root(tmp_path)
    knowledge.build(root, research)
    plain = knowledge.ask(research, "停損有沒有效？")
    assert plain["model"] is None and "沒有設定 AI" in plain["note"] and plain["evidence"][0]["cite"] == "K1"
    client = FakeClient()
    answer = knowledge.ask(research, "停損有沒有效？", client)
    assert answer["conclusion"] == "試過而且失敗" and answer["citations"] == ["K1"] and client.calls == 1
    again = knowledge.ask(research, "停損有沒有效？", client)
    assert again["conclusion"] == "試過而且失敗" and client.calls == 1               # read back, not asked again
    assert knowledge.recent_questions(research)[0]["question"] == "停損有沒有效？"

    class Broken(FakeClient):
        def json_call(self, *args, **kwargs):
            raise RuntimeError("本月預算用完")

    failed = knowledge.ask(research, "籌碼因子有用嗎？", Broken())
    assert "本月預算用完" in failed["note"] and failed["evidence"]


def test_every_finished_experiment_needs_its_result_written(tmp_path):
    root, research = _root(tmp_path)
    (root / "scripts").mkdir()
    (root / "scripts" / "research-pipeline.ps1").write_text(
        'switch ($Name) {\n    "kb" {\n        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "kb")\n    }\n'
        '    "lost" {\n        Invoke-Step "1-scan" @("-m", "quant_platform.research", "scan-large")\n    }\n'
        '    "download" {\n        Invoke-Step "1" @("-m", "quant_platform.research.history", "finmind")\n    }\n}\n', encoding="utf-8")
    assert set(knowledge.experiment_pipelines((root / "scripts" / "research-pipeline.ps1").read_text(encoding="utf-8"))) == {"kb", "lost"}
    (research / "pipelines").mkdir()
    for name, finished in (("kb", "2026-10-22T10:00:00"), ("lost", "2026-10-22T11:00:00"), ("download", "2026-10-22T11:00:00")):
        (research / "pipelines" / f"{name}.json").write_text(json.dumps({"name": name, "status": "done", "finished_at": finished}),
                                                            encoding="utf-8")
    assert knowledge.unrecorded(root, research) == ["lost"]


def test_the_knowledge_page_answers_and_lists_the_failures(tmp_path):
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'kb.db'}"))
    client = create_app(container).test_client()
    body = client.get("/research/knowledge").get_data(as_text=True)
    assert "研究知識庫" in body and "失敗紀錄" in body and "欄位說明" in body
    assert client.get("/research/knowledge?q=停損").status_code == 200
    assert client.get("/research/knowledge.json?q=停損").get_json()["question"] == "停損"
    assert client.get("/research/knowledge/X-0000000000").status_code == 404


def test_an_edit_above_a_record_keeps_its_id(tmp_path):
    root, research = _root(tmp_path)
    knowledge.build(root, research)
    before = {row["title"]: row["id"] for row in knowledge.entries(research) if row["kind"] == "研究紀錄"}
    path = root / "docs" / "research_method.md"
    path.write_text(path.read_text(encoding="utf-8").replace("## 7. 結論\n", "## 7. 結論\n\n新增的一行。\n\n另一行。\n"),
                    encoding="utf-8")
    knowledge.build(root, research)
    after = {row["title"]: row["id"] for row in knowledge.entries(research) if row["kind"] == "研究紀錄"}
    assert after["停損（2026-10-05，跑之前寫）"] == before["停損（2026-10-05，跑之前寫）"]


def test_the_vectors_go_to_the_shared_server_when_one_is_set(tmp_path, monkeypatch):
    """2026-10-26 (使用者：向量資料庫吃 C:\Dev\DBQdrant): the server's address and key from the settings, this project's
    collection name there; without them the embedded store as before."""
    from quant_platform.research import knowledge_vectors

    monkeypatch.delenv("KNOWLEDGE_QDRANT_URL", raising=False)
    assert knowledge_vectors.server() is None and knowledge_vectors.collection() == knowledge_vectors.COLLECTION
    key = tmp_path / "api-key.txt"
    key.write_text("secret-value\n", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_QDRANT_URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("KNOWLEDGE_QDRANT_KEY_FILE", str(key))
    assert knowledge_vectors.server() == ("http://127.0.0.1:6333", "secret-value")
    assert knowledge_vectors.collection() == "stockresearch_knowledge_bge_m3_v1"
