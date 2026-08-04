from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from landscape.api import create_landscape_router
from landscape.scope_service import ScopeDraftPreparationService
from tests.test_landscape_scope_service import ExpansionStub, MemoryRepository
from tests.test_landscape_query_planning import confirmed_scope
from landscape.credential_lease import CredentialVault


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

    def get_confirmed(self, scope_revision_id):
        scope = confirmed_scope(companies=(("华为", ("华为", "Huawei")),))
        if scope_revision_id != "SCR-0000000000000001":
            raise KeyError(scope_revision_id)
        return scope.model_copy(
            update={
                "scope_revision_id": scope_revision_id,
                # This fake is used only to exercise API orchestration. The
                # production repository validates this binding from rows.
                "scope_revision_hash": scope.scope_revision_hash,
            }
        )


class ApiRunRepository:
    def __init__(self):
        self.calls = []

    def create(self, **values):
        self.calls.append(values)
        self.run = SimpleNamespace(
            run_id="LRN-0000000000000001",
            scope_revision_id=values["scope_revision_id"],
            taxonomy_version=values["taxonomy_version"],
            status="PLANNING",
        )
        return self.run

    def get(self, run_id):
        if run_id != self.run.run_id:
            raise KeyError(run_id)
        values = vars(self.run).copy()
        values["status"] = "ESTIMATING"
        return SimpleNamespace(**values)

    def list(self, limit=100):
        return (self.get(self.run.run_id),) if hasattr(self, "run") else ()


class ApiQueryPlanRepository:
    def __init__(self):
        self.plans = {}

    def put(self, run_id, plan):
        self.plans[run_id] = plan
        return plan


class ApiScaleRepository:
    def decide(self, run_id, *, approve):
        return {"run_id": run_id, "decision": "APPROVED" if approve else "REJECTED"}

    def get(self, run_id):
        raise KeyError(run_id)


class ApiStageRepository:
    def __init__(self):
        self.runs = set()

    def ensure(self, run_id):
        self.runs.add(run_id)
        return ()

    def list(self, run_id):
        if run_id not in self.runs:
            raise KeyError(run_id)
        return ()

    def limitations(self, run_id):
        return ()


def app_fixture():
    repository = ApiRepository()
    service = ScopeDraftPreparationService(repository, ExpansionStub(delay=0))
    run_repository = ApiRunRepository()
    runtime = SimpleNamespace(
        database=object(),
        store=object(),
        tasks=object(),
        scope_repository=repository,
        scope_service=service,
        run_repository=run_repository,
        query_repository=ApiQueryPlanRepository(),
        scale_repository=ApiScaleRepository(),
        stage_repository=ApiStageRepository(),
        credential_vault=CredentialVault(),
        taxonomy=SimpleNamespace(taxonomy_version="landscape-taxonomy/fixture"),
    )
    app = FastAPI()
    app.include_router(create_landscape_router(runtime))
    return app, repository


class LandscapeScopeApiTests(unittest.TestCase):
    def test_formal_run_accepts_only_a_confirmed_scope_revision(self) -> None:
        async def scenario():
            app, _repository = app_fixture()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                bypass = await client.post(
                    "/api/landscape/runs",
                    json={
                        "scope": {"mode": "COMPANY_ONLY"},
                        "api_key": "must-not-be-accepted",
                        "base_url": "https://example.test/v1",
                        "model": "old-model",
                    },
                )
                self.assertEqual(bypass.status_code, 422)
                created = await client.post(
                    "/api/landscape/runs",
                    json={"scope_revision_id": "SCR-0000000000000001"},
                )
                self.assertEqual(created.status_code, 201)
                self.assertEqual(created.json()["status"], "ESTIMATING")
                decision = await client.post(
                    "/api/landscape/runs/LRN-0000000000000001/scale-decision",
                    json={"approve": True},
                )
                self.assertEqual(decision.status_code, 200)
                self.assertEqual(decision.json()["decision"], "APPROVED")
                credentials = await client.post(
                    "/api/landscape/runs/LRN-0000000000000001/credentials",
                    json={"api_key":"temporary-secret","base_url":"https://example.test/v1","model":"fixture"},
                )
                self.assertEqual(credentials.status_code, 200)
                self.assertNotIn("temporary-secret", credentials.text)

        asyncio.run(scenario())

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
                run_repository=ApiRunRepository(),
                taxonomy=SimpleNamespace(
                    taxonomy_version="landscape-taxonomy/fixture"
                ),
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
