from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Any

from .agent_schemas import FeatureMapping, NoveltyDocumentMatrix, NoveltyResult
from .database import Database, canonical_json, now_ms


class NoveltyGateError(RuntimeError):
    """Raised when durable inputs cannot support a trustworthy novelty result."""


@dataclass(frozen=True)
class _DocumentMatrix:
    document_id: str
    publication_number: str
    publication_date: str | None
    matrix: NoveltyDocumentMatrix
    score: float
    confidence: float


class NoveltyService:
    """Build a novelty result deterministically from audited per-document mappings."""

    def __init__(self, database: Database, *, minimum_deep_reviews: int = 10):
        if minimum_deep_reviews < 1:
            raise ValueError("minimum_deep_reviews must be positive")
        self.database = database
        self.minimum_deep_reviews = minimum_deep_reviews

    def determine(
        self, run_id: str, *, minimum_deep_reviews: int | None = None
    ) -> NoveltyResult:
        minimum = minimum_deep_reviews or self.minimum_deep_reviews
        if minimum < 1:
            raise ValueError("minimum_deep_reviews must be positive")
        run, feature_rows, document_rows = self._load_inputs(run_id)
        if not feature_rows:
            raise NoveltyGateError("novelty requires at least one persisted idea feature")
        if not document_rows:
            raise NoveltyGateError("novelty requires analyzed patent documents")

        external_features = [self._external_feature_id(row) for row in feature_rows]
        matrices = [
            self._build_document_matrix(run_id, row, feature_rows, external_features)
            for row in document_rows
        ]
        for item in matrices:
            self._enforce_date(item, run["evaluation_date"])

        ranked = sorted(
            matrices,
            key=lambda item: (-item.score, -item.confidence, item.publication_number),
        )
        closest = ranked[0]
        destroying = [item for item in ranked if item.matrix.destroys_novelty]
        limitations: list[str] = []
        if len(matrices) < minimum:
            limitations.append(
                f"仅完成 {len(matrices)} 篇有效深读，低于配置下限 {minimum} 篇。"
            )

        if destroying:
            selected = destroying[0]
            conclusion = "NOT_NOVEL"
            confidence = selected.confidence
            destroying_publication = selected.publication_number
            missing_features: list[str] = []
            rationale = (
                f"文献 {selected.publication_number} 在同一篇文献内披露全部必要技术特征，"
                "不需要且未使用跨文献拼接。"
            )
        else:
            missing_features = [
                mapping.feature_id
                for mapping in closest.matrix.mappings
                if mapping.status != "DISCLOSED"
            ]
            every_document_has_definite_gap = all(
                any(mapping.status == "NOT_DISCLOSED" for mapping in item.matrix.mappings)
                for item in matrices
            )
            enough_reviews = len(matrices) >= minimum
            if every_document_has_definite_gap and enough_reviews:
                conclusion = "NOVEL"
                rationale = (
                    "已审阅文献中没有任何单篇文献披露全部必要技术特征；每篇文献均至少有一个"
                    "明确未披露特征，因此在本次检索范围和评估日下判定具备新颖性。"
                )
            else:
                conclusion = "UNCERTAIN"
                reason = (
                    "存在仅为部分披露或不确定的关键映射"
                    if not every_document_has_definite_gap
                    else "有效深读数量未达到配置下限"
                )
                rationale = f"没有单篇文献被证明可破坏新颖性，但{reason}，暂不作肯定结论。"
            confidence = min(0.95, mean(item.confidence for item in matrices))
            destroying_publication = None

        result = NoveltyResult(
            conclusion=conclusion,
            confidence=round(confidence, 4),
            matrices=[item.matrix for item in matrices],
            destroying_publication_number=destroying_publication,
            closest_publication_number=closest.publication_number,
            missing_features=missing_features,
            rationale=rationale,
            limitations=limitations,
        )
        self._persist(run_id, result, destroying)
        return result

    def _load_inputs(self, run_id: str):
        with self.database.connect() as connection:
            run = connection.execute(
                "SELECT evaluation_date FROM idea_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            feature_rows = connection.execute(
                "SELECT * FROM idea_features WHERE run_id = ? ORDER BY ordinal", (run_id,)
            ).fetchall()
            feature_rows = [
                row
                for row in feature_rows
                if self._feature_required(row)
            ]
            document_rows = connection.execute(
                """
                SELECT d.document_id,d.publication_number,d.publication_date
                FROM run_documents rd
                JOIN patent_documents d ON d.document_id = rd.document_id
                WHERE rd.run_id = ? AND rd.deep_reviewed = TRUE
                  AND rd.screening_status = 'ANALYZED'
                ORDER BY d.publication_number,d.document_id
                """,
                (run_id,),
            ).fetchall()
        return run, feature_rows, document_rows

    @staticmethod
    def _feature_required(row) -> bool:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError as exc:
            raise NoveltyGateError("invalid feature metadata JSON") from exc
        return metadata.get("required", True) is not False

    def _build_document_matrix(
        self,
        run_id: str,
        document_row,
        feature_rows,
        external_features: list[str],
    ) -> _DocumentMatrix:
        with self.database.connect() as connection:
            mapping_rows = connection.execute(
                """
                SELECT * FROM feature_mappings
                WHERE run_id = ? AND document_id = ?
                """,
                (run_id, document_row["document_id"]),
            ).fetchall()
            by_feature = {row["feature_id"]: row for row in mapping_rows}
            mappings: list[FeatureMapping] = []
            for feature_row, external_id in zip(feature_rows, external_features, strict=True):
                row = by_feature.get(feature_row["feature_id"])
                if row is None:
                    raise NoveltyGateError(
                        f"document {document_row['publication_number']} lacks mapping for {external_id}"
                    )
                try:
                    evidence_ids = json.loads(row["evidence_ids_json"])
                except json.JSONDecodeError as exc:
                    raise NoveltyGateError("invalid evidence ID JSON in feature mapping") from exc
                if not isinstance(evidence_ids, list) or not all(
                    isinstance(item, str) for item in evidence_ids
                ):
                    raise NoveltyGateError("feature mapping evidence IDs must be a string list")
                self._verify_evidence(
                    connection,
                    run_id,
                    document_row["document_id"],
                    evidence_ids,
                )
                mappings.append(
                    FeatureMapping(
                        feature_id=external_id,
                        status=row["coverage_status"],
                        evidence_ids=evidence_ids,
                        rationale=row["rationale"],
                        confidence=row["confidence"],
                    )
                )
        matrix = NoveltyDocumentMatrix(
            publication_number=document_row["publication_number"],
            mappings=mappings,
            destroys_novelty=all(mapping.status == "DISCLOSED" for mapping in mappings),
        )
        weights = {"DISCLOSED": 1.0, "PARTIAL": 0.5, "UNCERTAIN": 0.25, "NOT_DISCLOSED": 0.0}
        score = mean(weights[mapping.status] for mapping in mappings)
        confidence = min(mapping.confidence for mapping in mappings)
        return _DocumentMatrix(
            document_id=document_row["document_id"],
            publication_number=document_row["publication_number"],
            publication_date=document_row["publication_date"],
            matrix=matrix,
            score=score,
            confidence=confidence,
        )

    @staticmethod
    def _external_feature_id(row) -> str:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError as exc:
            raise NoveltyGateError("invalid feature metadata JSON") from exc
        external_id = metadata.get("external_feature_id")
        if not isinstance(external_id, str):
            raise NoveltyGateError(f"feature {row['feature_id']} has no external feature ID")
        return external_id

    @staticmethod
    def _verify_evidence(connection, run_id: str, document_id: str, evidence_ids: list[str]) -> None:
        for evidence_id in evidence_ids:
            row = connection.execute(
                """SELECT quote_text,content_hash FROM evidence
                WHERE evidence_id = ? AND run_id = ? AND document_id = ?""",
                (evidence_id, run_id, document_id),
            ).fetchone()
            if row is None:
                raise NoveltyGateError(f"unknown or cross-document evidence ID: {evidence_id}")
            actual_hash = hashlib.sha256(row["quote_text"].encode("utf-8")).hexdigest()
            if actual_hash != row["content_hash"]:
                raise NoveltyGateError(f"evidence content hash mismatch: {evidence_id}")

    @staticmethod
    def _enforce_date(item: _DocumentMatrix, evaluation_date: str) -> None:
        if not item.publication_date:
            raise NoveltyGateError(
                f"document {item.publication_number} has no publication date"
            )
        try:
            publication = NoveltyService._parse_date(item.publication_date)
            evaluation = NoveltyService._parse_date(evaluation_date)
        except ValueError as exc:
            raise NoveltyGateError(
                f"invalid date for document {item.publication_number}"
            ) from exc
        if publication > evaluation:
            raise NoveltyGateError(
                f"post-evaluation document entered novelty matrix: {item.publication_number}"
            )

    @staticmethod
    def _parse_date(value: str) -> date:
        compact = value.strip().replace("/", "-")
        if len(compact) == 8 and compact.isdigit():
            compact = f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
        return date.fromisoformat(compact)

    def _persist(
        self,
        run_id: str,
        result: NoveltyResult,
        destroying: list[_DocumentMatrix],
    ) -> None:
        destroying_document_id = None
        if result.destroying_publication_number:
            destroying_document_id = next(
                item.document_id
                for item in destroying
                if item.publication_number == result.destroying_publication_number
            )
        with self.database.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM novelty_results WHERE run_id = ?", (run_id,)
            ).fetchone()
            if exists:
                raise NoveltyGateError("novelty result already exists for this run")
            connection.execute(
                """
                INSERT INTO novelty_results(
                    run_id,conclusion,confidence,destroying_document_id,
                    matrix_json,rationale,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    result.conclusion,
                    result.confidence,
                    destroying_document_id,
                    canonical_json(result.model_dump(mode="json")),
                    result.rationale,
                    now_ms(),
                ),
            )
