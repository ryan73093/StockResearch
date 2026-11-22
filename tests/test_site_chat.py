"""2026-11-22 (D-058; 使用者：AI 的部分用個對話小視窗…右下角一個按鍵…不然我完全不知道後端到底有沒有在回我事情): the AI chat window.

The button and the window are on every page; a question is answered in a worker thread of its own, told to the page as a
text/event-stream (accepted, step, wait, delta, done, error); the conversation is kept in the research folder; the model, its
budget and the answer cache are knowledge.ask's (a fake client stands in for the model here)."""
import json
import time
from types import SimpleNamespace

import pytest

from quant_platform.config.settings import Settings
from quant_platform.container import build_container
from quant_platform.dashboard import chat
from quant_platform.dashboard.app import create_app
from quant_platform.research import knowledge

RESULTS = """# 研究：結果紀錄

## 7. 結論

### 停損（2026-10-05，跑之前寫）

- 做法：趨勢規則加 15% 移動停損（`stoploss`）。
- 結果：交易筆數變兩倍，期末輸 0050，回撤也沒有變小，**沒過**。下一版：看持股本身的波動。

### 開盤賣做不做得到（2026-10-22，跑之前寫）

- 判定：09:10 賣的好處至少是開盤的一半。
"""


class FakeClient:
    model = "fake-model"

    def __init__(self, delay=0.0, boom=None):
        self.delay, self.boom, self.calls = delay, boom, 0

    def json_call(self, instructions, user_input, operation, context=""):
        self.calls += 1
        time.sleep(self.delay)
        if self.boom:
            raise RuntimeError(self.boom)
        return {"conclusion": "試過而且失敗", "answer": "停損讓交易變兩倍 [K1]\n回撤也沒有變小 [K1]", "citations": ["K1"]}, {}


def ready(client):
    return lambda settings, research: (client, {"ready": True, "note": "問 AI 每題約 US$0.002"})


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "docs" / "research").mkdir(parents=True)
    (tmp_path / "docs" / "research" / "results.md").write_text(RESULTS, encoding="utf-8")
    (tmp_path / "docs" / "decisions.md").write_text("# 決定登記簿\n\n## D-001 策略一律 100% 個股\n\n> 0050 只當比較基準。\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)                                    # the services' working directory is the project root
    monkeypatch.setattr(knowledge, "semantic", lambda research, question, root=None: None)     # no vectors in a test
    client = FakeClient()
    monkeypatch.setattr(knowledge, "ai_status", ready(client))
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'instance' / 'chat.db'}", scheduler_in_web=False)
    app = create_app(build_container(settings))
    return SimpleNamespace(client=app.test_client(), research=tmp_path / "instance" / "research", root=tmp_path,
                           settings=settings, model=client)


def events_of(response):
    """The (event, data) pairs of a text/event-stream body."""
    pairs = []
    for block in response.get_data(as_text=True).split("\n\n"):
        lines = block.strip().splitlines()
        event = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if event and data:
            pairs.append((event, json.loads(data)))
    return pairs


def ask(site, question):
    response = site.client.post("/research/chat", json={"question": question})
    return response, events_of(response)


def steps_of(events):
    return [(data["key"], data["state"]) for name, data in events if name == "step"]


def test_every_page_carries_the_button_and_the_window(site):
    for path in ("/", "/holdings", "/plan", "/research", "/research/knowledge", "/system"):
        body = site.client.get(path).get_data(as_text=True)
        assert 'id="chat-launcher"' in body and 'aria-label="開啟 AI 對話"' in body, path
        assert 'id="chat-window"' in body and 'role="dialog"' in body and 'aria-live="polite"' in body, path
        assert "css/chat.css" in body and "js/chat.js" in body, path
        assert "停損有沒有效？" in body                              # the examples of the empty window (knowledge.EXAMPLES)
    page = site.client.get("/research/knowledge").get_data(as_text=True)
    assert "data-chat-ask" in page and 'name="ai"' in page          # the button opens the window; without the script it still submits ai=1


def test_a_question_is_answered_step_by_step_and_kept(site):
    response, events = ask(site, "停損有沒有效？")
    assert response.status_code == 200 and response.mimetype == "text/event-stream"
    names = [name for name, _ in events]
    assert names[0] == "accepted" and names[-1] == "done" and "delta" in names
    steps = steps_of(events)
    assert steps[0] == ("read", "running") and ("search", "done") in steps and steps[-1] == ("ask", "done")
    answer = events[-1][1]["answer"]
    assert answer["conclusion"] == "試過而且失敗" and answer["citations"] == ["K1"] and answer["model"] == "fake-model"
    assert answer["evidence"][0]["cite"] == "K1" and answer["evidence"][0]["title"].startswith("停損") and answer["cached"] is False
    deltas = [data for name, data in events if name == "delta"]
    assert deltas[0] == {"part": "conclusion", "text": "試過而且失敗"} and [d["part"] for d in deltas[1:]] == ["answer", "answer"]
    assert [int(line[4:]) for line in response.get_data(as_text=True).splitlines() if line.startswith("id: ")] == list(range(len(events)))
    # the thread has it the moment the last event is out
    thread = site.client.get("/research/chat/thread").get_json()
    assert thread["running"] is None and [turn["q"] for turn in thread["turns"]] == ["停損有沒有效？"]
    assert thread["turns"][0]["status"] == "done" and thread["turns"][0]["answer"]["conclusion"] == "試過而且失敗"
    assert [step["state"] for step in thread["turns"][0]["steps"]] == ["done", "skipped", "done", "done"]
    # the same question on the same knowledge base: the kept answer, no second call to the model
    _, again = ask(site, "停損有沒有效？")
    assert again[-1][1]["answer"]["cached"] is True and site.model.calls == 1
    assert "不重複計費" in dict(((d["key"], d["state"]), d["note"]) for n, d in again if n == "step")[("ask", "done")]


def test_without_an_ai_the_window_says_why_and_lists_the_records(site, monkeypatch):
    monkeypatch.setattr(knowledge, "ai_status", lambda settings, research: (None, {"ready": False, "note": "沒有設定 AI API 平台的專案金鑰：只能搜尋紀錄。"}))
    _, events = ask(site, "停損有沒有效？")
    assert events[-1][0] == "done" and not any(name == "delta" for name, _ in events)
    answer = events[-1][1]["answer"]
    assert answer["answer"] == "" and "專案金鑰" in answer["note"] and answer["evidence"][0]["title"].startswith("停損")
    failed = [data for name, data in events if name == "step" and data["state"] == "failed"]
    assert failed[-1]["key"] == "ask" and "專案金鑰" in failed[-1]["note"]


def test_a_model_that_fails_is_said_in_the_window_with_the_records_beside_it(site, monkeypatch):
    monkeypatch.setattr(knowledge, "ai_status", ready(FakeClient(boom="本月預算用完")))
    _, events = ask(site, "停損有沒有效？")
    answer = events[-1][1]["answer"]
    assert events[-1][0] == "done" and answer["answer"] == "" and "本月預算用完" in answer["note"] and answer["evidence"]
    assert steps_of(events)[-1] == ("ask", "failed")


def test_a_backend_that_breaks_ends_with_an_error_the_window_shows(site, monkeypatch):
    def broken(research, question, semantic_ids=None):
        raise RuntimeError("索引壞了")

    monkeypatch.setattr(knowledge, "evidence", broken)
    response, events = ask(site, "停損有沒有效？")
    assert events[-1][0] == "error" and "索引壞了" in events[-1][1]["error"]
    failed = [data for name, data in events if name == "step" and data["state"] == "failed"]
    assert failed[-1]["key"] == "search" and "索引壞了" in failed[-1]["note"]            # the step that was going, not left spinning
    thread = site.client.get("/research/chat/thread").get_json()
    assert thread["turns"][0]["status"] == "error" and "索引壞了" in thread["turns"][0]["error"]
    # and the window can ask again
    monkeypatch.undo()
    assert ask(site, "停損有沒有效？")[1][-1][0] == "done"


def test_a_slow_step_says_how_long_it_has_been_going_on(site, monkeypatch):
    monkeypatch.setattr(chat, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(knowledge, "ai_status", ready(FakeClient(delay=0.4)))
    _, events = ask(site, "停損有沒有效？")
    waits = [data for name, data in events if name == "wait"]
    assert waits and all(data["key"] == "ask" for data in waits) and "15～50 秒" in waits[0]["hint"]
    assert events[-1][0] == "done"


def test_one_question_at_a_time_and_a_page_can_follow_a_running_turn_again(site, monkeypatch):
    monkeypatch.setattr(chat, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(knowledge, "ai_status", ready(FakeClient(delay=0.6)))
    store = chat.store_for(site.research)
    turn, busy = store.start("停損有沒有效？", chat.Context(site.research, site.root, site.settings))
    assert turn is not None and busy is None
    second = site.client.post("/research/chat", json={"question": "另一題"})
    assert second.status_code == 409 and second.get_json()["turn"] == turn.id and second.get_json()["running"] is True
    assert site.client.delete("/research/chat/thread").status_code == 409                  # not while one is running
    running = site.client.get("/research/chat/thread").get_json()
    assert running["running"]["id"] == turn.id and running["turns"][0]["status"] == "running"
    followed = events_of(site.client.get(f"/research/chat/stream/{turn.id}?after=3"))     # the next page, from where it was
    assert followed[-1][0] == "done" and any(name == "wait" for name, _ in followed)
    assert site.client.delete("/research/chat/thread").get_json() == {"cleared": True}
    assert site.client.get("/research/chat/thread").get_json() == {"turns": [], "running": None}


def test_the_questions_that_cannot_be_asked_are_told_why(site):
    assert site.client.post("/research/chat", json={"question": "   "}).status_code == 400
    assert site.client.post("/research/chat", json={}).status_code == 400
    assert site.client.post("/research/chat", data="question=abc").status_code == 400      # not JSON
    long = site.client.post("/research/chat", json={"question": "停" * (chat.MAX_QUESTION + 1)})
    assert long.status_code == 400 and str(chat.MAX_QUESTION) in long.get_json()["error"]
    assert site.client.get("/research/chat/stream/not-a-turn").status_code == 404
    assert site.client.get("/research/chat/thread").get_json() == {"turns": [], "running": None}


def test_a_turn_whose_process_ended_is_shown_as_interrupted(site):
    path = site.research / "chat" / "thread.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"turns": [{"id": "dead", "q": "停損？", "at": "2026-11-22T17:00:00+08:00", "status": "running",
                                           "seconds": 0, "steps": [], "answer": None, "error": ""}]}), encoding="utf-8")
    thread = site.client.get("/research/chat/thread").get_json()
    assert thread["running"] is None and thread["turns"][0]["status"] == "interrupted" and "重新啟動" in thread["turns"][0]["error"]
    assert ask(site, "停損有沒有效？")[1][-1][0] == "done"                                   # nothing is stuck by it


def test_the_thread_keeps_the_last_turns_only(site):
    store = chat.store_for(site.research)
    store._write([{"id": str(number), "q": "q", "status": "done"} for number in range(chat.KEEP_TURNS + 10)])
    kept = store._read()
    assert len(kept) == chat.KEEP_TURNS and kept[-1]["id"] == str(chat.KEEP_TURNS + 9)


def test_the_wire_format_and_a_quiet_connection(monkeypatch):
    assert chat.sse("step", {"a": "停"}, 3) == 'id: 3\nevent: step\ndata: {"a": "停"}\n\n'
    assert chat.sse("done", {}) == "event: done\ndata: {}\n\n"
    monkeypatch.setattr(chat, "KEEPALIVE_SECONDS", 0.05)
    turn = chat.Turn("停損？")
    stream = chat.follow(turn)
    assert next(stream) == ": keep-alive\n\n"                                        # nothing yet: a comment keeps the line open
    turn.emit("step", {"key": "read"})
    turn.finish("done", "done", {})
    assert [item.split("\n")[1] for item in stream] == ["event: step", "event: done"]
    assert list(chat.follow(turn, after=99)) == []                                   # a finished turn followed from its end: nothing


def test_the_knowledge_pages_questions_open_the_window(site):
    knowledge.build(site.root, site.research)
    knowledge.ask(site.research, "停損有沒有效？", site.model)
    page = site.client.get("/research/knowledge").get_data(as_text=True)
    assert 'data-chat-ask data-chat-question="停損有沒有效？"' in page and "ai=1" in page           # re-asked in the window; the link still works
