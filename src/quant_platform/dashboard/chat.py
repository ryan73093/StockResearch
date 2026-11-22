"""The AI chat window's server side (2026-11-22, D-058).

使用者 2026-11-22：「麻煩 AI 的部分用個對話小視窗好嗎？你看一下 VectorDB 那個專案怎麼弄的？右下角一個按鍵，然後彈出來就一個對話視窗。
不然你現在我發的問題，我完全不知道後端到底有沒有在回我事情。」 「問 AI」 on the knowledge page was a button and a wait of 30-50 s.

A question starts a *turn*: a worker thread of its own runs the knowledge base's steps (research/knowledge.py: ensure, semantic, evidence,
ask) and writes each step as it starts and ends, a heartbeat ("已等 N 秒") while a slow one runs, and the answer, as events. The page
follows the turn as a text/event-stream in VectorDB's format (``event: accepted|step|wait|delta|done|error`` then ``data: json``, an
``id:`` per event). The turn does not depend on the connection: a page change (this site is many pages) or a dropped connection does not
lose the answer — the next page follows the same turn again (``/research/chat/stream/<id>?after=N``) and every finished turn is kept
in ``instance/research/chat/thread.json``, the conversation every page shows. The model, its monthly budget, the answer cache (the same
question on an unchanged knowledge base is not charged again) and the prompt are knowledge.ask's, unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, jsonify, request

from quant_platform.container import _instance_dir

logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")

MAX_QUESTION = 500                     # knowledge.ask reads this many characters of a question
KEEP_TURNS = 40                        # turns the thread keeps
KEEP_LIVE = 8                          # finished turns whose events a page can still follow
HEARTBEAT_SECONDS = 2.0                # a slow step says how long it has been going on this often
KEEPALIVE_SECONDS = 15.0               # a comment line keeps a quiet connection open
FOLDER = "chat"
# 2026-11-22: the model answered in 15 s on the first real question (claude-haiku-5-5); the K-1 measurement of 10/22 (gpt) was 25-50 s
ASK_HINT = "通常 15～50 秒，會計費約 US$0.002"
INTERRUPTED = "網站重新啟動，這一題沒有回完。AI 如果已經答完會存著，再問一次同一題不會重複計費。"


@dataclass(frozen=True)
class Context:
    """What one turn needs from the site (the worker thread has no request)."""

    research: Path
    root: Path
    settings: object


# --- a turn --------------------------------------------------------------------------------
class Turn:
    """One question and everything the page is told while it is answered."""

    def __init__(self, question: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.question = question
        self.at = datetime.now(TAIPEI).isoformat(timespec="seconds")
        self.started = time.monotonic()
        self.events: list[tuple[str, dict]] = []
        self.steps: list[dict] = []        # what the thread keeps: key, label, state, note, seconds
        self.status = "running"            # running | done | error
        self.answer: dict | None = None
        self.error = ""
        self.seconds = 0.0
        self.cond = threading.Condition()

    @property
    def finished(self) -> bool:
        return self.status != "running"

    def emit(self, event: str, data: dict) -> None:
        with self.cond:
            self.events.append((event, data))
            self.cond.notify_all()

    def finish(self, status: str, event: str, data: dict) -> None:
        """The last event and the end of the turn in one step, so a page that has read everything knows it is over."""
        with self.cond:
            self.seconds = round(time.monotonic() - self.started, 1)
            self.status = status
            self.events.append((event, data))
            self.cond.notify_all()

    def step(self, key: str, label: str, hint: str = "") -> "Step":
        return Step(self, key, label, hint)

    def fail_running(self, note: str) -> None:
        """A step that was going when the turn broke is said to have failed, with why (not left spinning)."""
        for record in self.steps:
            if record["state"] == "running":
                record.update(state="failed", note=note[:200])
                self.emit("step", dict(record))

    def record(self, status: str | None = None) -> dict:
        """The turn as the thread file keeps it (``status``: how it ends, said before the page is told)."""
        seconds = self.seconds if self.finished else round(time.monotonic() - self.started, 1)
        return {"id": self.id, "q": self.question, "at": self.at, "status": status or self.status, "seconds": seconds,
                "steps": [dict(step) for step in self.steps], "answer": self.answer, "error": self.error}


class Step:
    """One thing the backend does: told when it starts and when it ends, with how long it took."""

    def __init__(self, turn: Turn, key: str, label: str, hint: str = "") -> None:
        self.turn, self.key, self.started = turn, key, time.monotonic()
        self.record = {"key": key, "label": label, "state": "running", "note": "", "seconds": 0.0}
        turn.steps.append(self.record)
        turn.emit("step", {**self.record, "hint": hint})

    def end(self, state: str, note: str = "") -> None:
        self.record.update(state=state, note=note, seconds=round(time.monotonic() - self.started, 1))
        self.turn.emit("step", dict(self.record))

    def done(self, note: str = "") -> None:
        self.end("done", note)

    def skip(self, note: str = "") -> None:
        self.end("skipped", note)

    def fail(self, note: str = "") -> None:
        self.end("failed", note)


def with_heartbeat(turn: Turn, step: Step, work, hint: str = ""):
    """``work()`` in a thread of its own; while it runs, the page is told every HEARTBEAT_SECONDS how long it has been going on
    (a step of 30 s that says nothing is what the owner could not tell from a hang)."""
    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["value"] = work()
        except Exception as exc:                          # noqa: BLE001 - handed back to the turn
            box["error"] = exc

    worker = threading.Thread(target=run, name="chat-step", daemon=True)
    worker.start()
    while True:
        worker.join(HEARTBEAT_SECONDS)
        if not worker.is_alive():
            break
        turn.emit("wait", {"key": step.key, "seconds": round(time.monotonic() - step.started), "hint": hint})
    if "error" in box:
        raise box["error"]                                # type: ignore[misc]
    return box.get("value")


# --- the steps of a question ---------------------------------------------------------------
def answer(turn: Turn, context: Context) -> dict:
    """Every step of one question; each is an event as it starts and again as it ends. The answer, for the caller to keep and say."""
    from quant_platform.research import knowledge
    from quant_platform.research import knowledge_vectors as vectors

    research, question = context.research, turn.question
    turn.emit("accepted", {"turn": turn.id, "question": question, "at": turn.at})

    step = turn.step("read", "讀研究知識庫", "紀錄有變時要重建，約幾秒")
    stale = ""
    try:
        built = with_heartbeat(turn, step, lambda: knowledge.ensure(context.root, research), "紀錄有變時要重建，約幾秒")
    except Exception as exc:                                # noqa: BLE001 - the last build still answers
        logger.warning("chat: the knowledge base could not be rebuilt: %s", exc)
        built, stale = knowledge.meta(research), "（重建失敗，用上一次建好的）"
    if not built.get("entries"):
        step.fail("知識庫是空的（還沒建好）")
        raise RuntimeError("研究知識庫還沒有任何紀錄，沒辦法回答")
    stamp = str(built.get("built_at") or "")[:16].replace("T", " ")
    step.done(f"{int(built['entries']):,} 筆紀錄" + (f"，{stamp} 更新" if stamp else "") + stale)

    loaded = "model" in getattr(vectors, "_ENCODER", {})
    hint = "" if loaded else "第一次要載入語意模型，約 10～20 秒"
    step = turn.step("meaning", "用意思找相關紀錄（語意搜尋）", hint)
    meaning = with_heartbeat(turn, step, lambda: knowledge.semantic(research, question, context.root), hint)
    if meaning is None:
        step.skip("語意搜尋沒有開（沒有向量或模型），只用關鍵字")
    else:
        step.done(f"找到 {len(meaning)} 筆")

    step = turn.step("search", "挑出最相關的紀錄")
    found = knowledge.evidence(research, question, meaning)
    step.done(f"挑 {len(found)} 筆給 AI 讀" if found else "沒有找到相關的紀錄")

    cached = knowledge.cached(research, question)
    step = turn.step("ask", "請 AI 整理成答案", ASK_HINT)
    client, ai = None, {"ready": bool(cached), "note": ""}
    if cached is None:
        client, ai = knowledge.ai_status(context.settings, research)
    if cached is not None:
        result = knowledge.ask(research, question, None, semantic_ids=meaning)
        step.done("同一題在知識庫沒變以前不重複計費，用上次的答案" + (f"（{str(cached.get('at') or '')[:16].replace('T', ' ')}）" if cached.get("at") else ""))
    elif client is None:
        result = knowledge.ask(research, question, None, semantic_ids=meaning)
        step.fail(str(ai.get("note") or "AI 沒有開，只能列出最相關的紀錄"))
    else:
        result = with_heartbeat(turn, step, lambda: knowledge.ask(research, question, client, semantic_ids=meaning),
                                ASK_HINT)
        if result.get("answer") or result.get("conclusion"):
            step.done(f"模型 {result.get('model') or ''}")
        else:
            step.fail(str(result.get("note") or "AI 沒有回答"))

    note = str(result.get("note") or "")
    if client is None and cached is None and ai.get("note"):
        note = str(ai["note"])                              # the exact reason: no key, or this month's budget is used up
    payload = {
        "conclusion": str(result.get("conclusion") or ""), "answer": str(result.get("answer") or ""),
        "citations": list(result.get("citations") or []), "note": note,
        "model": result.get("model"), "cached": cached is not None,
        "evidence": [{"id": entry["id"], "cite": entry.get("cite", ""), "title": entry["title"], "kind": entry["kind"],
                      "verdict": entry["verdict"], "date": entry.get("date") or "", "gist": knowledge.gist(entry, 120)}
                     for entry in result.get("evidence") or []],
        "search": "/research/knowledge?q=" + quote(question),
    }
    if payload["conclusion"]:
        turn.emit("delta", {"part": "conclusion", "text": payload["conclusion"]})
    for paragraph in (line for line in payload["answer"].split("\n") if line.strip()):
        turn.emit("delta", {"part": "answer", "text": paragraph})
    return payload


# --- the thread ----------------------------------------------------------------------------
class Store:
    """The conversation of one research folder: the finished turns in a file, the running one (and the last few) in memory."""

    def __init__(self, research: Path) -> None:
        self.path = Path(research) / FOLDER / "thread.json"
        self.lock = threading.Lock()
        self.live: dict[str, Turn] = {}
        self.running: Turn | None = None

    def _read(self) -> list[dict]:
        try:
            turns = json.loads(self.path.read_text(encoding="utf-8")).get("turns") or []
        except (OSError, ValueError, AttributeError):
            return []
        return [turn for turn in turns if isinstance(turn, dict) and turn.get("id")]

    def _write(self, turns: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_name(self.path.name + ".partial")
        partial.write_text(json.dumps({"turns": turns[-KEEP_TURNS:]}, ensure_ascii=False), encoding="utf-8")
        os.replace(partial, self.path)

    def _save(self, turn: Turn, status: str | None = None) -> None:
        with self.lock:
            turns = [item for item in self._read() if item["id"] != turn.id]
            turns.append(turn.record(status))
            self._write(turns)

    def start(self, question: str, context: Context) -> tuple[Turn | None, Turn | None]:
        """(the new turn, None), or (None, the turn that is still running): one question at a time (each one may cost money)."""
        with self.lock:
            if self.running is not None and not self.running.finished:
                return None, self.running
            turn = Turn(question)
            self.running = turn
            older = [key for key in self.live if key != turn.id]
            for key in older[:max(0, len(older) - (KEEP_LIVE - 1))]:          # the oldest finished ones leave memory
                if self.live[key].finished:
                    del self.live[key]
            self.live[turn.id] = turn
            turns = [item for item in self._read() if item["id"] != turn.id]
            turns.append(turn.record())
            self._write(turns)
        threading.Thread(target=self._run, args=(turn, context), name="chat-turn", daemon=True).start()
        return turn, None

    def _run(self, turn: Turn, context: Context) -> None:
        status, event, data = "error", "error", {}
        try:
            payload = answer(turn, context)
            turn.answer = payload
            status, event = "done", "done"
            data = {"answer": payload, "seconds": round(time.monotonic() - turn.started, 1)}
            logger.info("chat: answered %r in %.1f s", turn.question[:60], data["seconds"])
        except Exception as exc:                            # noqa: BLE001 - said in the window, and in the log
            logger.exception("chat: the question %r failed", turn.question[:60])
            text = str(exc).strip() or type(exc).__name__
            turn.error = f"這一題沒有回完：{text[:200]}（細節在 instance/web.stderr.log）"
            turn.fail_running(text)
            data = {"error": turn.error}
        try:
            self._save(turn, status)                        # kept before the page is told it is over
        except OSError:
            logger.exception("chat: the thread could not be saved")
        turn.finish(status, event, data)
        with self.lock:
            if self.running is turn:
                self.running = None

    def snapshot(self) -> dict:
        with self.lock:
            turns = self._read()
            live = set(self.live)
            running = self.running if self.running is not None and not self.running.finished else None
        shown = []
        for item in turns:
            if item.get("status") == "running" and item["id"] not in live:       # its worker died with the last process
                item = {**item, "status": "interrupted", "error": INTERRUPTED}
            shown.append(item)
        return {"turns": shown,
                "running": {"id": running.id, "question": running.question,
                            "seconds": round(time.monotonic() - running.started)} if running is not None else None}

    def clear(self) -> bool:
        with self.lock:
            if self.running is not None and not self.running.finished:
                return False
            self._write([])
            self.live.clear()
        return True

    def get(self, turn_id: str) -> Turn | None:
        with self.lock:
            return self.live.get(turn_id)


_STORES: dict[str, Store] = {}
_STORES_LOCK = threading.Lock()


def store_for(research: Path) -> Store:
    key = str(Path(research).resolve())
    with _STORES_LOCK:
        if key not in _STORES:
            _STORES[key] = Store(Path(research))
        return _STORES[key]


# --- the wire ------------------------------------------------------------------------------
def sse(event: str, data: dict, index: int | None = None) -> str:
    head = f"id: {index}\n" if index is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def follow(turn: Turn, after: int = 0):
    """The turn's events from ``after`` on, as they come; a comment line while it is quiet; ends when the turn has."""
    index = max(0, after)
    while True:
        with turn.cond:
            if index >= len(turn.events) and not turn.finished:
                turn.cond.wait(KEEPALIVE_SECONDS)
            batch = turn.events[index:]
            finished = turn.finished
        for offset, (event, data) in enumerate(batch):
            yield sse(event, data, index + offset)
        index += len(batch)
        if finished and not batch:
            return
        if not batch:
            yield ": keep-alive\n\n"


STREAM_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


def create_chat_blueprint(dependencies) -> Blueprint:
    blueprint = Blueprint("chat", __name__)
    settings = dependencies.settings

    def research_dir() -> Path:
        return _instance_dir(settings.database_url) / "research"

    @blueprint.app_context_processor
    def chat_examples() -> dict[str, object]:
        from quant_platform.research import knowledge

        return {"chat_examples": list(knowledge.EXAMPLES)}

    @blueprint.post("/research/chat")
    def ask():
        payload = request.get_json(silent=True)
        payload = payload if isinstance(payload, dict) else {}
        question = re.sub(r"\s+", " ", str(payload.get("question") or "")).strip()
        if not question:
            return jsonify({"error": "請輸入問題"}), 400
        if len(question) > MAX_QUESTION:
            return jsonify({"error": f"問題最多 {MAX_QUESTION} 字（現在 {len(question)} 字）"}), 400
        store = store_for(research_dir())
        # the root is the working directory, as the services run (scripts/run_local_services.ps1; v2._project_root)
        turn, busy = store.start(question, Context(research_dir(), Path.cwd(), settings))
        if turn is None:
            seconds = round(time.monotonic() - busy.started)
            return jsonify({"error": f"上一題還在處理（已等 {seconds} 秒），等它回完再問", "turn": busy.id, "running": True}), 409
        return Response(follow(turn), mimetype="text/event-stream", headers=STREAM_HEADERS)

    @blueprint.get("/research/chat/stream/<turn_id>")
    def stream(turn_id: str):
        turn = store_for(research_dir()).get(turn_id)
        if turn is None:
            return jsonify({"error": "這一題已經不在記憶體裡（網站重新啟動過），看對話紀錄"}), 404
        try:
            after = int(request.args.get("after") or 0)
        except ValueError:
            after = 0
        return Response(follow(turn, after), mimetype="text/event-stream", headers=STREAM_HEADERS)

    @blueprint.get("/research/chat/thread")
    def thread():
        return jsonify(store_for(research_dir()).snapshot())

    @blueprint.delete("/research/chat/thread")
    def clear():
        if not store_for(research_dir()).clear():
            return jsonify({"error": "有一題還在處理，等它回完再開新對話"}), 409
        return jsonify({"cleared": True})

    return blueprint
