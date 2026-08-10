from __future__ import annotations

import time

from quant_platform.config import get_settings
from quant_platform.container import build_container


def main() -> None:
    started = time.perf_counter()
    settings = get_settings()
    container = build_container(settings)
    service = container.daily_report_knowledge_service
    provider = service._embedding
    print(
        "開始建立財經記憶："
        f"{provider.name} / {provider.model} / {provider.device} / {provider.dimensions} 維；"
        f"OpenAI embedding={settings.openai_embedding_enabled}"
    )
    report = service.generate("TW")
    overview = service.overview()
    print(
        f"完成：報告={report.title}；新聞文件={overview.news_document_count:,}；"
        f"全部文件={overview.document_count:,}；向量片段={overview.chunk_count:,}；"
        f"耗時={time.perf_counter() - started:.1f} 秒"
    )


if __name__ == "__main__":
    main()
