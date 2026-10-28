"""2026-10-22 (使用者：向量資料庫可用 BGE-M3、Qdrant): the knowledge base's vectors in an embedded Qdrant — only changed
entries encoded, gone ones deleted, the dense and sparse candidates fused; the keywords and the meaning fused in the
search. A small stand-in encoder replaces BGE-M3 (the real model is 2 GB)."""
import numpy as np
import pytest

from quant_platform.research import knowledge, knowledge_vectors

pytest.importorskip("qdrant_client")


class Encoder:
    """Words to fixed directions: a text's dense vector is the sum of its words', its sparse weights the words."""
    path, device = "stand-in", "cpu"
    WORDS = {"停損": 0, "回撤": 1, "月營收": 2, "深度學習": 3, "官股": 4}

    def __init__(self):
        self.calls = 0

    def encode(self, texts, max_length=0, batch=8):
        self.calls += len(texts)
        dense = np.zeros((len(texts), knowledge_vectors.DIMENSION), dtype=np.float32)
        sparse = []
        for row, text in enumerate(texts):
            weights = {}
            for word, slot in self.WORDS.items():
                if word in text:
                    dense[row, slot] = 1.0
                    weights[slot + 10] = 1.0
            dense[row, 999] = 0.01                            # never all zero
            dense[row] /= np.linalg.norm(dense[row])
            sparse.append(weights)
        return dense, sparse


def _entries():
    return [{"id": "R-1", "kind": "研究紀錄", "title": "停損", "text": "趨勢規則加移動停損，期末輸 0050，回撤沒有變小", "verdict": "失敗"},
            {"id": "R-2", "kind": "研究紀錄", "title": "月營收", "text": "月營收加速做規則全部 T3", "verdict": "失敗"},
            {"id": "R-3", "kind": "研究紀錄", "title": "深度學習", "text": "深度學習前一成 6,750 萬", "verdict": "通過"}]


def test_only_changed_entries_are_encoded_and_the_meaning_finds_them(tmp_path):
    model = Encoder()
    rows = _entries()
    first = knowledge_vectors.index(tmp_path, rows, model)
    assert first["points"] == 3 and first["encoded"] == 3 and model.calls == 3
    again = knowledge_vectors.index(tmp_path, rows, model)
    assert again["encoded"] == 0 and model.calls == 3                       # nothing changed, nothing encoded
    rows[1] = {**rows[1], "text": "月營收加速做規則全部 T3，回撤也深"}
    changed = knowledge_vectors.index(tmp_path, rows[:2], model)            # R-3 gone, R-2 changed
    assert changed["encoded"] == 1 and changed["deleted"] == 1 and changed["points"] == 2
    assert knowledge_vectors.query(tmp_path, "停損有沒有用", model)[0] == "R-1"
    assert knowledge_vectors.status(tmp_path)["points"] == 2


def test_the_keywords_and_the_meaning_are_fused(tmp_path):
    from test_research_knowledge import _root

    root, research = _root(tmp_path)
    knowledge.build(root, research)
    plain = knowledge.search(research, "停損")
    meaning = [plain[-1]["id"]] if len(plain) > 1 else []
    fused = knowledge.search(research, "停損", semantic_ids=meaning)
    assert {entry["id"] for entry in fused} >= {entry["id"] for entry in plain}
    only_meaning = knowledge.search(research, "完全不相干的字", semantic_ids=["T-1"])
    assert only_meaning[0]["id"] == "T-1"                                   # found by meaning alone
    assert knowledge.semantic(research, "停損") is None                      # no vectors built: keywords alone


def test_the_model_folder_is_found_without_downloading(tmp_path, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_BGE_MODEL", raising=False)
    assert knowledge_vectors.model_path(tmp_path / "project") is None
    folder = tmp_path / "VectorDB" / "models" / "bge-m3"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    (folder / "sparse_linear.pt").write_bytes(b"x")
    assert knowledge_vectors.model_path(tmp_path / "project") == folder       # the sibling project's copy
    monkeypatch.setenv("KNOWLEDGE_BGE_MODEL", str(folder))
    assert knowledge_vectors.model_path(tmp_path / "elsewhere") == folder


def test_a_relative_project_folder_still_finds_the_sibling_model(tmp_path, monkeypatch):
    """The worker passes the instance folder's parent as "." — resolved first, its parent is the projects folder."""
    monkeypatch.delenv("KNOWLEDGE_BGE_MODEL", raising=False)
    folder = tmp_path / "VectorDB" / "models" / "bge-m3"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    (folder / "sparse_linear.pt").write_bytes(b"x")
    (tmp_path / "StockResearch").mkdir()
    monkeypatch.chdir(tmp_path / "StockResearch")
    assert knowledge_vectors.model_path(".") == folder


def test_a_slow_server_is_tried_again(monkeypatch):
    """2026-10-29: the shared server timed out now and then under load; the rebuild pauses and tries again."""
    from qdrant_client.http.exceptions import ResponseHandlingException

    monkeypatch.setattr(knowledge_vectors.time, "sleep", lambda seconds: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ResponseHandlingException(TimeoutError("timed out"))
        return "stored"

    assert knowledge_vectors._patient(flaky) == "stored" and len(calls) == 3

    def down():
        raise ResponseHandlingException(TimeoutError("timed out"))

    with pytest.raises(ResponseHandlingException):          # still down after the last try: the rebuild fails as before
        knowledge_vectors._patient(down)
