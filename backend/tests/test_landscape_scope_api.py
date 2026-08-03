from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from landscape.api import create_landscape_router
from landscape.scope_service import ScopeDraftPreparationService
from tests.test_landscape_scope_service import ExpansionStub, MemoryRepository


class ApiRepository(MemoryRepository):
    def confirm(self, draft_id, *, expected_revision):
        current = self.drafts[draft_id]
        if current.revision != expected_revision:
            raise ValueError("stale confirmation")
        return {
            "scope_revision_id": "SCR-0000000000000001",
            "source_draft_id": draft_id,
            "source_draft_revision": expected_revision,
        }


def app_fixture():
    repository = ApiRepository()
    service = ScopeDraftPreparationService(repository, ExpansionStub(delay=0))
    runtime = SimpleNamespace(
        database=object(),
        store=object(),
        tasks=object(),
        scope_repository=repository,
        scope_service=service,
    )
    app = FastAPI()
    app.include_router(create_landscape_router(runtime))
    return app, repository


class LandscapeScopeApiTests(unittest.TestCase):
    def test_create_get_expand_patch_and_confirm_are_separate_resources(self) -> None:
        async def scenario():
            app, repository = app_fixture()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                created_response = await client.post(
                    "/api/landscape/scope-drafts",
                    json={
                        "company_names": ["华为"],
                        "publication_start": "2000-01-01",
                        "publication_end": "2026-12-31",
                    },
                )
                self.assertEqual(created_response.status_code, 201)
                created = created_response.json()
                self.assertEqual(created["status"], "DRAFT")
                self.assertEqual(created["revision"], 1)
                draft_id = created["draft_id"]
                fetched = await client.get(
                    f"/api/landscape/scope-drafts/{draft_id}"
                )
                self.assertEqual(fetched.json(), created)

                expanded_response = await client.post(
                    f"/api/landscape/scope-drafts/{draft_id}/expand",
                    json={
                        "expected_revision": 1,
                        "api_key": "temporary-test-key",
                        "base_url": "https://example.test/v1",
                        "model": "fixture-model",
                    },
                )
                self.assertEqual(expanded_response.status_code, 200)
                expanded = expanded_response.json()
                self.assertEqual(expanded["status"], "AWAITING_CONFIRMATION")
                self.assertEqual(expanded["revision"], 3)
                self.assertNotIn("temporary-test-key", str(repository.drafts))

                edited = dict(expanded)
                edited["revision"] = 4
                edited["publication_end"] = "2027-12-31"
                patched_response = await client.patch(
                    f"/api/landscape/scope-drafts/{draft_id}",
                    json={"expected_revision": 3, "draft": edited},
                )
                self.assertEqual(patched_response.status_code, 200)
                patched = patched_response.json()
                self.assertEqual(patched["publication_end"], "2027-12-31")
                self.assertEqual(patched["status"], "AWAITING_CONFIRMATION")

                confirmed = await client.post(
                    f"/api/landscape/scope-drafts/{draft_id}/confirm",
                    json={"expected_revision": 4},
                )
                self.assertEqual(confirmed.status_code, 200)
                self.assertEqual(confirmed.json()["source_draft_revision"], 4)

        asyncio.run(scenario())

    def test_stale_expand_and_body_path_mismatch_are_rejected(self) -> None:
        async def scenario():
            app, _repository = app_fixture()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                created = (
                    await client.post(
                        "/api/landscape/scope-drafts",
                        json={
                            "technology_input": "液冷",
                            "publication_start": "2020-01-01",
                            "publication_end": "2026-01-01",
                        },
                    )
                ).json()
                draft_id = created["draft_id"]
                stale = await client.post(
                    f"/api/landscape/scope-drafts/{draft_id}/expand",
                    json={
                        "expected_revision": 2,
                        "api_key": "temporary-test-key",
                        "base_url": "https://example.test/v1",
                        "model": "fixture-model",
                    },
                )
                self.assertEqual(stale.status_code, 409)

                changed = dict(created)
                changed["draft_id"] = "SCD-0000000000000099"
                changed["revision"] = 2
                mismatch = await client.patch(
                    f"/api/landscape/scope-drafts/{draft_id}",
                    json={"expected_revision": 1, "draft": changed},
                )
                self.assertEqual(mismatch.status_code, 422)

        asyncio.run(scenario())

    def test_secret_fields_are_rejected_inside_persisted_draft(self) -> None:
        async def scenario():
            app, _repository = app_fixture()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                created = (
                    await client.post(
                        "/api/landscape/scope-drafts",
                        json={
                            "company_names": ["华为"],
                            "publication_start": "2020-01-01",
                            "publication_end": "2026-01-01",
                        },
                    )
                ).json()
                created["api_key"] = "must-not-persist"
                created["revision"] = 2
                response = await client.patch(
                    f"/api/landscape/scope-drafts/{created['draft_id']}",
                    json={"expected_revision": 1, "draft": created},
                )
                self.assertEqual(response.status_code, 422)

        asyncio.run(scenario())

    def test_patch_cannot_erase_server_expansion_limitations(self) -> None:
        async def scenario():
            repository = ApiRepository()
            service = ScopeDraftPreparationService(
                repository,
                ExpansionStub(fail_companies={"华为"}, delay=0),
            )
            runtime = SimpleNamespace(
                database=object(),
                store=object(),
                tasks=object(),
                scope_repository=repository,
                scope_service=service,
            )
            app = FastAPI()
            app.include_router(create_landscape_router(runtime))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                created = (
                    await client.post(
                        "/api/landscape/scope-drafts",
                        json={
                            "company_names": ["华为"],
                            "publication_start": "2020-01-01",
                            "publication_end": "2026-01-01",
                        },
                    )
                ).json()
                expanded = (
                    await client.post(
                        f"/api/landscape/scope-drafts/{created['draft_id']}/expand",
                        json={
                            "expected_revision": 1,
                            "api_key": "temporary-test-key",
                            "base_url": "https://example.test/v1",
                            "model": "fixture-model",
                        },
                    )
                ).json()
                self.assertEqual(len(expanded["limitations"]), 1)
                expanded["limitations"] = []
                expanded["revision"] = 4
                patched = await client.patch(
                    f"/api/landscape/scope-drafts/{created['draft_id']}",
                    json={"expected_revision": 3, "draft": expanded},
                )
                self.assertEqual(patched.status_code, 200)
                self.assertEqual(len(patched.json()["limitations"]), 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
