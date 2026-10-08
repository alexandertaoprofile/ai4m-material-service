"""Audit and pilot a composition-level DFT E_hull screening proxy for HEAs.

This is deliberately an offline feasibility experiment.  E_hull is a
structure-level DFT label, so repeated structures at one exact composition are
aggregated to the *lowest observed* E_hull before fitting a composition-only
proxy.  Its output is therefore a dataset-scoped low-E_hull potential, never a
guarantee of stability for an arbitrary unrelaxed structure.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import average_precision_score, mean_absolute_error, mean_squared_error, r2_score, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.neighbors import NearestNeighbors


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data" / "processed" / "hea_mattergen_v1" / "structure_index.csv"
EXPERIMENTAL_HEA = ROOT / "data" / "processed" / "experimental_yield_strength.csv"
REPORT_DIR = ROOT / "reports" / "dft_stability"
TOKEN = re.compile(r"([A-Z][a-z]?)([0-9]*(?:\.[0-9]+)?)")
THRESHOLDS_EV_PER_ATOM = (0.05, 0.10, 0.20)


def parse_formula(formula: str) -> dict[str, float]:
    """Parse the dataset's flat reduced-formula syntax (e.g. Al2CoCrFeNi)."""
    tokens = TOKEN.findall(str(formula))
    if not tokens or "".join(element + amount for element, amount in tokens) != str(formula):
        raise ValueError(f"Unsupported reduced_formula: {formula!r}")
    amounts = {element: float(amount or 1.0) for element, amount in tokens}
    total = sum(amounts.values())
    return {element: amount / total for element, amount in amounts.items()}


def matrix_from_compositions(compositions: list[dict[str, float]], elements: list[str]) -> np.ndarray:
    return np.asarray([[composition.get(element, 0.0) for element in elements] for composition in compositions], dtype=float)


def regression_metrics(y: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    return {
        "MAE_eV_per_atom": float(mean_absolute_error(y, prediction)),
        "RMSE_eV_per_atom": float(mean_squared_error(y, prediction) ** 0.5),
        "R2": float(r2_score(y, prediction)),
    }


def main() -> None:
    raw = pd.read_csv(INDEX)
    raw = raw.dropna(subset=["reduced_formula", "chemical_system", "energy_above_hull"]).copy()
    parse_errors: list[str] = []
    parsed: list[dict[str, float] | None] = []
    for formula in raw["reduced_formula"]:
        try:
            parsed.append(parse_formula(formula))
        except ValueError:
            parsed.append(None)
            parse_errors.append(str(formula))
    raw["composition"] = parsed
    raw = raw[raw["composition"].notna()].copy()
    raw["composition_key"] = raw["composition"].map(
        lambda value: "|".join(f"{element}:{fraction:.10f}" for element, fraction in sorted(value.items()))
    )

    # One label per exact composition: minimum observed E_hull among structures.
    aggregate = (
        raw.groupby("composition_key", as_index=False)
        .agg(
            chemical_system=("chemical_system", "first"),
            reduced_formula=("reduced_formula", "first"),
            n_structures=("fid", "size"),
            min_energy_above_hull=("energy_above_hull", "min"),
            median_energy_above_hull=("energy_above_hull", "median"),
            composition=("composition", "first"),
        )
        .reset_index(drop=True)
    )
    elements = sorted({element for composition in aggregate["composition"] for element in composition})
    X = matrix_from_compositions(aggregate["composition"].tolist(), elements)
    y = aggregate["min_energy_above_hull"].to_numpy(dtype=float)
    X = np.column_stack([X, (X > 0).sum(axis=1)])

    # Reference quality: random composition split.  Strict quality: unseen exact
    # chemical systems.  The latter is expected to be much harder and is the
    # relevant warning for new element sets.
    indices = np.arange(len(aggregate))
    random_train, random_test = train_test_split(indices, test_size=0.20, random_state=2026)
    system_train, system_test = next(
        GroupShuffleSplit(n_splits=1, test_size=0.20, random_state=2026).split(X, y, groups=aggregate["chemical_system"])
    )

    def fit_and_evaluate(train_idx: np.ndarray, test_idx: np.ndarray) -> tuple[dict, np.ndarray]:
        model = ExtraTreesRegressor(
            n_estimators=300, min_samples_leaf=2, max_features=0.8, n_jobs=-1, random_state=2026
        )
        model.fit(X[train_idx], y[train_idx])
        prediction = model.predict(X[test_idx])
        result: dict[str, object] = regression_metrics(y[test_idx], prediction)
        threshold_metrics: dict[str, dict[str, float | None]] = {}
        for threshold in THRESHOLDS_EV_PER_ATOM:
            truth = y[test_idx] <= threshold
            if truth.min() == truth.max():
                threshold_metrics[f"{threshold:.2f}"] = {"ROC_AUC": None, "PR_AUC": None, "positive_count": int(truth.sum())}
            else:
                threshold_metrics[f"{threshold:.2f}"] = {
                    "ROC_AUC": float(roc_auc_score(truth, -prediction)),
                    "PR_AUC": float(average_precision_score(truth, -prediction)),
                    "positive_count": int(truth.sum()),
                }
        result["low_ehull_classification"] = threshold_metrics
        return result, prediction

    random_metrics, _ = fit_and_evaluate(random_train, random_test)
    system_metrics, _ = fit_and_evaluate(system_train, system_test)

    # The proxy used for a service pilot is trained on all DFT composition labels.
    service_model = ExtraTreesRegressor(
        n_estimators=300, min_samples_leaf=2, max_features=0.8, n_jobs=-1, random_state=2026
    )
    service_model.fit(X, y)
    dft_composition = X[:, :-1]
    neighbors = NearestNeighbors(n_neighbors=2).fit(dft_composition)
    dft_neighbor_distance = neighbors.kneighbors(dft_composition)[0][:, 1]
    inside_distance = float(np.quantile(dft_neighbor_distance, 0.95))

    experimental = pd.read_csv(EXPERIMENTAL_HEA)
    experimental = experimental.dropna(subset=["composition_at_pct"]).copy()
    experimental_composition = experimental["composition_at_pct"].map(json.loads)
    supported = experimental_composition.map(lambda value: set(value).issubset(elements))
    experimental = experimental[supported].copy()
    experimental_composition = experimental_composition[supported].map(
        lambda value: {element: float(amount) / 100.0 for element, amount in value.items()}
    )
    experimental_X_base = matrix_from_compositions(experimental_composition.tolist(), elements)
    experimental_X = np.column_stack([experimental_X_base, (experimental_X_base > 0).sum(axis=1)])
    distance = neighbors.kneighbors(experimental_X_base, n_neighbors=1)[0][:, 0]
    experimental["dft_proxy_distance"] = distance
    experimental["dft_proxy_in_domain"] = distance <= inside_distance
    experimental["predicted_min_energy_above_hull_eV_per_atom"] = service_model.predict(experimental_X)
    for threshold in THRESHOLDS_EV_PER_ATOM:
        experimental[f"passes_ehull_{threshold:.2f}_eV_per_atom"] = (
            experimental["dft_proxy_in_domain"]
            & (experimental["predicted_min_energy_above_hull_eV_per_atom"] <= threshold)
        )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    experimental[
        [
            "record_id", "canonical_formula", "yield_strength_MPa", "dft_proxy_distance", "dft_proxy_in_domain",
            "predicted_min_energy_above_hull_eV_per_atom",
            *[f"passes_ehull_{threshold:.2f}_eV_per_atom" for threshold in THRESHOLDS_EV_PER_ATOM],
        ]
    ].to_csv(REPORT_DIR / "experimental_hea_dft_proxy_screening_pilot.csv", index=False)

    report = {
        "experiment": "cantor_dft_composition_level_ehull_proxy_pilot_v1",
        "scope": "Offline feasibility only. Composition label is minimum observed structure-level E_hull in this DFT dataset.",
        "source": {"index": str(INDEX), "raw_records_after_parse": int(len(raw)), "exact_compositions": int(len(aggregate))},
        "formula_audit": {"parse_error_count": len(parse_errors), "parse_error_examples": parse_errors[:20]},
        "element_domain": elements,
        "label": "min_energy_above_hull_eV_per_atom",
        "model": "ExtraTreesRegressor on atomic fractions plus element count",
        "evaluation": {"random_composition_split": random_metrics, "unseen_chemical_system_split": system_metrics},
        "applicability": {"distance_feature": "Euclidean distance over atomic fractions", "inside_threshold_95pct": inside_distance},
        "experimental_hea_screening": {
            "input_records": int(len(pd.read_csv(EXPERIMENTAL_HEA))),
            "records_with_supported_elements": int(len(experimental)),
            "records_in_dft_proxy_domain": int(experimental["dft_proxy_in_domain"].sum()),
            "threshold_sensitivity": {
                f"{threshold:.2f}_eV_per_atom": int(experimental[f"passes_ehull_{threshold:.2f}_eV_per_atom"].sum())
                for threshold in THRESHOLDS_EV_PER_ATOM
            },
        },
        "caveat": "Do not expose this as an exact E_hull prediction, high-temperature stability, experimental proof, or a MatterGen output. It is a dataset-scoped DFT proxy and requires structure-level DFT confirmation.",
    }
    (REPORT_DIR / "dft_ehull_proxy_pilot_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
