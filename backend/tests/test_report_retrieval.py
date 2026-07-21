from __future__ import annotations

import asyncio
import unittest

from idea.agent_schemas import IdeaFeature
from idea.chunks import PatentChunk
from idea.hybrid import HybridHit, HybridSearchResult, RetrievalMode
from idea.lexical import LexicalHit
from idea.report_retrieval import (
    InitialReportRetriever,
    ReportDocumentScope,
    ReportRetrievalError,
)


def chunk(version_id: str, publication_number: str, suffix: str) -> PatentChunk:
    text = f"cache heat eviction {suffix}"
    return PatentChunk(
        chunk_id=f"chunk-{version_id}-{suffix}",
        version_id=version_id,
        publication_number=publication_number,
        section_type="description",
        section_label=f"paragraph-{suffix}",
        claim_number=None,
        claim_kind=None,
        parent_claim_numbers=(),
        start_offset=0,
        end_offset=len(text),
        text=text,
        text_hash="a" * 64,
        token_count=4,
        chunker_version="claims-paragraphs-v1",
    )


def structured_chunk(
    version_id: str,
    publication_number: str,
    *,
    section_type: str,
    label: str,
    claim_number: int | None = None,
    claim_kind: str | None = None,
    parents: tuple[int, ...] = (),
) -> PatentChunk:
    text = f"{label} evidence text"
    return PatentChunk(
        chunk_id=f"chunk-{version_id}-{label}",
        version_id=version_id,
        publication_number=publication_number,
        section_type=section_type,
        section_label=label,
        claim_number=claim_number,
        claim_kind=claim_kind,
        parent_claim_numbers=parents,
        start_offset=0,
        end_offset=len(text),
        text=text,
        text_hash="b" * 64,
        token_count=3,
        chunker_version="claims-paragraphs-v1",
    )


class FakeScopeRepository:
    def __init__(self, scopes):
        self.scopes = tuple(scopes)
        self.prepared = []
        self.persisted = []

    async def prepare_features(self, run_id, features):
        self.prepared.append((run_id, tuple(features)))

    async def load_ready_deep_reviewed(self, run_id):
        self.loaded_run_id = run_id
        return self.scopes

    async def load_ready_for_analysis(self, run_id, allowed_version_ids):
        self.loaded_for_analysis = (run_id, tuple(allowed_version_ids))
        return tuple(
            item for item in self.scopes if item.version_id in allowed_version_ids
        )

    async def persist(self, result):
        self.persisted.append(result)


class FakeLexicalSearch:
    def __init__(self):
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        version_id = request.allowed_version_ids[0]
        publication = {"cv-1": "CN1A", "cv-2": "CN2A"}[version_id]
        return (
            LexicalHit(
                query_id=request.query_id,
                rank=1,
                lexical_score=0.75,
                match_kind="fts",
                chunk=chunk(version_id, publication, request.query_id[-2:]),
            ),
        )


class FakeChunkRepository:
    def __init__(self, chunks):
        self.chunks = tuple(chunks)

    async def list_for_versions(self, version_ids):
        self.requested = tuple(version_ids)
        return self.chunks


class FakeHybridSearch:
    def __init__(self, hits=()):
        self.hits = tuple(hits)
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return HybridSearchResult(
            query_id=request.query_id,
            mode=RetrievalMode.LEXICAL_ONLY,
            retriever_version="hybrid-rrf-v1",
            hits=self.hits,
            limitations=("LEXICAL_ONLY",),
        )


class FakeQueryEmbedding:
    def __init__(self):
        self.queries = []

    async def embed_query(self, text):
        self.queries.append(text)
        return (0.0, 1.0), "ep-fixture"


class InitialReportRetrieverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.features = (
            IdeaFeature(
                feature_id="F1", feature_text="cache eviction",
                source_type="normalized", required=True,
            ),
            IdeaFeature(
                feature_id="F2", feature_text="heat score",
                source_type="normalized", required=True,
            ),
        )
        self.scopes = (
            ReportDocumentScope("doc-1", "cv-1", "CN1A"),
            ReportDocumentScope("doc-2", "cv-2", "CN2A"),
        )

    def test_retrieves_every_feature_document_pair_with_single_version_scope(self) -> None:
        repository = FakeScopeRepository(self.scopes)
        lexical = FakeLexicalSearch()

        result = asyncio.run(
            InitialReportRetriever(repository, lexical).retrieve(
                run_id="run-1", features=self.features
            )
        )

        self.assertEqual(len(lexical.requests), 4)
        self.assertEqual(
            {(item.feature_id, item.version_id) for item in result.queries},
            {("F1", "cv-1"), ("F1", "cv-2"), ("F2", "cv-1"), ("F2", "cv-2")},
        )
        self.assertTrue(all(len(request.allowed_version_ids) == 1 for request in lexical.requests))
        self.assertEqual(result.corpus_version_ids, ("cv-1", "cv-2"))
        self.assertEqual(repository.persisted, [result])

    def test_shared_hybrid_path_preserves_ranks_scores_and_explicit_fallback(self) -> None:
        evidence = chunk("cv-1", "CN1A", "hybrid")
        hybrid = FakeHybridSearch((HybridHit(
            chunk=evidence,
            lexical_rank=2,
            vector_rank=None,
            rrf_score=0.02,
            section_weight=1.05,
            final_score=0.021,
            sources=("lexical",),
        ),))
        embedding = FakeQueryEmbedding()
        result = asyncio.run(InitialReportRetriever(
            FakeScopeRepository(self.scopes[:1]),
            FakeLexicalSearch(),
            hybrid_search=hybrid,
            query_embedding=embedding,
        ).retrieve(run_id="run-1", features=self.features[:1]))

        self.assertEqual(result.retriever_version, "hybrid-rrf-v1")
        self.assertEqual(result.limitations, ("LEXICAL_ONLY",))
        self.assertEqual(len(hybrid.requests), 1)
        self.assertEqual(hybrid.requests[0].allowed_version_ids, ("cv-1",))
        self.assertEqual(hybrid.requests[0].semantic_embedding, (0.0, 1.0))
        self.assertEqual(hybrid.requests[0].embedding_profile_id, "ep-fixture")
        self.assertEqual(embedding.queries, ["cache eviction"])
        self.assertEqual(result.selections[0].selection_reason, "hybrid")
        self.assertEqual(result.selections[0].hit.lexical_rank, 2)
        self.assertEqual(result.selections[0].hit.rrf_score, 0.02)
        self.assertEqual(result.selections[0].hit.final_rank, 1)

    def test_scope_must_be_nonempty_and_identity_unique(self) -> None:
        with self.assertRaisesRegex(ReportRetrievalError, "READY deep-reviewed"):
            asyncio.run(
                InitialReportRetriever(FakeScopeRepository(()), FakeLexicalSearch()).retrieve(
                    run_id="run-1", features=self.features
                )
            )

        duplicate = self.scopes + (
            ReportDocumentScope("doc-other", "cv-1", "CN1A"),
        )
        with self.assertRaisesRegex(ReportRetrievalError, "unique Version"):
            asyncio.run(
                InitialReportRetriever(
                    FakeScopeRepository(duplicate), FakeLexicalSearch()
                ).retrieve(run_id="run-1", features=self.features)
            )

    def test_only_required_features_enter_the_matrix(self) -> None:
        optional = IdeaFeature(
            feature_id="F3", feature_text="optional telemetry",
            source_type="normalized", required=False,
        )
        repository = FakeScopeRepository(self.scopes[:1])
        lexical = FakeLexicalSearch()

        result = asyncio.run(
            InitialReportRetriever(repository, lexical).retrieve(
                run_id="run-1", features=self.features + (optional,)
            )
        )

        self.assertEqual({item.feature_id for item in result.queries}, {"F1", "F2"})
        self.assertEqual(len(lexical.requests), 2)

    def test_frozen_checkpoint_versions_are_used_before_deep_review(self) -> None:
        repository = FakeScopeRepository(self.scopes)
        result = asyncio.run(
            InitialReportRetriever(repository, FakeLexicalSearch()).retrieve(
                run_id="run-1",
                features=self.features[:1],
                allowed_version_ids=("cv-2",),
            )
        )
        self.assertEqual(repository.loaded_for_analysis, ("run-1", ("cv-2",)))
        self.assertEqual(result.corpus_version_ids, ("cv-2",))

    def test_frozen_checkpoint_scope_must_match_database_exactly(self) -> None:
        with self.assertRaisesRegex(ReportRetrievalError, "does not match"):
            asyncio.run(
                InitialReportRetriever(
                    FakeScopeRepository(self.scopes[:1]), FakeLexicalSearch()
                ).retrieve(
                    run_id="run-1",
                    features=self.features[:1],
                    allowed_version_ids=("cv-1", "cv-2"),
                )
            )

    def test_empty_required_feature_set_fails_closed(self) -> None:
        optional = IdeaFeature(
            feature_id="F1", feature_text="optional",
            source_type="normalized", required=False,
        )
        with self.assertRaisesRegex(ReportRetrievalError, "required feature"):
            asyncio.run(
                InitialReportRetriever(
                    FakeScopeRepository(self.scopes), FakeLexicalSearch()
                ).retrieve(run_id="run-1", features=(optional,))
            )

    def test_forces_abstract_and_every_independent_claim_for_each_document(self) -> None:
        chunks = (
            structured_chunk("cv-1", "CN1A", section_type="abstract", label="abstract"),
            structured_chunk(
                "cv-1", "CN1A", section_type="claims", label="claim-1",
                claim_number=1, claim_kind="independent",
            ),
            structured_chunk(
                "cv-1", "CN1A", section_type="claims", label="claim-5",
                claim_number=5, claim_kind="independent",
            ),
        )
        result = asyncio.run(
            InitialReportRetriever(
                FakeScopeRepository(self.scopes[:1]), FakeLexicalSearch(),
                chunk_repository=FakeChunkRepository(chunks),
            ).retrieve(run_id="run-1", features=self.features[:1])
        )

        forced = {
            (item.selection_reason, item.hit.chunk.section_label)
            for item in result.selections
            if item.selection_reason.startswith("forced_")
        }
        self.assertEqual(
            forced,
            {
                ("forced_abstract", "abstract"),
                ("forced_claim", "claim-1"),
                ("forced_claim", "claim-5"),
            },
        )

    def test_selected_dependent_claim_recursively_adds_parent_chain(self) -> None:
        claim_1 = structured_chunk(
            "cv-1", "CN1A", section_type="claims", label="claim-1",
            claim_number=1, claim_kind="independent",
        )
        claim_2 = structured_chunk(
            "cv-1", "CN1A", section_type="claims", label="claim-2",
            claim_number=2, claim_kind="dependent", parents=(1,),
        )
        claim_3 = structured_chunk(
            "cv-1", "CN1A", section_type="claims", label="claim-3",
            claim_number=3, claim_kind="dependent", parents=(2,),
        )
        abstract = structured_chunk(
            "cv-1", "CN1A", section_type="abstract", label="abstract"
        )

        class DependentLexical(FakeLexicalSearch):
            async def search(self, request):
                self.requests.append(request)
                return (LexicalHit(request.query_id, 1, 0.9, "fts", claim_3),)

        result = asyncio.run(
            InitialReportRetriever(
                FakeScopeRepository(self.scopes[:1]), DependentLexical(),
                chunk_repository=FakeChunkRepository((abstract, claim_1, claim_2, claim_3)),
            ).retrieve(run_id="run-1", features=self.features[:1])
        )

        forced_claims = {
            item.hit.chunk.claim_number
            for item in result.selections
            if item.selection_reason == "forced_claim"
        }
        self.assertEqual(forced_claims, {1, 2})

    def test_missing_parent_claim_fails_closed(self) -> None:
        abstract = structured_chunk(
            "cv-1", "CN1A", section_type="abstract", label="abstract"
        )
        independent = structured_chunk(
            "cv-1", "CN1A", section_type="claims", label="claim-1",
            claim_number=1, claim_kind="independent",
        )
        dependent = structured_chunk(
            "cv-1", "CN1A", section_type="claims", label="claim-3",
            claim_number=3, claim_kind="dependent", parents=(2,),
        )

        class DependentLexical(FakeLexicalSearch):
            async def search(self, request):
                self.requests.append(request)
                return (LexicalHit(request.query_id, 1, 0.9, "fts", dependent),)

        with self.assertRaisesRegex(ReportRetrievalError, "parent claim 2"):
            asyncio.run(
                InitialReportRetriever(
                    FakeScopeRepository(self.scopes[:1]), DependentLexical(),
                    chunk_repository=FakeChunkRepository((abstract, independent, dependent)),
                ).retrieve(run_id="run-1", features=self.features[:1])
            )

    def test_missing_mandatory_abstract_or_independent_claim_fails_closed(self) -> None:
        abstract = structured_chunk(
            "cv-1", "CN1A", section_type="abstract", label="abstract"
        )
        independent = structured_chunk(
            "cv-1", "CN1A", section_type="claims", label="claim-1",
            claim_number=1, claim_kind="independent",
        )
        for available, expected in (
            ((independent,), "abstract evidence"),
            ((abstract,), "independent-claim evidence"),
        ):
            with self.subTest(expected=expected), self.assertRaisesRegex(
                ReportRetrievalError, expected
            ):
                asyncio.run(
                    InitialReportRetriever(
                        FakeScopeRepository(self.scopes[:1]), FakeLexicalSearch(),
                        chunk_repository=FakeChunkRepository(available),
                    ).retrieve(run_id="run-1", features=self.features[:1])
                )

    def test_merged_claim_one_is_independent_despite_later_reference_noise(self) -> None:
        abstract = structured_chunk(
            "cv-1", "CN1A", section_type="abstract", label="abstract"
        )
        merged_claim_one = structured_chunk(
            "cv-1", "CN1A", section_type="claims", label="claim-1",
            claim_number=1, claim_kind="dependent", parents=(6,),
        )

        class MergedClaimLexical(FakeLexicalSearch):
            async def search(self, request):
                self.requests.append(request)
                return (LexicalHit(request.query_id, 1, 0.9, "fts", merged_claim_one),)

        result = asyncio.run(
            InitialReportRetriever(
                FakeScopeRepository(self.scopes[:1]), MergedClaimLexical(),
                chunk_repository=FakeChunkRepository((abstract, merged_claim_one)),
            ).retrieve(run_id="run-1", features=self.features[:1])
        )

        forced = [
            item for item in result.selections
            if item.selection_reason == "forced_claim"
        ]
        self.assertEqual([item.hit.chunk.claim_number for item in forced], [1])


if __name__ == "__main__":
    unittest.main()
