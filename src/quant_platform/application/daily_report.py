from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from quant_platform.application.news_research import NewsResearchService
from quant_platform.application.ports import (
    BacktestResearchRepository,
    DailyDecisionRepository,
    ModelResearchRepository,
    PortfolioResearchRepository,
    RegimeFactorResearchRepository,
    ResearchKnowledgeRepository,
)
from quant_platform.domain.entities import (
    DailyResearchReport,
    KnowledgeChunk,
    KnowledgeDocument,
    RagQueryAudit,
)
from quant_platform.rag import (
    AnswerProvider,
    EmbeddingProvider,
    chunk_text,
    cosine_similarity,
    parse_vector,
    research_tokens,
    vector_json,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RagEvidence:
    citation: str
    title: str
    document_key: str
    document_type: str
    source_uri: str
    chunk_index: int
    score: float
    excerpt: str


@dataclass(frozen=True, slots=True)
class RagAnswer:
    question: str
    answer: str
    evidence: tuple[RagEvidence, ...]
    mode: str
    grounded: bool
    provider: str
    model: str
    latency_ms: int
    query_id: int | None
    refusal_reason: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeIndexResult:
    document_count: int
    chunk_count: int
    changed_chunk_count: int
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int


@dataclass(frozen=True, slots=True)
class ReportOverview:
    reports: tuple[DailyResearchReport, ...]
    document_count: int
    chunk_count: int
    recent_query_count: int
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    embedding_device: str
    news_document_count: int
    answer_provider: str
    answer_model: str
    external_llm_enabled: bool
    last_indexed_at: datetime | None
    answer: RagAnswer | None


class DailyReportKnowledgeService:
    """Evidence-linked reports, persisted vector retrieval and citation-safe answers."""

    def __init__(
        self,
        repository: ResearchKnowledgeRepository,
        decisions: DailyDecisionRepository,
        factors: RegimeFactorResearchRepository,
        backtests: BacktestResearchRepository,
        portfolios: PortfolioResearchRepository,
        models: ModelResearchRepository,
        news: NewsResearchService,
        embedding_provider: EmbeddingProvider,
        answer_provider: AnswerProvider | None = None,
    ) -> None:
        self._repository = repository
        self._decisions = decisions
        self._factors = factors
        self._backtests = backtests
        self._portfolios = portfolios
        self._models = models
        self._news = news
        self._embedding = embedding_provider
        self._answer_provider = answer_provider

    def generate(
        self, market: str = "TW", report_date: date | None = None, index: bool = True
    ) -> DailyResearchReport:
        """Save the daily report; ``index=False`` skips the RAG document and
        vector sync (paused module, REQUIREMENTS §13)."""
        normalized = market.upper()
        if normalized not in {"TW", "US"}:
            raise ValueError("market must be TW or US")
        today = report_date or date.today()
        now = datetime.now(UTC)
        decisions = sorted(
            self._decisions.list_latest(normalized), key=lambda item: item.score, reverse=True
        )
        factors = self._factors.list_factor_results(normalized)
        backtests = self._backtests.list_runs(normalized)
        portfolios = self._portfolios.list_runs(normalized)
        models = self._models.list_runs(normalized)
        news = self._news.get_overview(limit=20) if normalized == "TW" else None
        candidates = [
            item for item in decisions if item.status.upper() in {"CANDIDATE", "WATCH"}
        ][:5]
        blocked = [
            item for item in decisions if item.status.upper() not in {"CANDIDATE", "WATCH"}
        ][:5]
        candidate_backtests = [item for item in backtests if item.promotion_gate == "CANDIDATE"]
        candidate_models = [item for item in models if item.promotion_gate == "CANDIDATE"]
        lines = [
            f"# {today} {'台股' if normalized == 'TW' else '美股'}每日量化研究報告",
            "",
            "## 今日結論",
            f"- 決策標的 {len(decisions)} 檔；可觀察候選 {len(candidates)} 檔。",
            f"- 候選回測 {len(candidate_backtests)} 組；候選模型 {len(candidate_models)} 組。",
            f"- 因子研究 {len(factors)} 組；投資組合研究 {len(portfolios)} 組。",
        ]
        if news:
            lines.append(
                f"- 近期新聞 {news.total_count} 則：正向 {news.positive_count}、"
                f"中性 {news.neutral_count}、負向 {news.negative_count}。"
            )
        lines.extend(["", "## 候選與觀察"])
        lines.extend(
            f"- {item.symbol}：分數 {item.score:.3f}，五日預測 "
            f"{item.predicted_return_5d or 0:+.2%}，市場狀態 {item.regime}。"
            for item in candidates
        )
        if not candidates:
            lines.append("- 今天沒有通過研究門檻的候選，維持觀察而非強迫交易。")
        lines.extend(["", "## 今日避免"])
        lines.extend(
            f"- {item.symbol}：狀態 {item.status}，風險與未通過門檻請見每日決策頁。"
            for item in blocked
        )
        if not blocked:
            lines.append("- 尚無足夠決策資料，請先執行每日研究流程。")
        lines.extend([
            "", "## 研究限制",
            "- 本報告只引用資料庫中已保存的模型、回測、因子、新聞與風險證據。",
            "- 沒有通過樣本外、成本後與資料品質門檻時，不產生買進指令。",
            "- 問答採持久化向量與詞彙混合檢索；每項結論必須連回來源。",
        ])
        sources = ["/decisions", "/factors", "/backtests", "/models", "/portfolios"]
        if normalized == "TW":
            sources.append("/news")
        report = DailyResearchReport(
            id=None, report_date=today, market=normalized,
            title=f"{today} {'台股' if normalized == 'TW' else '美股'}每日量化研究報告",
            body_markdown="\n".join(lines),
            sources_json=json.dumps(sources, ensure_ascii=False), generated_at=now,
        )
        report_id = self._repository.save_report(report)
        saved = DailyResearchReport(**{**asdict(report), "id": report_id})
        if index:
            self._sync_documents(saved, factors, backtests, models, portfolios, now)
            self.sync_index()
        return saved

    def overview(self, question: str | None = None) -> ReportOverview:
        if question and question.strip():
            answer = self.ask(question)
        else:
            self.sync_index()
            answer = None
        return self._build_overview(answer)

    def status(self) -> ReportOverview:
        """Return persisted counts without rebuilding the index on a status-page view."""
        return self._build_overview(None)

    def _build_overview(self, answer: RagAnswer | None) -> ReportOverview:
        chunks = self._active_chunks()
        audits = self._repository.list_query_audits(limit=30)
        documents = self._repository.list_documents()
        return ReportOverview(
            reports=tuple(self._repository.list_reports(limit=30)),
            document_count=len(documents),
            chunk_count=len(chunks), recent_query_count=len(audits),
            embedding_provider=self._embedding.name,
            embedding_model=self._embedding.model,
            embedding_dimensions=self._embedding.dimensions,
            embedding_device=str(getattr(self._embedding, "device", "unknown")),
            news_document_count=sum(item.document_type == "財經新聞" for item in documents),
            answer_provider=self._answer_provider.name if self._answer_provider else "local",
            answer_model=self._answer_provider.model if self._answer_provider else "evidence-summary-v1",
            external_llm_enabled=self._answer_provider is not None,
            last_indexed_at=max((item.updated_at for item in chunks), default=None),
            answer=answer,
        )

    def sync_index(self) -> KnowledgeIndexResult:
        changed = 0
        documents = self._repository.list_documents()
        existing_by_document: dict[str, list[KnowledgeChunk]] = {}
        for item in self._repository.list_chunks():
            existing_by_document.setdefault(item.document_key, []).append(item)
        pending: list[tuple[KnowledgeDocument, list[object]]] = []
        for document in documents:
            drafts = chunk_text(f"{document.title}\n\n{document.content}")
            existing = sorted(
                existing_by_document.get(document.document_key, []),
                key=lambda item: item.chunk_index,
            )
            expected_hashes = [item.content_hash for item in drafts]
            current_hashes = [item.content_hash for item in existing]
            current_contract = all(
                item.embedding_provider == self._embedding.name
                and item.embedding_model == self._embedding.model
                and item.embedding_dimensions == self._embedding.dimensions
                for item in existing
            )
            if current_contract and current_hashes == expected_hashes:
                continue
            pending.append((document, drafts))

        contents = [draft.content for _, drafts in pending for draft in drafts]
        vectors: list[list[float]] = []
        batch_size = max(1, int(getattr(self._embedding, "batch_size", 64)))
        for start in range(0, len(contents), batch_size):
            vectors.extend(self._embedding.embed(contents[start:start + batch_size]))
        cursor = 0
        for document, drafts in pending:
            document_vectors = vectors[cursor:cursor + len(drafts)]
            cursor += len(drafts)
            values = []
            for draft, vector in zip(drafts, document_vectors):
                chunk_key = hashlib.sha256(
                    f"{document.document_key}|{draft.index}|{draft.content_hash}|"
                    f"{self._embedding.name}|{self._embedding.model}".encode("utf-8")
                ).hexdigest()
                values.append(KnowledgeChunk(
                    id=None, chunk_key=chunk_key, document_key=document.document_key,
                    document_type=document.document_type, title=document.title,
                    chunk_index=draft.index, content=draft.content,
                    content_hash=draft.content_hash, source_uri=document.source_uri,
                    embedding_provider=self._embedding.name,
                    embedding_model=self._embedding.model,
                    embedding_dimensions=self._embedding.dimensions,
                    embedding_json=vector_json(vector), updated_at=document.updated_at,
                ))
            changed += self._repository.replace_chunks(document.document_key, values)
        return KnowledgeIndexResult(
            document_count=len(documents), chunk_count=len(self._active_chunks()),
            changed_chunk_count=changed, embedding_provider=self._embedding.name,
            embedding_model=self._embedding.model,
            embedding_dimensions=self._embedding.dimensions,
        )

    def ask(self, question: str, limit: int = 4) -> RagAnswer:
        started = time.perf_counter()
        clean_question = question.strip()
        if not clean_question:
            raise ValueError("question cannot be empty")
        self.sync_index()
        query_vector = self._embedding.embed([clean_question])[0]
        query_tokens = set(research_tokens(clean_question))
        ranked: list[tuple[float, float, float, KnowledgeChunk]] = []
        for chunk in self._active_chunks():
            vector_score = max(0.0, cosine_similarity(query_vector, parse_vector(chunk.embedding_json)))
            chunk_tokens = set(research_tokens(f"{chunk.title} {chunk.content}"))
            lexical_score = len(query_tokens & chunk_tokens) / max(1, len(query_tokens))
            score = vector_score * 0.70 + lexical_score * 0.30
            threshold = 0.18 if self._embedding.name == "openai" else 0.10
            if score >= threshold and (lexical_score > 0 or vector_score >= 0.20):
                ranked.append((score, vector_score, lexical_score, chunk))
        ranked.sort(key=lambda item: (item[0], item[3].updated_at), reverse=True)
        selected: list[tuple[float, KnowledgeChunk]] = []
        used_documents: set[str] = set()
        for score, _, _, chunk in ranked:
            if chunk.document_key in used_documents:
                continue
            selected.append((score, chunk))
            used_documents.add(chunk.document_key)
            if len(selected) >= limit:
                break
        evidence = tuple(RagEvidence(
            citation=f"[來源{index}]", title=chunk.title,
            document_key=chunk.document_key, document_type=chunk.document_type,
            source_uri=chunk.source_uri, chunk_index=chunk.chunk_index, score=score,
            excerpt=self._excerpt(chunk.content),
        ) for index, (score, chunk) in enumerate(selected, start=1))
        refusal_reason: str | None = None
        provider = "local"
        model = "evidence-summary-v1"
        mode = "本機向量與詞彙混合檢索"
        grounded = bool(evidence)
        if not evidence:
            refusal_reason = "知識庫沒有達到檢索門檻的證據"
            answer_text = (
                "知識庫目前找不到足夠證據。請先產生每日報告或執行研究流程；"
                "系統不會臆測答案。"
            )
        elif self._answer_provider is not None:
            try:
                generated = self._answer_provider.answer(
                    clean_question,
                    [(item.citation, f"{item.title}\n{item.excerpt}") for item in evidence],
                )
                if not self._valid_citations(generated, len(evidence)):
                    raise ValueError("generated answer did not contain valid citations")
                answer_text = generated
                provider = self._answer_provider.name
                model = self._answer_provider.model
                mode = "OpenAI API 證據限定回答"
            except Exception as exc:
                logger.warning("External RAG answer failed; using local evidence summary: %s", exc)
                answer_text = self._local_answer(evidence)
                mode = "OpenAI 不可用，已降級為本機證據摘要"
        else:
            answer_text = self._local_answer(evidence)
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        retrieval = [
            {
                "citation": item.citation, "document_key": item.document_key,
                "chunk_index": item.chunk_index, "score": round(item.score, 6),
                "source_uri": item.source_uri,
            }
            for item in evidence
        ]
        audit = RagQueryAudit(
            id=None, question=clean_question, mode=mode,
            embedding_provider=self._embedding.name,
            embedding_model=self._embedding.model,
            answer_provider=provider, answer_model=model,
            retrieval_json=json.dumps(retrieval, ensure_ascii=False),
            answer=answer_text, grounded=grounded,
            refusal_reason=refusal_reason, latency_ms=latency_ms,
            created_at=datetime.now(UTC),
        )
        query_id = self._repository.save_query_audit(audit)
        return RagAnswer(
            question=clean_question, answer=answer_text, evidence=evidence,
            mode=mode, grounded=grounded, provider=provider, model=model,
            latency_ms=latency_ms, query_id=query_id, refusal_reason=refusal_reason,
        )

    def _active_chunks(self) -> list[KnowledgeChunk]:
        return [
            item for item in self._repository.list_chunks()
            if item.embedding_provider == self._embedding.name
            and item.embedding_model == self._embedding.model
            and item.embedding_dimensions == self._embedding.dimensions
        ]

    def _sync_documents(self, report, factors, backtests, models, portfolios, now) -> None:
        documents = [KnowledgeDocument(
            id=None, document_key=f"report:{report.market}:{report.report_date}",
            document_type="每日報告", title=report.title, content=report.body_markdown,
            source_uri="/reports", updated_at=now,
        )]
        groups = (
            ("因子", factors, "/factors"), ("回測", backtests[:80], "/backtests"),
            ("模型", models[:80], "/models"),
            ("投資組合", portfolios[:30], "/portfolios"),
        )
        for kind, values, uri in groups:
            for index, value in enumerate(values):
                raw = json.dumps(
                    self._compact_research_value(asdict(value)),
                    ensure_ascii=False, default=str, separators=(",", ":"),
                )
                digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
                key_part = getattr(value, "id", None) or f"{report.market}:{index}:{digest}"
                documents.append(KnowledgeDocument(
                    id=None, document_key=f"{kind}:{report.market}:{key_part}",
                    document_type=kind,
                    title=self._document_title(kind, value), content=raw,
                    source_uri=uri, updated_at=now,
                ))
        if report.market == "TW":
            for item in self._news.get_overview(limit=10_000).items:
                identity = hashlib.sha256(
                    (
                        f"{item.symbol}|{item.event_time.isoformat()}|"
                        f"{item.title}|{item.url}"
                    ).encode("utf-8")
                ).hexdigest()[:24]
                content = "\n".join(
                    (
                        f"股票：{item.symbol}",
                        f"事件時間：{item.event_time.isoformat()}",
                        f"模型可用時間：{item.available_time.isoformat()}",
                        f"來源：{item.source_name}",
                        f"情緒：{item.sentiment_label} {item.sentiment:+.2f}",
                        f"標題：{item.title}",
                        f"摘要：{item.summary or '原始資料沒有摘要'}",
                    )
                )
                documents.append(KnowledgeDocument(
                    id=None,
                    document_key=f"news:{identity}",
                    document_type="財經新聞",
                    title=f"新聞｜{item.symbol} {item.title}",
                    content=content,
                    source_uri=item.url or f"/news?symbol={item.symbol}",
                    updated_at=item.available_time,
                ))
        self._repository.upsert_documents(documents)
        generated_keys = {item.document_key for item in documents}
        generated_types = {item[0] for item in groups}
        stale_keys = []
        for item in self._repository.list_documents():
            if item.document_type not in generated_types:
                continue
            market_prefix = f"{item.document_type}:{report.market}:"
            is_current_market_stale = (
                item.document_key.startswith(market_prefix)
                and item.document_key not in generated_keys
            )
            is_legacy = not item.document_key.startswith(
                (f"{item.document_type}:TW:", f"{item.document_type}:US:")
            )
            if is_current_market_stale or is_legacy:
                stale_keys.append(item.document_key)
        self._repository.remove_documents(stale_keys)

    @classmethod
    def _compact_research_value(cls, value, depth: int = 0):
        """Keep decision-relevant metadata without embedding full time-series artifacts."""
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value if len(value) <= 500 else value[:500] + "…"
        if depth >= 3:
            try:
                return {"筆數": len(value)}
            except TypeError:
                return str(value)[:500]
        if isinstance(value, dict):
            compact = {}
            for key, item in list(value.items())[:40]:
                compact[str(key)] = cls._compact_research_value(item, depth + 1)
            if len(value) > 40:
                compact["其餘欄位數"] = len(value) - 40
            return compact
        if isinstance(value, (list, tuple)):
            if not value:
                return []
            if all(item is None or isinstance(item, (bool, int, float, str)) for item in value):
                sample = [cls._compact_research_value(item, depth + 1) for item in value[:20]]
            else:
                sample = [cls._compact_research_value(value[0], depth + 1)]
                if len(value) > 1:
                    sample.append(cls._compact_research_value(value[-1], depth + 1))
            return {"筆數": len(value), "首末樣本": sample}
        return str(value)[:500]

    @staticmethod
    def _local_answer(evidence: tuple[RagEvidence, ...]) -> str:
        lines = ["根據目前已保存的研究證據："]
        lines.extend(
            f"{item.citation} {item.title}：{item.excerpt}" for item in evidence
        )
        lines.append("請點開來源頁確認資料時間、樣本外門檻與風險限制；這不是保證報酬。")
        return "\n".join(lines)

    @staticmethod
    def _excerpt(content: str, limit: int = 260) -> str:
        normalized = re.sub(r"\s+", " ", content).strip()
        return normalized if len(normalized) <= limit else normalized[:limit].rstrip() + "…"

    @staticmethod
    def _valid_citations(answer: str, evidence_count: int) -> bool:
        found = {int(value) for value in re.findall(r"\[來源(\d+)\]", answer)}
        return bool(found) and all(1 <= value <= evidence_count for value in found)

    @staticmethod
    def _document_title(kind: str, value) -> str:
        symbol = getattr(value, "symbol", "")
        name = (
            getattr(value, "strategy_name", None)
            or getattr(value, "model_name", None)
            or getattr(value, "feature_name", None)
            or getattr(value, "method", None)
            or "研究紀錄"
        )
        return f"{kind}｜{symbol} {name}".strip()
