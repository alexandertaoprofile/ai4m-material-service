"""Shared customer-card composition formatting invariants."""

from src.alloy_workflow.presentation import _candidate_composition_table


def test_stainless_components_are_individual_rows_with_one_basis() -> None:
    table = _candidate_composition_table(
        {"Cr": 16.6, "Ni": 9.2, "C": 0.033, "N": 0.014, "Fe": 72.153},
        basis="wt.%", balance_element="Fe",
    )
    assert "| 元素 | 质量占比（wt.%） |" in table
    assert "| 铁（Fe） · 平衡组分 | 约 72.15% |" in table
    assert "| 铬（Cr） | 16.6% |" in table
    assert "| 氮（N） | 0.014% |" in table
    assert "| 磷（P） |" not in table
    assert "Cr16.6 Ni9.2" not in table


def test_trace_components_are_not_rounded_to_zero_or_invented() -> None:
    table = _candidate_composition_table(
        {"Ni": 30, "Co": 25, "B": 0.0001234, "Hf": 0, "Ti": float("nan")},
        basis="at.%",
    )
    assert "| 元素 | 原子占比（at.%） |" in table
    assert "| 硼（B） | 0.0001234% |" in table
    assert "铪（Hf）" not in table
    assert "钛（Ti）" not in table


def test_oxide_table_uses_its_own_molar_basis() -> None:
    table = _candidate_composition_table(
        {"SiO2": 68.25, "Al2O3": 15.75}, basis="mol.%", component_label="氧化物",
    )
    assert "| 氧化物 | 摩尔占比（mol.%） |" in table
    assert "| SiO2 | 68.25% |" in table
    assert "| Al2O3 | 15.75% |" in table
