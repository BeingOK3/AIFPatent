from __future__ import annotations

from pydantic import model_validator

from .classification_terminal import ClassificationResult, ClassificationTerminal
from .direction_record import DirectionRecord
from .family_resolution import FamilyResolution
from .metrics import MetricAnalysisUnit, MetricPublication
from .organization_assignment import OrganizationAssignmentSet
from .others_discovery import OthersDiscovery
from .patent_snapshot import PatentSnapshotSet
from .scope import ScopeModel
from .taxonomy import TaxonomyArtifact


class AnalyticsAssembly(ScopeModel):
    units: tuple[MetricAnalysisUnit, ...]
    excluded_missing_date_analysis_unit_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_partition(self) -> "AnalyticsAssembly":
        included = {item.analysis_unit_id for item in self.units}
        excluded = set(self.excluded_missing_date_analysis_unit_ids)
        if included & excluded:
            raise ValueError("analytics unit cannot be both included and excluded")
        if len(included) != len(self.units) or len(excluded) != len(
            self.excluded_missing_date_analysis_unit_ids
        ):
            raise ValueError("analytics assembly contains duplicate units")
        return self


def assemble_metric_units(
    resolution: FamilyResolution,
    snapshots: PatentSnapshotSet,
    organizations: OrganizationAssignmentSet,
    classifications: tuple[ClassificationResult, ...],
    directions: tuple[DirectionRecord, ...],
    others: OthersDiscovery,
    taxonomy: TaxonomyArtifact,
) -> AnalyticsAssembly:
    snapshot_by_id = {item.publication_id: item for item in snapshots.snapshots}
    assignment_by_id = {
        item.publication_id: item for item in organizations.assignments
    }
    classification_by_id = {
        item.analysis_unit_id: item for item in classifications
    }
    direction_by_id = {item.analysis_unit_id: item for item in directions}
    expected_units = {item.analysis_unit_id for item in resolution.analysis_units}
    if set(classification_by_id) != expected_units or set(direction_by_id) != expected_units:
        raise ValueError("semantic results must exactly cover analysis units")
    expected_publications = {
        publication_id
        for unit in resolution.analysis_units
        for publication_id in unit.member_publication_ids
    }
    if set(snapshot_by_id) != expected_publications or set(assignment_by_id) != expected_publications:
        raise ValueError("snapshot and organization results must cover publications")
    taxonomy_by_id = {node.category_id: node for node in taxonomy.nodes}
    others_cluster_by_member = {
        member_id: cluster
        for cluster in others.clusters
        for member_id in cluster.member_ids
    }
    units = []
    excluded = []
    for unit in resolution.analysis_units:
        classification = classification_by_id[unit.analysis_unit_id]
        direction = direction_by_id[unit.analysis_unit_id]
        publications = []
        for publication_id in unit.member_publication_ids:
            snapshot = snapshot_by_id[publication_id]
            if snapshot.publication_date is None:
                continue
            assignment = assignment_by_id[publication_id]
            publications.append(
                MetricPublication(
                    publication_id=publication_id,
                    publication_number=snapshot.publication_number or publication_id,
                    title=snapshot.title,
                    publication_date=snapshot.publication_date,
                    primary_organization_id=assignment.primary_organization_id,
                    co_organization_ids=assignment.co_organization_ids,
                )
            )
        if not publications:
            excluded.append(unit.analysis_unit_id)
            continue
        direction_id, path, centrality = _direction_projection(
            classification,
            others_cluster_by_member,
            taxonomy_by_id,
        )
        dated_count = len(publications)
        total_count = len(unit.member_publication_ids)
        units.append(
            MetricAnalysisUnit(
                analysis_unit_id=unit.analysis_unit_id,
                direction_id=direction_id,
                classification_path=path,
                publications=tuple(publications),
                classification_confidence=classification.confidence,
                evidence_completeness=round(dated_count / total_count, 8),
                direction_centrality=centrality,
                evidence_ids=direction.evidence_ids,
            )
        )
    return AnalyticsAssembly(
        units=tuple(sorted(units, key=lambda item: item.analysis_unit_id)),
        excluded_missing_date_analysis_unit_ids=tuple(sorted(excluded)),
    )


def _direction_projection(classification, others_by_member, taxonomy_by_id):
    if classification.terminal == ClassificationTerminal.CLASSIFIED:
        node = taxonomy_by_id[classification.primary_category_id]
        return node.category_id, node.path, 1.0
    if classification.terminal == ClassificationTerminal.OTHERS:
        cluster = others_by_member[classification.analysis_unit_id]
        return cluster.cluster_id, ("Others", cluster.name), cluster.cohesion
    return "UNRESOLVED", (), 0.0


__all__ = ["AnalyticsAssembly", "assemble_metric_units"]
