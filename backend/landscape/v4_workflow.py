from __future__ import annotations

from enum import StrEnum

from idea.providers.base import FetchedDocument, PagedSearchProvider

from .abstract_evidence import abstract_from_document, abstract_provider_failure
from .abstract_evidence import AbstractStatus
from .analytics_assembly import assemble_metric_units
from .classification_terminal import ClassificationResult
from .direction_agent import DirectionUnitPacket
from .direction_record import DirectionRecord, DirectionStatus
from .family_resolution import resolve_families
from .organization_assignment import assign_organizations
from .others_discovery import discover_others_directions
from .patent_snapshot import PatentSnapshotStatus, snapshot_bibliography
from .metrics import build_metric_cube
from .representatives import select_representative_patents
from .report_v4 import build_report_v4, render_report_markdown
from .trends import build_trend_candidates
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
        direction_extraction=None,
        classification_matching=None,
        direction_repository=None,
        classification_repository=None,
        taxonomy_repository=None,
        others_repository=None,
        metric_repository=None,
        trend_repository=None,
        representative_repository=None,
        report_repository=None,
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
        self.direction_extraction = direction_extraction
        self.classification_matching = classification_matching
        self.direction_repository = direction_repository
        self.classification_repository = classification_repository
        self.taxonomy_repository = taxonomy_repository
        self.others_repository = others_repository
        self.metric_repository = metric_repository
        self.trend_repository = trend_repository
        self.representative_repository = representative_repository
        self.report_repository = report_repository

    async def execute_full(self, run_id: str) -> V4WorkflowOutcome:
        outcome = await self.execute_through_analytics(run_id)
        if outcome == V4WorkflowOutcome.AWAITING_SCALE_CONFIRMATION:
            return outcome
        self._build_report(run_id)
        self._verify_run(run_id)
        return outcome

    def execute_after_semantics(self, run_id: str) -> None:
        self.execute_analytics(run_id)
        self._build_report(run_id)
        self._verify_run(run_id)

    async def execute_through_analytics(self, run_id: str) -> V4WorkflowOutcome:
        outcome = await self.execute_through_classification(run_id)
        if outcome == V4WorkflowOutcome.AWAITING_SCALE_CONFIRMATION:
            return outcome
        self.execute_analytics(run_id)
        return outcome

    async def execute_through_classification(self, run_id: str) -> V4WorkflowOutcome:
        outcome = await self.execute_preanalysis(run_id)
        if outcome == V4WorkflowOutcome.AWAITING_SCALE_CONFIRMATION:
            return outcome
        await self.execute_semantics(run_id)
        return outcome

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

    async def execute_semantics(self, run_id: str) -> None:
        required = {
            "direction_extraction": self.direction_extraction,
            "classification_matching": self.classification_matching,
            "direction_repository": self.direction_repository,
            "classification_repository": self.classification_repository,
            "taxonomy_repository": self.taxonomy_repository,
            "others_repository": self.others_repository,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise V4WorkflowError(
                "semantic workflow dependencies are missing: " + ", ".join(missing)
            )
        run = self.run_repository.get(run_id)
        taxonomy = self.taxonomy_repository.get(run.taxonomy_version)
        directions = await self._extract_directions(run_id, taxonomy)
        classifications = await self._match_taxonomy(run_id, directions, taxonomy)
        self._discover_others(run_id, directions, classifications)

    def execute_analytics(self, run_id: str) -> None:
        required = {
            "metric_repository": self.metric_repository,
            "trend_repository": self.trend_repository,
            "representative_repository": self.representative_repository,
            "organization_repository": self.organization_repository,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise V4WorkflowError(
                "analytics workflow dependencies are missing: " + ", ".join(missing)
            )
        run = self.run_repository.get(run_id)
        taxonomy = self.taxonomy_repository.get(run.taxonomy_version)
        resolution = self.family_repository.get(run_id)
        directions = tuple(
            self.direction_repository.get(run_id, unit.analysis_unit_id)
            for unit in resolution.analysis_units
        )
        classifications = tuple(
            self.classification_repository.get(run_id, unit.analysis_unit_id)
            for unit in resolution.analysis_units
        )
        assembly = assemble_metric_units(
            resolution,
            self.snapshot_repository.get(run_id),
            self.organization_repository.get(run_id),
            classifications,
            directions,
            self.others_repository.get(run_id),
            taxonomy,
        )
        cube = self._compute_metrics(run_id, run, assembly)
        self._build_trends(run_id, cube, assembly.units)
        self._select_representatives(run_id, assembly.units)

    def _compute_metrics(self, run_id: str, run, assembly):
        if self._succeeded(run_id, V4StageName.COMPUTE_METRICS):
            return self.metric_repository.get(run_id)
        self.stage_repository.start(
            run_id,
            V4StageName.COMPUTE_METRICS,
            total_count=len(assembly.units),
        )
        cube = build_metric_cube(
            assembly.units,
            publication_start=run.publication_start,
            publication_end=run.publication_end,
        )
        self.metric_repository.put(run_id, cube)
        self.stage_repository.progress(
            run_id,
            V4StageName.COMPUTE_METRICS,
            completed_count=len(assembly.units),
            total_count=len(assembly.units),
        )
        excluded = assembly.excluded_missing_date_analysis_unit_ids
        if excluded:
            self.stage_repository.add_limitation(
                run_id,
                V4StageName.COMPUTE_METRICS,
                code="MISSING_PUBLICATION_DATE",
                message="Analysis units without a verifiable publication date were excluded from time metrics.",
                affected_count=len(excluded),
            )
        self.stage_repository.succeed(
            run_id,
            V4StageName.COMPUTE_METRICS,
            with_limitations=bool(excluded),
        )
        return cube

    def _build_trends(self, run_id: str, cube, units):
        if self._succeeded(run_id, V4StageName.BUILD_TRENDS):
            return self.trend_repository.get(run_id)
        self.stage_repository.start(run_id, V4StageName.BUILD_TRENDS)
        candidates = build_trend_candidates(cube, units)
        self.trend_repository.put(run_id, candidates)
        self.stage_repository.progress(
            run_id,
            V4StageName.BUILD_TRENDS,
            completed_count=len(candidates),
            total_count=len(candidates),
        )
        self.stage_repository.succeed(run_id, V4StageName.BUILD_TRENDS)
        return candidates

    def _select_representatives(self, run_id: str, units):
        if self._succeeded(run_id, V4StageName.SELECT_REPRESENTATIVES):
            return self.representative_repository.get(run_id)
        direction_ids = tuple(
            sorted({unit.direction_id for unit in units if unit.direction_id != "UNRESOLVED"})
        )
        self.stage_repository.start(
            run_id,
            V4StageName.SELECT_REPRESENTATIVES,
            total_count=len(direction_ids),
        )
        representatives = tuple(
            representative
            for direction_id in direction_ids
            for representative in select_representative_patents(
                units,
                direction_id=direction_id,
            )
        )
        self.representative_repository.put(run_id, representatives)
        self.stage_repository.progress(
            run_id,
            V4StageName.SELECT_REPRESENTATIVES,
            completed_count=len(direction_ids),
            total_count=len(direction_ids),
        )
        self.stage_repository.succeed(run_id, V4StageName.SELECT_REPRESENTATIVES)
        return representatives

    def _build_report(self, run_id: str):
        if self.report_repository is None:
            raise V4WorkflowError("report repository is missing")
        if self._succeeded(run_id, V4StageName.BUILD_REPORT):
            return self.report_repository.get(run_id)
        self.stage_repository.start(
            run_id, V4StageName.BUILD_REPORT, total_count=1
        )
        run = self.run_repository.get(run_id)
        scope = self.scope_repository.get_confirmed(run.scope_revision_id)
        plan = self.search_coordinator.query_repository.get(run_id)
        resolution = self.family_repository.get(run_id)
        directions = tuple(
            self.direction_repository.get(run_id, unit.analysis_unit_id)
            for unit in resolution.analysis_units
        )
        classifications = tuple(
            self.classification_repository.get(run_id, unit.analysis_unit_id)
            for unit in resolution.analysis_units
        )
        report = build_report_v4(
            run=run,
            scope=scope,
            query_plan=plan,
            search_pages_by_query={
                query.query_id: self.search_coordinator.page_repository.list(
                    run_id, query.query_id
                )
                for query in plan.queries
            },
            scale_gate=self.search_coordinator.scale_repository.get(run_id),
            resolution=resolution,
            snapshots=self.snapshot_repository.get(run_id),
            organizations=self.organization_repository.get(run_id),
            classifications=classifications,
            directions=directions,
            others=self.others_repository.get(run_id),
            metric_cube=self.metric_repository.get(run_id),
            trends=self.trend_repository.get(run_id),
            representatives=self.representative_repository.get(run_id),
            limitations=self.stage_repository.limitations(run_id),
        )
        artifact = self.report_repository.put(report, render_report_markdown(report))
        self.stage_repository.progress(
            run_id,
            V4StageName.BUILD_REPORT,
            completed_count=1,
            total_count=1,
        )
        self.stage_repository.succeed(run_id, V4StageName.BUILD_REPORT)
        return artifact

    def _verify_run(self, run_id: str) -> None:
        if self._succeeded(run_id, V4StageName.VERIFY_RUN):
            return
        self.stage_repository.start(run_id, V4StageName.VERIFY_RUN, total_count=1)
        report, markdown = self.report_repository.get(run_id)
        if report.run_id != run_id or not markdown.strip():
            raise V4WorkflowError("Report 4.0 artifact failed final verification")
        if (
            report.counts.classified_count
            + report.counts.others_count
            + report.counts.unresolved_count
            != report.counts.analysis_unit_count
        ):
            raise V4WorkflowError("classification terminal counts do not reconcile")
        self.stage_repository.progress(
            run_id,
            V4StageName.VERIFY_RUN,
            completed_count=1,
            total_count=1,
        )
        self.stage_repository.succeed(run_id, V4StageName.VERIFY_RUN)
        limitations = self.stage_repository.limitations(run_id)
        target = (
            LandscapeRunStatus.COMPLETED_WITH_LIMITATIONS
            if limitations
            else LandscapeRunStatus.COMPLETED
        )
        self.run_repository.transition(
            run_id,
            target,
            expected=(LandscapeRunStatus.RUNNING,),
        )

    async def _extract_directions(self, run_id: str, taxonomy):
        resolution = self.family_repository.get(run_id)
        if self._succeeded(run_id, V4StageName.EXTRACT_DIRECTIONS):
            return tuple(
                self.direction_repository.get(run_id, unit.analysis_unit_id)
                for unit in resolution.analysis_units
            )
        total = resolution.analysis_unit_count
        self.stage_repository.start(
            run_id, V4StageName.EXTRACT_DIRECTIONS, total_count=total
        )
        packets = []
        terminal: dict[str, DirectionRecord] = {}
        for unit in resolution.analysis_units:
            abstracts = tuple(
                self.abstract_repository.get(run_id, publication_id)
                for publication_id in unit.member_publication_ids
            )
            available = tuple(
                item for item in abstracts if item.status == AbstractStatus.AVAILABLE
            )
            if not available:
                terminal[unit.analysis_unit_id] = DirectionRecord(
                    analysis_unit_id=unit.analysis_unit_id,
                    status=DirectionStatus.UNRESOLVED,
                    evidence_sufficient=False,
                    confidence=0,
                    unresolved_reason="NO_AVAILABLE_ABSTRACT_IN_ANALYSIS_UNIT",
                )
                continue
            packets.append(
                DirectionUnitPacket(
                    analysis_unit_id=unit.analysis_unit_id,
                    patent_abstracts=tuple(
                        f"标题：{item.title}\n摘要：{item.normalized_abstract}"
                        for item in available
                    ),
                    evidence_ids=tuple(
                        evidence_id
                        for item in available
                        for evidence_id in item.evidence_ids
                    ),
                )
            )
        if packets:
            extracted = await self.direction_extraction.extract(
                tuple(packets), taxonomy
            )
            terminal.update(
                (item.analysis_unit_id, item) for item in extracted
            )
        for completed, unit in enumerate(resolution.analysis_units, start=1):
            self.direction_repository.put(run_id, terminal[unit.analysis_unit_id])
            self.stage_repository.progress(
                run_id,
                V4StageName.EXTRACT_DIRECTIONS,
                completed_count=completed,
                total_count=total,
            )
        unresolved_count = sum(
            item.status == DirectionStatus.UNRESOLVED for item in terminal.values()
        )
        if unresolved_count:
            self.stage_repository.add_limitation(
                run_id,
                V4StageName.EXTRACT_DIRECTIONS,
                code="DIRECTION_UNRESOLVED",
                message="Some analysis units lack sufficient abstract evidence or model output.",
                affected_count=unresolved_count,
            )
        self.stage_repository.succeed(
            run_id,
            V4StageName.EXTRACT_DIRECTIONS,
            with_limitations=bool(unresolved_count),
        )
        return tuple(terminal[unit.analysis_unit_id] for unit in resolution.analysis_units)

    async def _match_taxonomy(self, run_id: str, directions, taxonomy):
        if self._succeeded(run_id, V4StageName.MATCH_TAXONOMY):
            return tuple(
                self.classification_repository.get(run_id, item.analysis_unit_id)
                for item in directions
            )
        total = len(directions)
        self.stage_repository.start(
            run_id, V4StageName.MATCH_TAXONOMY, total_count=total
        )
        results: tuple[ClassificationResult, ...] = (
            await self.classification_matching.classify(directions, taxonomy)
        )
        for completed, result in enumerate(results, start=1):
            self.classification_repository.put(run_id, result)
            self.stage_repository.progress(
                run_id,
                V4StageName.MATCH_TAXONOMY,
                completed_count=completed,
                total_count=total,
            )
        unresolved_count = sum(result.terminal.value == "UNRESOLVED" for result in results)
        if unresolved_count:
            self.stage_repository.add_limitation(
                run_id,
                V4StageName.MATCH_TAXONOMY,
                code="CLASSIFICATION_UNRESOLVED",
                message="Some analysis units could not be classified from abstract evidence.",
                affected_count=unresolved_count,
            )
        self.stage_repository.succeed(
            run_id,
            V4StageName.MATCH_TAXONOMY,
            with_limitations=bool(unresolved_count),
        )
        return results

    def _discover_others(self, run_id: str, directions, classifications):
        if self._succeeded(run_id, V4StageName.DISCOVER_OTHERS):
            return self.others_repository.get(run_id)
        others_count = sum(
            result.terminal.value == "OTHERS" for result in classifications
        )
        self.stage_repository.start(
            run_id, V4StageName.DISCOVER_OTHERS, total_count=others_count
        )
        discovery = discover_others_directions(classifications, directions)
        self.others_repository.put(run_id, discovery)
        self.stage_repository.progress(
            run_id,
            V4StageName.DISCOVER_OTHERS,
            completed_count=others_count,
            total_count=others_count,
        )
        self.stage_repository.succeed(run_id, V4StageName.DISCOVER_OTHERS)
        return discovery

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
