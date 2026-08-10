from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol, Sequence

import requests


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class AnswerProvider(Protocol):
    name: str
    model: str

    def answer(self, question: str, evidence: Sequence[tuple[str, str]]) -> str: ...


def research_tokens(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    latin = re.findall(r"[a-z0-9_.-]{2,}", normalized)
    chinese_chars = [char for char in normalized if "\u4e00" <= char <= "\u9fff"]
    chinese = chinese_chars + [
        "".join(chinese_chars[index:index + 2])
        for index in range(max(0, len(chinese_chars) - 1))
    ]
    return latin + chinese


class LocalHashEmbeddingProvider:
    """Deterministic CPU embedding for offline Chinese/English retrieval.

    It is intentionally dependency-free. It is not a learned semantic model, but it
    gives the platform a stable vector contract and can be replaced through DI.
    """

    name = "local"
    model = "hashing-zh-en-v1"
    device = "cpu"
    batch_size = 512

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 64:
            raise ValueError("embedding dimensions must be at least 64")
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        counts = Counter(research_tokens(text))
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


class OpenAIEmbeddingProvider:
    name = "openai"
    device = "openai-managed"
    batch_size = 64

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-large",
        dimensions: int = 1024,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 30,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        self._api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = requests.post(
            f"{self._base_url}/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self.model, "input": list(texts), "dimensions": self.dimensions},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        ordered = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
        vectors = [item["embedding"] for item in ordered]
        if len(vectors) != len(texts):
            raise RuntimeError("OpenAI embeddings response count mismatch")
        if any(len(vector) != self.dimensions for vector in vectors):
            raise RuntimeError("OpenAI embeddings response dimension mismatch")
        return vectors


class SentenceTransformerEmbeddingProvider:
    """Optional learned multilingual embeddings with CUDA/CPU auto selection.

    The dependency is loaded only when explicitly selected, so the default
    installation remains lightweight and offline-safe.
    """

    name = "sentence-transformers"

    def __init__(
        self,
        model: str = "BAAI/bge-small-zh-v1.5",
        device: str = "auto",
        batch_size: int = 32,
        offline: bool = True,
    ) -> None:
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "本機語意向量需要安裝 sentence-transformers 與支援 CUDA 的 PyTorch"
            ) from exc
        requested = device.lower()
        if requested == "auto":
            requested = "cuda" if torch.cuda.is_available() else "cpu"
        if requested == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("已指定 CUDA，但目前 PyTorch 無法使用 GPU")
        self.device = requested
        self.model = model
        self.batch_size = max(1, min(256, int(batch_size)))
        self.offline = bool(offline)
        self._encoder = SentenceTransformer(
            model,
            device=requested,
            local_files_only=self.offline,
        )
        self.dimensions = int(self._encoder.get_sentence_embedding_dimension())

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._encoder.encode(
            list(texts),
            batch_size=self.batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [[float(value) for value in row] for row in vectors]


class OpenAIResponsesAnswerProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.6-luna",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 45,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def answer(self, question: str, evidence: Sequence[tuple[str, str]]) -> str:
        context = "\n\n".join(f"{citation}\n{content}" for citation, content in evidence)
        response = requests.post(
            f"{self._base_url}/responses",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self.model,
                "instructions": (
                    "你是量化研究助手。只能根據提供的研究證據回答；每個事實後必須引用"
                    "[來源N]。證據不足時明確拒答，不得保證報酬或產生無條件買進指令。"
                ),
                "input": f"問題：{question}\n\n研究證據：\n{context}",
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        return self._extract_output_text(response.json())

    @staticmethod
    def _extract_output_text(payload: dict[str, object]) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        parts: list[str] = []
        for output in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
            if not isinstance(output, dict):
                continue
            content = output.get("content", [])
            if not isinstance(content, list):
                continue
            for item in content:
                if isinstance(item, dict) and item.get("type") == "output_text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        if not parts:
            raise RuntimeError("OpenAI Responses API returned no output text")
        return "\n".join(parts).strip()


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    index: int
    content: str
    content_hash: str


def chunk_text(text: str, max_characters: int = 1000, overlap: int = 160) -> list[ChunkDraft]:
    if max_characters < 300 or overlap < 0 or overlap >= max_characters:
        raise ValueError("invalid chunk sizing")
    blocks: list[str] = []
    for raw in re.split(r"\n\s*\n", text.strip()):
        block = raw.strip()
        if not block:
            continue
        if len(block) <= max_characters:
            blocks.append(block)
        else:
            step = max_characters - overlap
            blocks.extend(block[start:start + max_characters] for start in range(0, len(block), step))
    chunks: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}".strip() if current else block
        if current and len(candidate) > max_characters:
            chunks.append(current)
            prefix = current[-overlap:] if overlap else ""
            current = f"{prefix}\n\n{block}".strip()
            if len(current) > max_characters:
                chunks.append(current[:max_characters])
                current = current[max_characters - overlap:]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [ChunkDraft(
        index=index,
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    ) for index, content in enumerate(chunks)]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def vector_json(vector: Sequence[float]) -> str:
    return json.dumps([round(float(value), 8) for value in vector], separators=(",", ":"))


def parse_vector(value: str) -> list[float]:
    parsed = json.loads(value)
    return [float(item) for item in parsed]
