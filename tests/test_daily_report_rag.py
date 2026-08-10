import sqlite3
from datetime import UTC, datetime

from quant_platform.application.daily_report import DailyReportKnowledgeService
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.rag import OpenAIResponsesAnswerProvider, chunk_text
from quant_platform.domain.entities import KnowledgeDocument


class FakeAnswerProvider:
    name = "openai"
    model = "fake-response-model"

    def __init__(self, response: str) -> None:
        self.response = response

    def answer(self, question, evidence):
        return self.response


def test_daily_report_builds_knowledge_and_grounded_answer(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'reports.db'}"))
    report = container.daily_report_knowledge_service.generate("TW")
    overview = container.daily_report_knowledge_service.overview("為什麼今天沒有候選？")

    assert report.id is not None
    assert "每日量化研究報告" in report.title
    assert overview.document_count >= 1
    assert overview.answer is not None
    assert overview.answer.evidence
    assert overview.answer.grounded
    assert "[來源1]" in overview.answer.answer
    assert overview.chunk_count >= 1
    assert overview.embedding_dimensions == 384
    assert container.daily_report_knowledge_service.sync_index().changed_chunk_count == 0

    audits = container.daily_report_knowledge_service._repository.list_query_audits()
    assert len(audits) == 1
    assert audits[0].grounded is True
    assert audits[0].latency_ms >= 0
    assert audits[0].answer_provider == "local"


def test_rag_refuses_without_evidence_and_audits_reason(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'refusal.db'}"))
    container.daily_report_knowledge_service.generate("TW")

    answer = container.daily_report_knowledge_service.ask("xylophonequasar")

    assert not answer.grounded
    assert not answer.evidence
    assert answer.refusal_reason
    assert "不會臆測" in answer.answer


def test_external_answer_requires_valid_source_marker(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'citation.db'}"))
    service = container.daily_report_knowledge_service
    service.generate("TW")
    service._answer_provider = FakeAnswerProvider("沒有引用的回答")

    fallback = service.ask("為什麼今天沒有候選？")
    assert "已降級" in fallback.mode
    assert "[來源1]" in fallback.answer

    service._answer_provider = FakeAnswerProvider("目前證據不足，因此維持觀察。[來源1]")
    generated = service.ask("為什麼今天沒有候選？")
    assert generated.mode == "OpenAI API 證據限定回答"
    assert generated.provider == "openai"


def test_chunking_and_raw_responses_output_parser() -> None:
    chunks = chunk_text("# 標題\n\n" + "研究內容" * 400, max_characters=400, overlap=40)
    assert len(chunks) > 1
    assert all(len(item.content) <= 400 for item in chunks)
    assert [item.index for item in chunks] == list(range(len(chunks)))
    assert all(len(item.content_hash) == 64 for item in chunks)
    assert OpenAIResponsesAnswerProvider._extract_output_text({
        "output": [{"content": [{"type": "output_text", "text": "回答 [來源1]"}]}]
    }) == "回答 [來源1]"
    compact = DailyReportKnowledgeService._compact_research_value({
        "equity_curve": [{"date": index, "value": index * 1.1} for index in range(1000)]
    })
    assert compact["equity_curve"]["筆數"] == 1000
    assert len(str(compact)) < 500


def test_existing_rag_audit_table_gets_safe_additive_upgrade(tmp_path) -> None:
    database_path = tmp_path / "upgrade.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("""
            CREATE TABLE rag_query_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                mode VARCHAR(80) NOT NULL,
                embedding_provider VARCHAR(40) NOT NULL,
                embedding_model VARCHAR(100) NOT NULL,
                retrieval_json TEXT NOT NULL DEFAULT '[]',
                answer TEXT NOT NULL,
                grounded BOOLEAN NOT NULL DEFAULT 0,
                refusal_reason TEXT,
                latency_ms INTEGER NOT NULL,
                created_at DATETIME NOT NULL
            )
        """)
    build_container(Settings(database_url=f"sqlite:///{database_path}"))
    with sqlite3.connect(database_path) as connection:
        columns = {item[1] for item in connection.execute("PRAGMA table_info(rag_query_audits)")}
    assert {"answer_provider", "answer_model"} <= columns


def test_report_generation_removes_only_legacy_rebuildable_documents(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'prune.db'}"))
    repository = container.daily_report_knowledge_service._repository
    repository.upsert_documents([
        KnowledgeDocument(
            id=None, document_key="因子:legacy-id", document_type="因子",
            title="舊因子文件", content="可重新建立", source_uri="/factors",
            updated_at=datetime.now(UTC),
        ),
        KnowledgeDocument(
            id=None, document_key="manual:keep", document_type="研究筆記",
            title="人工研究筆記", content="不可刪除", source_uri="/reports",
            updated_at=datetime.now(UTC),
        ),
    ])

    container.daily_report_knowledge_service.generate("TW")
    keys = {item.document_key for item in repository.list_documents()}

    assert "因子:legacy-id" not in keys
    assert "manual:keep" in keys


def test_report_page_generates_and_renders(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'report-page.db'}"))
    client = create_app(container).test_client()
    response = client.post("/reports/generate/TW", follow_redirects=True)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "財經記憶與研究問答" in body
    assert "台股每日量化研究報告" in body
    assert "名詞解釋" in body
    assert "384 維" in body
    assert "同步本機索引" in body
    assert "不呼叫 embedding API" in body
    assert "只傳問題與最多 4 段" in body

    api_response = client.post("/reports/reindex", follow_redirects=True)
    assert api_response.status_code == 200
    assert "本次更新 0 個切塊" in api_response.get_data(as_text=True)
