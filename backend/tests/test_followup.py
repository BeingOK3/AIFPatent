from __future__ import annotations

import inspect
import unittest

from idea.followup import (
    FollowupError,
    FollowupScope,
    FollowupScopeDocument,
    question_hash,
)
from idea.postgres_followup import PostgreSQLFollowupRepository


def document(number: int) -> FollowupScopeDocument:
    return FollowupScopeDocument(
        document_id=f"doc-{number}",
        version_id=f"cv-{number}",
        publication_number=f"CN{number}A",
        content_sha256=(str(number) * 64),
    )


class FollowupDomainTests(unittest.TestCase):
    def test_scope_hash_is_deterministic_and_selection_creates_new_snapshot(self) -> None:
        first = FollowupScope.freeze([document(2), document(1)])
        same = FollowupScope.freeze([document(1), document(2)])
        selected = first.select_publications(["CN2A"])

        self.assertEqual(first.corpus_snapshot_hash, same.corpus_snapshot_hash)
        self.assertEqual(first.version_ids, ("cv-1", "cv-2"))
        self.assertEqual(selected.version_ids, ("cv-2",))
        self.assertNotEqual(selected.corpus_snapshot_hash, first.corpus_snapshot_hash)

    def test_scope_rejects_duplicates_outside_selection_and_tampering(self) -> None:
        with self.assertRaisesRegex(FollowupError, "Version IDs must be unique"):
            FollowupScope.freeze([document(1), document(1)])
        scope = FollowupScope.freeze([document(1)])
        with self.assertRaisesRegex(FollowupError, "outside the source Run"):
            scope.select_publications(["CN9A"])
        with self.assertRaisesRegex(FollowupError, "hash does not match"):
            FollowupScope.from_json(scope.as_json(), "f" * 64)

    def test_question_hash_normalizes_spacing_but_preserves_question_content(self) -> None:
        self.assertEqual(question_hash("缓存  淘汰\n方法"), question_hash("缓存 淘汰 方法"))
        self.assertNotEqual(question_hash("缓存淘汰方法"), question_hash("缓存 淘汰 方法"))
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            question_hash("  \n ")

    def test_repository_mutations_have_no_api_key_parameter(self) -> None:
        for method_name in (
            "create_thread",
            "create_turn",
            "start_turn",
            "save_plan",
            "complete_turn",
            "fail_turn",
            "cancel_turn",
        ):
            parameters = inspect.signature(
                getattr(PostgreSQLFollowupRepository, method_name)
            ).parameters
            lowered = " ".join(parameters).lower()
            self.assertNotIn("api_key", lowered)
            self.assertNotIn("authorization", lowered)

    def test_record_retrieval_accepts_only_explicit_context_selection(self) -> None:
        signature = inspect.signature(PostgreSQLFollowupRepository.record_retrieval)
        selected = signature.parameters["selected_chunk_ids"]
        self.assertEqual(selected.kind, inspect.Parameter.KEYWORD_ONLY)


if __name__ == "__main__":
    unittest.main()
