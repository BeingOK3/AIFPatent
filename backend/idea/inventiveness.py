from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass

from .agent_schemas import (
    InventiveStepOutput,
    NoveltyDocumentMatrix,
    NoveltyResult,
)
from .agents import AgentExecutionError, IdeaAgentService
from .database import Database, canonical_json, now_ms


INVENTIVE_STEP_PROMPT = """
You are patent-inventive-step-analyzer. Analyze one supplied D1 route using only its supplied
distinguishing features and D2 evidence candidates. Apply a problem-solution approach: identify
the objective technical problem, test whether every distinguishing feature is taught by D2, and
explain whether a skilled person had a supported motivation to combine. Never search, invent a
patent/evidence ID, or change the novelty result. Return the supplied route_id and D1 publication
exactly. If any required D2 teaching or combination motivation lacks evidence, return
NEED_MORE_EVIDENCE or UNCERTAIN rather than NOT_INVENTIVE.
Write the objective technical problem, every rationale, and every limitation in Simplified Chinese.
"""


@dataclass(frozen=True)
class _RouteInput:
    route_id: str
    d1_document_id: str
    d1_publication_number: str
    distinguishing_feature_ids: tuple[str, ...]
    payload: dict
    allowed_publications: dict[str, set[str]]
    evidence_publication: dict[str, dict[str, str]]
    publication_status: dict[str, dict[str, str]]
    publication_document_id: dict[str, str]


class InventivenessService:
    def __init__(
        self,
        database: Database,
        agents: IdeaAgentService,
        *,
        max_routes: int = 3,
        max_d2_per_feature: int = 5,
        concurrency: int = 3,
    ):
        if max_routes < 1 or max_d2_per_feature < 1 or concurrency < 1:
            raise ValueError("inventiveness limits must be positive")
        self.database = database
        self.agents = agents
        self.max_routes = max_routes
        self.max_d2_per_feature = max_d2_per_feature
        self.concurrency = concurrency

    async def analyze(
        self, run_id: str, novelty: NoveltyResult
    ) -> list[InventiveStepOutput]:
        if novelty.conclusion == "NOT_NOVEL":
            return []
        routes = self._build_routes(run_id, novelty)
        if not routes:
            raise AgentExecutionError("no valid D1 route with distinguishing features")
        semaphore = asyncio.Semaphore(self.concurrency)

        async def execute(route: _RouteInput) -> tuple[_RouteInput, InventiveStepOutput]:
            async with semaphore:
                result = await self.agents.call_agent(
                    run_id,
                    "patent-inventive-step-analyzer",
                    system_prompt=INVENTIVE_STEP_PROMPT,
                    input_payload=route.payload,
                    input_size=len(canonical_json(route.payload)),
                )
                output = result.output
                if not isinstance(output, InventiveStepOutput):
                    raise AgentExecutionError("inventive-step analyzer returned wrong model")
                self._validate_output(route, output)
                return route, output

        completed = await asyncio.gather(*(execute(route) for route in routes))
        self._persist(run_id, completed)
        return [output for _, output in completed]

    def _build_routes(self, run_id: str, novelty: NoveltyResult) -> list[_RouteInput]:
        ranked = sorted(
            novelty.matrices,
            key=lambda matrix: (
                -sum(mapping.status == "DISCLOSED" for mapping in matrix.mappings),
                -sum(mapping.confidence for mapping in matrix.mappings),
                matrix.publication_number,
            ),
        )
        closest = next(
            (
                matrix
                for matrix in ranked
                if matrix.publication_number == novelty.closest_publication_number
            ),
            None,
        )
        ordered = ([closest] if closest else []) + [matrix for matrix in ranked if matrix is not closest]
        selected = [
            matrix for matrix in ordered if any(mapping.status != "DISCLOSED" for mapping in matrix.mappings)
        ][: self.max_routes]
        routes = []
        for index, d1 in enumerate(selected, 1):
            routes.append(self._route_for_matrix(run_id, f"R{index}", d1))
        return routes

    def _route_for_matrix(
        self, run_id: str, route_id: str, d1: NoveltyDocumentMatrix
    ) -> _RouteInput:
        with self.database.connect() as connection:
            d1_row = connection.execute(
                """
                SELECT d.document_id
                FROM run_documents rd JOIN patent_documents d ON d.document_id = rd.document_id
                WHERE rd.run_id = ? AND d.publication_number = ? AND rd.deep_reviewed = 1
                ORDER BY d.document_id LIMIT 1
                """,
                (run_id, d1.publication_number),
            ).fetchone()
            if d1_row is None:
                raise AgentExecutionError(f"D1 is not a deep-reviewed document: {d1.publication_number}")
            features = self._feature_index(connection, run_id)
            distinctions = [mapping for mapping in d1.mappings if mapping.status != "DISCLOSED"]
            allowed_publications: dict[str, set[str]] = {}
            evidence_publication: dict[str, dict[str, str]] = {}
            publication_status: dict[str, dict[str, str]] = {}
            publication_document_id: dict[str, str] = {}
            payload_distinctions = []
            for distinction in distinctions:
                internal_feature_id = features.get(distinction.feature_id)
                if internal_feature_id is None:
                    raise AgentExecutionError(
                        f"unknown distinguishing feature: {distinction.feature_id}"
                    )
                candidates = self._d2_candidates(
                    connection,
                    run_id,
                    d1_row["document_id"],
                    internal_feature_id,
                )
                allowed_publications[distinction.feature_id] = {
                    candidate["publication_number"] for candidate in candidates
                }
                evidence_publication[distinction.feature_id] = {}
                publication_status[distinction.feature_id] = {}
                for candidate in candidates:
                    publication_document_id[candidate["publication_number"]] = candidate[
                        "document_id"
                    ]
                    publication_status[distinction.feature_id][
                        candidate["publication_number"]
                    ] = candidate["coverage_status"]
                    for evidence in candidate["evidence"]:
                        evidence_publication[distinction.feature_id][
                            evidence["evidence_id"]
                        ] = candidate["publication_number"]
                payload_distinctions.append(
                    {
                        "feature_id": distinction.feature_id,
                        "d1_status": distinction.status,
                        "d1_rationale": distinction.rationale,
                        "d2_candidates": [
                            {key: value for key, value in candidate.items() if key != "document_id"}
                            for candidate in candidates
                        ],
                    }
                )
        payload = {
            "route_id": route_id,
            "d1_publication_number": d1.publication_number,
            "distinguishing_features": payload_distinctions,
            "rules": {
                "use_only_supplied_evidence": True,
                "not_inventive_requires_every_difference": True,
                "missing_evidence_status": "NEED_MORE_EVIDENCE",
            },
        }
        return _RouteInput(
            route_id=route_id,
            d1_document_id=d1_row["document_id"],
            d1_publication_number=d1.publication_number,
            distinguishing_feature_ids=tuple(item.feature_id for item in distinctions),
            payload=payload,
            allowed_publications=allowed_publications,
            evidence_publication=evidence_publication,
            publication_status=publication_status,
            publication_document_id=publication_document_id,
        )

    @staticmethod
    def _feature_index(connection, run_id: str) -> dict[str, str]:
        result = {}
        rows = connection.execute(
            "SELECT feature_id,metadata_json FROM idea_features WHERE run_id = ?", (run_id,)
        ).fetchall()
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError as exc:
                raise AgentExecutionError("invalid idea feature metadata") from exc
            external_id = metadata.get("external_feature_id")
            if isinstance(external_id, str):
                result[external_id] = row["feature_id"]
        return result

    def _d2_candidates(
        self,
        connection,
        run_id: str,
        d1_document_id: str,
        feature_id: str,
    ) -> list[dict]:
        rows = connection.execute(
            """
            SELECT fm.document_id,fm.coverage_status,fm.confidence,fm.evidence_ids_json,
                   fm.rationale,d.publication_number
            FROM feature_mappings fm
            JOIN patent_documents d ON d.document_id = fm.document_id
            JOIN run_documents rd ON rd.run_id = fm.run_id AND rd.document_id = fm.document_id
            WHERE fm.run_id = ? AND fm.feature_id = ? AND fm.document_id != ?
              AND fm.coverage_status IN ('DISCLOSED','PARTIAL') AND rd.deep_reviewed = 1
            ORDER BY CASE fm.coverage_status WHEN 'DISCLOSED' THEN 0 ELSE 1 END,
                     fm.confidence DESC,d.publication_number
            LIMIT ?
            """,
            (run_id, feature_id, d1_document_id, self.max_d2_per_feature),
        ).fetchall()
        candidates = []
        for row in rows:
            try:
                evidence_ids = json.loads(row["evidence_ids_json"])
            except json.JSONDecodeError as exc:
                raise AgentExecutionError("invalid D2 evidence ID JSON") from exc
            evidence = []
            for evidence_id in evidence_ids:
                item = connection.execute(
                    """SELECT section_type,section_label,quote_text,content_hash
                    FROM evidence WHERE evidence_id = ? AND run_id = ? AND document_id = ?""",
                    (evidence_id, run_id, row["document_id"]),
                ).fetchone()
                if item is None:
                    raise AgentExecutionError(f"missing D2 evidence: {evidence_id}")
                actual_hash = hashlib.sha256(item["quote_text"].encode("utf-8")).hexdigest()
                if actual_hash != item["content_hash"]:
                    raise AgentExecutionError(f"D2 evidence hash mismatch: {evidence_id}")
                evidence.append(
                    {
                        "evidence_id": evidence_id,
                        "section_type": item["section_type"],
                        "section_label": item["section_label"],
                        "quote_text": item["quote_text"],
                    }
                )
            candidates.append(
                {
                    "document_id": row["document_id"],
                    "publication_number": row["publication_number"],
                    "coverage_status": row["coverage_status"],
                    "confidence": row["confidence"],
                    "rationale": row["rationale"],
                    "evidence": evidence,
                }
            )
        return candidates

    @staticmethod
    def _validate_output(route: _RouteInput, output: InventiveStepOutput) -> None:
        if output.route_id != route.route_id:
            raise AgentExecutionError("inventive route ID mismatch")
        if output.d1_publication_number != route.d1_publication_number:
            raise AgentExecutionError("inventive D1 publication mismatch")
        actual_features = [item.feature_id for item in output.distinguishing_features]
        if len(actual_features) != len(set(actual_features)):
            raise AgentExecutionError("inventive output contains duplicate distinguishing features")
        if set(actual_features) != set(route.distinguishing_feature_ids):
            raise AgentExecutionError("inventive output does not cover exact distinguishing features")
        for item in output.distinguishing_features:
            allowed_publications = route.allowed_publications[item.feature_id]
            if len(item.d2_publication_numbers) != len(set(item.d2_publication_numbers)):
                raise AgentExecutionError("inventive output contains duplicate D2 publications")
            unknown_publications = set(item.d2_publication_numbers) - allowed_publications
            if unknown_publications:
                raise AgentExecutionError(
                    f"inventive output cites unknown D2 publications: {sorted(unknown_publications)}"
                )
            bound_publications = set()
            for evidence_id in item.evidence_ids:
                publication = route.evidence_publication[item.feature_id].get(evidence_id)
                if publication is None or publication not in item.d2_publication_numbers:
                    raise AgentExecutionError(
                        f"inventive output cites unbound D2 evidence: {evidence_id}"
                    )
                bound_publications.add(publication)
            if set(item.d2_publication_numbers) != bound_publications:
                raise AgentExecutionError("every cited D2 publication requires bound evidence")
            if output.status == "NOT_INVENTIVE" and not any(
                route.publication_status[item.feature_id][publication] == "DISCLOSED"
                for publication in item.d2_publication_numbers
            ):
                raise AgentExecutionError(
                    "NOT_INVENTIVE requires a fully disclosed D2 teaching for every distinction"
                )

    def _persist(
        self,
        run_id: str,
        completed: list[tuple[_RouteInput, InventiveStepOutput]],
    ) -> None:
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT COUNT(*) FROM inventive_routes WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            if existing:
                raise AgentExecutionError("inventive routes already exist for this run")
            timestamp = now_ms()
            for route, output in completed:
                d2_publications = sorted(
                    {
                        publication
                        for item in output.distinguishing_features
                        for publication in item.d2_publication_numbers
                    }
                )
                d2_document_ids = [
                    route.publication_document_id[publication]
                    for publication in d2_publications
                ]
                connection.execute(
                    """
                    INSERT INTO inventive_routes(
                        route_id,run_id,d1_document_id,d2_document_ids_json,
                        status,result_json,created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        f"{run_id}:{route.route_id}",
                        run_id,
                        route.d1_document_id,
                        canonical_json(d2_document_ids),
                        output.status,
                        canonical_json(output.model_dump(mode="json")),
                        timestamp,
                    ),
                )
