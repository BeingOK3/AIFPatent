from __future__ import annotations

from enum import StrEnum

from idea.providers.base import FetchedDocument, PagedSearchProvider

from .abstract_evidence import abstract_from_document, abstract_provider_failure
from .family_resolution import resolve_families
from .organization_assignment import assign_organizations
from .patent_snapshot import PatentSnapshotStatus, snapshot_bibliography
from .stage_repository import V4StageName, V4StageStatus
from .v4_run import LandscapeRunStatus


class V4WorkflowOutcome(StrEnum):
    AWAITING_SCALE_CONFIRMATION = "AWAITING_SCALE_CONFIRMATION"
    PREANALYSIS_COMPLETE = "PREANALYSIS_COMPLETE"


class V4WorkflowError(RuntimeError):
    pass


class V4LandscapeWorkflow:
    """Checkpointed serial v4 workflow.

    This first executable slice ends after abstract evidence and organization
    assignment. Later semantic stages attach to the same stage ledger.
    """

    def __init__(
        self,
        *,
        run_repository,
        scope_repository,
        stage_repository,
        search_coordinator,
        publication_repository,
        snapshot_fetch,
        snapshot_repository,
        family_repository,
        abstract_repository,
        organization_repository,
        provider: PagedSearchProvider,
    ):
        self.run_repository = run_repository
        self.scope_repository = scope_repository
        self.stage_repository = stage_repository
        self.search_coordinator = search_coordinator
        self.publication_repository = publication_repository
        self.snapshot_fetch = snapshot_fetch
        self.snapshot_repository = snapshot_repository
        self.family_repository = family_repository
        self.abstract_repository = abstract_repository
        self.organization_repository = organization_repository
        self.provider = provider

    async def execute_preanalysis(self, run_id: str) -> V4WorkflowOutcome:
        self.stage_repository.ensure(run_id)
        run = self.run_repository.get(run_id)
        if run.status == LandscapeRunStatus.PLANNING:
            run = self.run_repository.transition(
                run_id,
                LandscapeRunStatus.ESTIMATING,
                expected=(LandscapeRunStatus.PLANNING,),
            )
        if not self._succeeded(run_id, V4StageName.ESTIMATE_SCALE):
            plan = self.search_coordinator.query_repository.get(run_id)
            self.stage_repository.start(
                run_id, V4StageName.ESTIMATE_SCALE, total_count=len(plan.queries)
            )
            await self.search_coordinator.estimate(run_id, self.provider)
            self.stage_repository.succeed(run_id, V4StageName.ESTIMATE_SCALE)
        run = self.run_repository.get(run_id)
        if run.status == LandscapeRunStatus.AWAITING_SCALE_CONFIRMATION:
            return V4WorkflowOutcome.AWAITING_SCALE_CONFIRMATION
        if run.status == LandscapeRunStatus.READY:
            self.run_repository.transition(
                run_id,
                LandscapeRunStatus.RUNNING,
                expected=(LandscapeRunStatus.READY,),
            )
        elif run.status != LandscapeRunStatus.RUNNING:
            raise V4WorkflowError(
                f"preanalysis cannot execute from run status {run.status.value}"
            )

        results = await self._retrieve(run_id)
        frozen = self._freeze(run_id, results)
        snapshots = await self._resolve(run_id, frozen)
        self._fetch_abstracts_and_organizations(run_id, snapshots)
        return V4WorkflowOutcome.PREANALYSIS_COMPLETE

    async def _retrieve(self, run_id: str):
        if self._succeeded(run_id, V4StageName.RETRIEVE_PAGES):
            # Reconstruct from page checkpoints; no completed page is fetched again.
            return await self.search_coordinator.retrieve(run_id, self.provider)
        plan = self.search_coordinator.query_repository.get(run_id)
        self.stage_repository.start(
            run_id, V4StageName.RETRIEVE_PAGES, total_count=len(plan.queries)
        )
        results = await self.search_coordinator.retrieve(run_id, self.provider)
        self.stage_repository.progress(
            run_id,
            V4StageName.RETRIEVE_PAGES,
            completed_count=len(results),
            total_count=len(plan.queries),
        )
        self.stage_repository.succeed(run_id, V4StageName.RETRIEVE_PAGES)
        return results

    def _freeze(self, run_id: str, results):
        if self._succeeded(run_id, V4StageName.FREEZE_PUBLICATIONS):
            return self.publication_repository.get(run_id)
        self.stage_repository.start(run_id, V4StageName.FREEZE_PUBLICATIONS)
        frozen = self.search_coordinator.freeze(run_id, results)
        self.stage_repository.progress(
            run_id,
            V4StageName.FREEZE_PUBLICATIONS,
            completed_count=frozen.publication_count,
            total_count=frozen.publication_count,
        )
        self.stage_repository.succeed(run_id, V4StageName.FREEZE_PUBLICATIONS)
        return frozen

    async def _resolve(self, run_id: str, frozen):
        if self._succeeded(run_id, V4StageName.RESOLVE_FAMILIES):
            return self.snapshot_repository.get(run_id)
        self.stage_repository.start(
            run_id,
            V4StageName.RESOLVE_FAMILIES,
            total_count=frozen.publication_count,
        )

        async def progress(completed: int, total: int) -> None:
            self.stage_repository.progress(
                run_id,
                V4StageName.RESOLVE_FAMILIES,
                completed_count=completed,
                total_count=total,
            )

        snapshots = await self.snapshot_fetch.fetch(frozen, progress=progress)
        resolution = resolve_families(snapshot_bibliography(snapshots))
        self.family_repository.put(run_id, resolution)
        self.stage_repository.succeed(run_id, V4StageName.RESOLVE_FAMILIES)
        return snapshots

    def _fetch_abstracts_and_organizations(self, run_id: str, snapshots) -> None:
        if self._succeeded(run_id, V4StageName.FETCH_ABSTRACTS):
            return
        total = len(snapshots.snapshots)
        self.stage_repository.start(
            run_id, V4StageName.FETCH_ABSTRACTS, total_count=total
        )
        scope = self.scope_repository.get_confirmed(
            self.run_repository.get(run_id).scope_revision_id
        )
        for completed, snapshot in enumerate(snapshots.snapshots, start=1):
            if snapshot.status == PatentSnapshotStatus.AVAILABLE:
                evidence = abstract_from_document(
                    snapshot.publication_id,
                    FetchedDocument(
                        provider=snapshot.provider,
                        publication_number=snapshot.publication_number
                        or snapshot.publication_id,
                        title=snapshot.title,
                        assignee=snapshot.applicants[0] if snapshot.applicants else None,
                        assignees=list(snapshot.applicants),
                        url=snapshot.url,
                        language=snapshot.language,
                        abstract_text=snapshot.abstract_text,
                    ),
                )
            else:
                evidence = abstract_provider_failure(
                    snapshot.publication_id,
                    snapshot.provider,
                    snapshot.failure_reason or "PROVIDER_FAILED",
                )
            self.abstract_repository.put(run_id, evidence)
            self.stage_repository.progress(
                run_id,
                V4StageName.FETCH_ABSTRACTS,
                completed_count=completed,
                total_count=total,
            )
        organizations = assign_organizations(
            run_id,
            scope,
            {
                snapshot.publication_id: snapshot.applicants
                for snapshot in snapshots.snapshots
            },
        )
        self.organization_repository.put(organizations)
        limited = any(
            snapshot.status != PatentSnapshotStatus.AVAILABLE
            for snapshot in snapshots.snapshots
        )
        if limited:
            self.stage_repository.add_limitation(
                run_id,
                V4StageName.FETCH_ABSTRACTS,
                code="PATENT_DETAIL_FETCH_FAILED",
                message="Some patents could not be fetched and remain unresolved.",
                affected_count=sum(
                    snapshot.status != PatentSnapshotStatus.AVAILABLE
                    for snapshot in snapshots.snapshots
                ),
            )
        self.stage_repository.succeed(
            run_id,
            V4StageName.FETCH_ABSTRACTS,
            with_limitations=limited,
        )

    def _succeeded(self, run_id: str, stage_name: V4StageName) -> bool:
        return next(
            stage.status
            in {
                V4StageStatus.SUCCEEDED,
                V4StageStatus.SUCCEEDED_WITH_LIMITATIONS,
            }
            for stage in self.stage_repository.list(run_id)
            if stage.stage_name == stage_name
        )


__all__ = ["V4LandscapeWorkflow", "V4WorkflowError", "V4WorkflowOutcome"]
