from __future__ import annotations

from datetime import date, timedelta
from typing import Final


SCALE_CASE_SIZES: Final[dict[str, int]] = {
    "LAND-050": 50,
    "LAND-500": 500,
    "LAND-1000": 1000,
    "LAND-2500-SHARDED": 2500,
}

INPUT_MODES: Final[tuple[str, ...]] = (
    "COMPANY_ONLY",
    "TECHNOLOGY_ONLY",
    "COMPANY_AND_TECHNOLOGY",
)

_ORGANIZATIONS: Final[tuple[tuple[str, str], ...]] = (
    ("华为技术有限公司", "Huawei Technologies Co., Ltd."),
    ("中兴通讯股份有限公司", "ZTE Corporation"),
    ("三星电子株式会社", "Samsung Electronics Co., Ltd."),
    ("清华大学", "Tsinghua University"),
    ("北京大学", "Peking University"),
    ("腾讯科技（深圳）有限公司", "Tencent Technology (Shenzhen) Co., Ltd."),
    ("小米通讯技术有限公司", "Xiaomi Communications Co., Ltd."),
)

_DIRECTIONS: Final[tuple[tuple[str, str, str], ...]] = (
    ("无线信道估计", "wireless channel estimation", "导频降噪与信道状态重建"),
    ("端侧模型推理", "on-device model inference", "模型量化与异构算子调度"),
    ("电池热管理", "battery thermal management", "热状态预测与冷却回路控制"),
    ("图像语义分割", "image semantic segmentation", "多尺度特征融合与边界恢复"),
    ("分布式存储", "distributed storage", "副本一致性与故障恢复"),
    ("光通信调制", "optical communication modulation", "相干检测与非线性补偿"),
    ("机器人路径规划", "robot path planning", "动态障碍预测与轨迹优化"),
    ("隐私计算", "privacy-preserving computation", "秘密共享与安全聚合"),
)


def make_scale_case(
    case_name: str,
    *,
    mode: str = "COMPANY_AND_TECHNOLOGY",
) -> dict:
    """Build a deterministic, JSON-serializable publication fixture.

    Scale fixtures intentionally contain one publication per future analysis
    unit. Patent-family edge cases live in a separate fixture so scale tests do
    not silently change their cardinality when family rules evolve.
    """

    if case_name not in SCALE_CASE_SIZES:
        raise ValueError(f"unknown Landscape v4 scale case: {case_name}")
    if mode not in INPUT_MODES:
        raise ValueError(f"unsupported Landscape v4 input mode: {mode}")

    size = SCALE_CASE_SIZES[case_name]
    start = date(2023, 1, 1)
    publications = [_publication(index, start=start) for index in range(size)]
    return {
        "schema_version": "landscape-synthetic-fixture/1.0.0",
        "case_name": case_name,
        "mode": mode,
        "scope": _scope(mode),
        "expected": {
            "publication_count": size,
            "analysis_unit_count": size,
            "shard_count": (size + 999) // 1000,
            "duplicate_publication_count": 0,
        },
        "publications": publications,
    }


def make_family_case() -> dict:
    publications = [
        _family_publication("PUB-APP-A", "CN118000001A", "CN202210000001", ["CN202110000001.0"]),
        _family_publication("PUB-APP-B", "CN118000001B", "CN202210000001", ["CN202110000001.0"]),
        _family_publication("PUB-SIMPLE-CN", "CN118000002A", "CN202210000002", ["PCT/CN2021/000002"]),
        _family_publication("PUB-SIMPLE-WO", "WO2023000002A1", "PCT/CN2022/000002", ["PCT/CN2021/000002"]),
        _family_publication("PUB-EXTENDED", "US2024000003A1", "US18000003", ["PCT/CN2021/000002", "US17000003"]),
        _family_publication(
            "PUB-DIVISIONAL",
            "CN118000004A",
            "CN202310000004",
            ["CN202110000004.0"],
            relationship="DIVISIONAL",
            related_application="CN202210000004",
        ),
        _family_publication("PUB-PARENT", "CN118000005A", "CN202210000004", ["CN202110000004.0"]),
        _family_publication("PUB-MISSING", "EP4000006A1", "EP23000006", []),
    ]
    return {
        "schema_version": "landscape-family-fixture/1.0.0",
        "case_name": "LAND-FAMILY",
        "publications": publications,
        "expected_analysis_units": {
            "AU-SAME-APPLICATION": ["PUB-APP-A", "PUB-APP-B"],
            "AU-SIMPLE-FAMILY": ["PUB-SIMPLE-CN", "PUB-SIMPLE-WO"],
            "AU-EXTENDED-SEPARATE": ["PUB-EXTENDED"],
            "AU-DIVISIONAL-SEPARATE": ["PUB-DIVISIONAL"],
            "AU-PARENT-SEPARATE": ["PUB-PARENT"],
            "AU-MISSING-SEPARATE": ["PUB-MISSING"],
        },
    }


def make_classification_terminal_case() -> dict:
    records = [
        {"analysis_unit_id": f"AU-{index:03d}", "expected_terminal": terminal}
        for index, terminal in enumerate(
            (
                "CLASSIFIED",
                "CLASSIFIED",
                "CLASSIFIED",
                "CLASSIFIED",
                "OTHERS",
                "OTHERS",
                "OTHERS",
                "UNRESOLVED",
                "UNRESOLVED",
            ),
            start=1,
        )
    ]
    records[-2]["unresolved_reason"] = "ABSTRACT_MISSING"
    records[-1]["unresolved_reason"] = "ABSTRACT_INSUFFICIENT"
    return {
        "schema_version": "landscape-terminal-fixture/1.0.0",
        "case_name": "LAND-OTHERS",
        "analysis_units": records,
        "expected_counts": {"CLASSIFIED": 4, "OTHERS": 3, "UNRESOLVED": 2},
    }


def _scope(mode: str) -> dict:
    companies = [
        {"display_name": cn, "confirmed_names": [cn, en]}
        for cn, en in _ORGANIZATIONS[:3]
    ]
    technology = {
        "input": "人工智能与通信技术",
        "confirmed_terms": [
            "人工智能",
            "无线通信",
            "artificial intelligence",
            "wireless communication",
        ],
    }
    return {
        "publication_start": "2023-01-01",
        "publication_end": "2025-12-31",
        "companies": companies if mode != "TECHNOLOGY_ONLY" else [],
        "technology": technology if mode != "COMPANY_ONLY" else None,
    }


def _publication(index: int, *, start: date) -> dict:
    sequence = index + 1
    organization_cn, organization_en = _ORGANIZATIONS[index % len(_ORGANIZATIONS)]
    direction_cn, direction_en, mechanism = _DIRECTIONS[index % len(_DIRECTIONS)]
    publication_date = start + timedelta(days=(index * 17) % 1095)
    application_date = publication_date - timedelta(days=240 + index % 180)
    publication_number = f"CN{120000000 + sequence}A"
    application_number = f"CN2023{sequence:08d}"
    return {
        "publication_id": f"PUB-{sequence:06d}",
        "publication_number": publication_number,
        "application_number": application_number,
        "title": f"一种{direction_cn}的方法及装置 {sequence}",
        "title_en": f"Method and apparatus for {direction_en} {sequence}",
        "abstract": (
            f"本公开涉及{direction_cn}。系统读取目标对象的状态数据，"
            f"通过{mechanism}生成控制结果，并依据反馈调整执行参数。"
            "该方案限定了输入、处理机制和输出对象，可用于对应技术场景。"
        ),
        "abstract_language": "zh",
        "assignees": [organization_cn, organization_en],
        "application_date": application_date.isoformat(),
        "publication_date": publication_date.isoformat(),
        "priority_numbers": [f"CN{application_number[2:]}.0"],
        "ipc": [f"G06F{index % 20 + 1:02d}/00"],
        "cpc": [f"G06F{index % 20 + 1:02d}/10"],
        "family_hint": None,
        "source_url": f"https://patents.google.com/patent/{publication_number}",
    }


def _family_publication(
    publication_id: str,
    publication_number: str,
    application_number: str,
    priority_numbers: list[str],
    *,
    relationship: str | None = None,
    related_application: str | None = None,
) -> dict:
    return {
        "publication_id": publication_id,
        "publication_number": publication_number,
        "application_number": application_number,
        "priority_numbers": priority_numbers,
        "relationship": relationship,
        "related_application": related_application,
        "title": "一种无线信号处理方法",
        "abstract": "系统根据导频信号估计信道状态，并使用估计结果恢复目标数据。",
        "publication_date": "2024-01-01",
        "source_url": f"https://patents.google.com/patent/{publication_number}",
    }


__all__ = [
    "INPUT_MODES",
    "SCALE_CASE_SIZES",
    "make_classification_terminal_case",
    "make_family_case",
    "make_scale_case",
]
