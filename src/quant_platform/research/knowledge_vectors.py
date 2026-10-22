"""2026-10-22 (使用者：向量資料庫可用 BGE-M3、Qdrant，我另一個專案應該有): the knowledge base's entries as BGE-M3 vectors
in an embedded Qdrant collection — the VectorDB project's design (dense and learned sparse, fused by RRF), the model
files read from that project's ``models/bge-m3`` (or KNOWLEDGE_BGE_MODEL), nothing downloaded.

- Encoder (``BgeM3``): transformers' XLM-R with BGE-M3's weights; dense = the normalized [CLS] state, sparse = ReLU of
  ``sparse_linear.pt`` over each token's state, the largest per token id, special tokens left out — what
  FlagEmbedding's BGEM3FlagModel returns, without its dependencies.
- Store: ``QdrantClient(path=…/knowledge/qdrant)`` (embedded, no server), opened for each operation and closed again
  so the web process and the indexing process take turns; named vectors "dense" (cosine) and "sparse".
- ``index``: only the entries whose text changed are encoded (each point keeps its text's hash); entries gone are
  deleted. ``query``: Qdrant's own RRF over the dense and sparse candidates.

Without the packages, the model or the collection, the knowledge base searches by its keywords alone."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
COLLECTION = "research_knowledge_bge_m3_v1"
DIMENSION = 1024
NAMESPACE = uuid.UUID("6f1c2f5e-2f53-4e43-9a1d-6d1f6d2b0a11")
DOC_TOKENS = 2048                 # an entry is at most MAX_TEXT characters (about this many tokens)
QUERY_TOKENS = 256
CANDIDATES = 60                   # each of dense and sparse before Qdrant fuses them


def model_path(root: str | Path | None = None) -> Path | None:
    """KNOWLEDGE_BGE_MODEL, this project's models/bge-m3, or the VectorDB project's next to it — a folder with the
    weights and sparse_linear.pt; None when none has them."""
    candidates = [os.getenv("KNOWLEDGE_BGE_MODEL", "")]
    base = (Path(root) if root else Path.cwd()).resolve()      # the worker passes "." (its parent is itself)
    candidates += [str(base / "models" / "bge-m3"), str(base.parent / "VectorDB" / "models" / "bge-m3")]
    for item in candidates:
        if item and (Path(item) / "sparse_linear.pt").is_file() and (Path(item) / "config.json").is_file():
            return Path(item)
    return None


def available() -> bool:
    try:
        import qdrant_client  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


class BgeM3:
    """BGE-M3 dense and learned sparse vectors from the local model folder."""

    def __init__(self, path: str | Path, device: str = "auto", fp16: bool = True) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.path = str(path)
        self.tokenizer = AutoTokenizer.from_pretrained(self.path, local_files_only=True)
        self.model = AutoModel.from_pretrained(self.path, local_files_only=True).eval().to(device)
        self.sparse = torch.nn.Linear(self.model.config.hidden_size, 1)
        self.sparse.load_state_dict(torch.load(Path(self.path) / "sparse_linear.pt", map_location="cpu"))
        self.sparse = self.sparse.eval().to(device)
        if fp16 and device.startswith("cuda"):
            self.model.half()
            self.sparse.half()
        tokenizer = self.tokenizer
        self.special = {token for token in (tokenizer.cls_token_id, tokenizer.eos_token_id, tokenizer.pad_token_id,
                                            tokenizer.unk_token_id) if token is not None}

    def encode(self, texts: list[str], max_length: int = DOC_TOKENS, batch: int = 8) -> tuple[np.ndarray, list[dict[int, float]]]:
        torch = self._torch
        dense, sparse = [], []
        with torch.inference_mode():
            for start in range(0, len(texts), batch):
                chunk = texts[start:start + batch]
                inputs = self.tokenizer(chunk, padding=True, truncation=True, max_length=max_length,
                                        return_tensors="pt").to(self.device)
                hidden = self.model(**inputs, return_dict=True).last_hidden_state
                dense.append(torch.nn.functional.normalize(hidden[:, 0].float(), dim=-1).cpu().numpy())
                weights = torch.relu(self.sparse(hidden)).squeeze(-1).float().cpu().numpy()
                ids = inputs["input_ids"].cpu().numpy()
                for row in range(len(chunk)):
                    found: dict[int, float] = {}
                    for token, weight in zip(ids[row].tolist(), weights[row].tolist()):
                        if token in self.special or weight <= 0:
                            continue
                        if weight > found.get(token, 0.0):
                            found[token] = float(weight)
                    sparse.append(found)
        return (np.vstack(dense) if dense else np.zeros((0, DIMENSION), dtype=np.float32)), sparse


_ENCODER: dict[str, object] = {}
_LOCK = threading.Lock()


def encoder(root: str | Path | None = None, device: str | None = None):
    """The process's one encoder, loaded on first use (KNOWLEDGE_BGE_DEVICE, default the CPU: the web process leaves
    the graphics card to the research runs); None without the packages or the model."""
    with _LOCK:
        if "model" in _ENCODER:
            return _ENCODER["model"]
        path = model_path(root)
        if path is None or not available():
            return None
        _ENCODER["model"] = BgeM3(path, device or os.getenv("KNOWLEDGE_BGE_DEVICE", "cpu"))
        return _ENCODER["model"]


def store(research: str | Path) -> Path:
    return Path(research) / "knowledge" / "qdrant"


def _open(research: str | Path, tries: int = 40):
    """The embedded collection's client; another process holding it is waited for (closed after each use)."""
    from qdrant_client import QdrantClient

    path = store(research)
    path.mkdir(parents=True, exist_ok=True)
    for attempt in range(tries):
        try:
            return QdrantClient(path=str(path))
        except (RuntimeError, BlockingIOError, PermissionError) as exc:
            if attempt == tries - 1 or "already accessed" not in str(exc) and not isinstance(exc, (BlockingIOError, PermissionError)):
                raise
            time.sleep(0.5)
    raise RuntimeError("Qdrant 一直被另一個程序佔用")


def _point_id(entry_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, entry_id))


def _hash(entry: dict[str, object]) -> str:
    return hashlib.sha256(f"{entry['title']}\n{entry['text']}".encode("utf-8")).hexdigest()[:20]


def _ensure_collection(client) -> None:
    from qdrant_client import models

    if not client.collection_exists(COLLECTION):
        client.create_collection(COLLECTION,
                                 vectors_config={"dense": models.VectorParams(size=DIMENSION, distance=models.Distance.COSINE)},
                                 sparse_vectors_config={"sparse": models.SparseVectorParams()})


def index(research: str | Path, entries: list[dict[str, object]], model, batch: int = 8, job=None) -> dict[str, object]:
    """The entries whose text changed encoded and stored; the ones gone deleted; what was done."""
    from qdrant_client import models

    started = time.time()
    client = _open(research)
    try:
        _ensure_collection(client)
        stored: dict[str, str] = {}
        offset = None
        while True:
            points, offset = client.scroll(COLLECTION, limit=1000, offset=offset, with_payload=["hash"], with_vectors=False)
            stored.update({str(point.id): str((point.payload or {}).get("hash")) for point in points})
            if offset is None:
                break
        wanted = {_point_id(str(entry["id"])): entry for entry in entries}
        changed = [entry for point, entry in wanted.items() if stored.get(point) != _hash(entry)]
        for start in range(0, len(changed), 64):
            part = changed[start:start + 64]
            if job:
                job.update(done=start, total=len(changed), current=f"編碼 {start:,}／{len(changed):,} 筆")
            dense, sparse = model.encode([f"{entry['title']}\n{entry['text']}" for entry in part], DOC_TOKENS, batch)
            client.upsert(COLLECTION, points=[
                models.PointStruct(id=_point_id(str(entry["id"])),
                                   vector={"dense": dense[number].tolist(),
                                           "sparse": models.SparseVector(indices=list(sparse[number]),
                                                                         values=list(sparse[number].values()))},
                                   payload={"entry": entry["id"], "hash": _hash(entry), "kind": entry["kind"],
                                            "verdict": entry["verdict"]})
                for number, entry in enumerate(part)])
        gone = [point for point in stored if point not in wanted]
        if gone:
            client.delete(COLLECTION, points_selector=models.PointIdsList(points=gone))
        total = client.count(COLLECTION).count
    finally:
        client.close()
    report = {"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "collection": COLLECTION, "points": total,
              "encoded": len(changed), "deleted": len(gone), "seconds": round(time.time() - started, 1),
              "model": getattr(model, "path", ""), "device": getattr(model, "device", "")}
    (Path(research) / "knowledge" / "vectors.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def status(research: str | Path) -> dict[str, object]:
    try:
        return json.loads((Path(research) / "knowledge" / "vectors.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def query(research: str | Path, question: str, model, limit: int = CANDIDATES) -> list[str]:
    """Entry ids by Qdrant's RRF over the dense and the sparse candidates, best first."""
    from qdrant_client import models

    dense, sparse = model.encode([question], QUERY_TOKENS, 1)
    client = _open(research)
    try:
        if not client.collection_exists(COLLECTION):
            return []
        found = client.query_points(
            COLLECTION,
            prefetch=[models.Prefetch(query=dense[0].tolist(), using="dense", limit=limit),
                      models.Prefetch(query=models.SparseVector(indices=list(sparse[0]), values=list(sparse[0].values())),
                                      using="sparse", limit=limit)],
            query=models.FusionQuery(fusion=models.Fusion.RRF), limit=limit, with_payload=["entry"])
    finally:
        client.close()
    return [str((point.payload or {}).get("entry")) for point in found.points]
