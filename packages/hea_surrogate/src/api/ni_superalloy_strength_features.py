"""Shared, dependency-light feature schema for the hot-end strength M1 candidate.

All composition inputs are mass fractions (sum approximately one).  Constants
are stored here rather than queried from a runtime chemistry package so the
serving environment and offline training use exactly the same descriptors.
"""
from __future__ import annotations

import math
import re
from typing import Mapping


ELEMENTS = ["Ni", "Cr", "Co", "Re", "Ru", "Al", "Ta", "W", "Ti", "Mo", "Fe", "V", "Ir", "Mn", "Y", "C", "B", "Zr", "Si", "Nb", "Hf"]
ROUTES = ["cc", "ds", "sc", "am/sebm", "am/lc"]
VEC = {"Ni": 10, "Cr": 6, "Co": 9, "Re": 7, "Ru": 8, "Al": 3, "Ta": 5, "W": 6, "Ti": 4, "Mo": 6, "Fe": 8, "V": 5, "Ir": 9, "Mn": 7, "Y": 3, "C": 4, "B": 3, "Zr": 4, "Si": 4, "Nb": 5, "Hf": 4}
ATOMIC_NUMBER = {"Ni": 28, "Cr": 24, "Co": 27, "Re": 75, "Ru": 44, "Al": 13, "Ta": 73, "W": 74, "Ti": 22, "Mo": 42, "Fe": 26, "V": 23, "Ir": 77, "Mn": 25, "Y": 39, "C": 6, "B": 5, "Zr": 40, "Si": 14, "Nb": 41, "Hf": 72}
ATOMIC_MASS = {"Ni": 58.6934, "Cr": 51.9961, "Co": 58.933194, "Re": 186.207, "Ru": 101.07, "Al": 26.9815385, "Ta": 180.94788, "W": 183.84, "Ti": 47.867, "Mo": 95.95, "Fe": 55.845, "V": 50.9415, "Ir": 192.217, "Mn": 54.938044, "Y": 88.90584, "C": 12.011, "B": 10.81, "Zr": 91.224, "Si": 28.085, "Nb": 92.90637, "Hf": 178.49}
COVALENT_RADIUS = {"Ni": 1.24, "Cr": 1.39, "Co": 1.26, "Re": 1.51, "Ru": 1.46, "Al": 1.21, "Ta": 1.7, "W": 1.62, "Ti": 1.6, "Mo": 1.54, "Fe": 1.32, "V": 1.53, "Ir": 1.41, "Mn": 1.39, "Y": 1.9, "C": 0.76, "B": 0.84, "Zr": 1.75, "Si": 1.11, "Nb": 1.64, "Hf": 1.75}

BASE_FEATURE_NAMES = ["temperature_K", *[f"{item}_wt_percent" for item in ELEMENTS], *[f"route__{item}" for item in ROUTES], "max_solution_temp_C", "sum_solution_time_h", "max_precipitation_temp_C", "sum_precipitation_time_h"]
M1_DESCRIPTOR_NAMES = ["atomic_number_mean", "atomic_number_std", "atomic_mass_mean", "atomic_mass_std", "vec_mean", "vec_std", "covalent_radius_mean", "atomic_size_mismatch", "mixing_entropy_J_molK", "gamma_prime_former_fraction", "refractory_fraction", "chromium_fraction", "cobalt_fraction", "minor_strengthener_fraction"]


def _number_or_nan(value: str | float | None) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def heat_treatment_summary(value: str) -> dict[str, float]:
    """Match the compact four heat-treatment values of the serving M0 schema."""
    stages: list[float] = []
    for family, count in (("solution", 3), ("precipitation", 4)):
        for stage in range(1, count + 1):
            for suffix in ("temp_C", "time_h", "heating_C_per_h"):
                match = re.search(fr"{family}_stage_{stage}_{suffix}=([-+0-9.eE]+)", value or "")
                stages.append(_number_or_nan(match.group(1)) if match else float("nan"))
    solution = [(stages[index], stages[index + 1]) for index in range(0, 9, 3)]
    precipitation = [(stages[index], stages[index + 1]) for index in range(9, 21, 3)]
    finite = lambda values: [item for item in values if math.isfinite(item)]
    return {
        "max_solution_temp_C": max(finite([temp for temp, _ in solution]), default=float("nan")),
        "sum_solution_time_h": sum(finite([duration for _, duration in solution])),
        "max_precipitation_temp_C": max(finite([temp for temp, _ in precipitation]), default=float("nan")),
        "sum_precipitation_time_h": sum(finite([duration for _, duration in precipitation])),
    }


def base_feature_values(composition: Mapping[str, float], route: str, temperature_K: float, heat_treatment: str) -> dict[str, float]:
    return {
        "temperature_K": float(temperature_K),
        **{f"{element}_wt_percent": float(composition.get(element, 0.0)) for element in ELEMENTS},
        **{f"route__{item}": float(route == item) for item in ROUTES},
        **heat_treatment_summary(heat_treatment),
    }


def m1_descriptor_values(composition: Mapping[str, float]) -> dict[str, float]:
    """Deterministic composition descriptors; no fitted or thermodynamic data."""
    weights = {element: max(float(composition.get(element, 0.0)), 0.0) for element in ELEMENTS}
    total = sum(weights.values())
    if total <= 0:
        return {name: float("nan") for name in M1_DESCRIPTOR_NAMES}
    weights = {element: amount / total for element, amount in weights.items()}

    def moments(table: Mapping[str, float]) -> tuple[float, float]:
        mean = sum(weights[element] * table[element] for element in ELEMENTS)
        return mean, math.sqrt(sum(weights[element] * (table[element] - mean) ** 2 for element in ELEMENTS))

    z_mean, z_std = moments(ATOMIC_NUMBER)
    mass_mean, mass_std = moments(ATOMIC_MASS)
    vec_mean, vec_std = moments(VEC)
    radius_mean, _ = moments(COVALENT_RADIUS)
    size_mismatch = math.sqrt(sum(weights[element] * (1.0 - COVALENT_RADIUS[element] / radius_mean) ** 2 for element in ELEMENTS)) if radius_mean > 0 else float("nan")
    entropy = -8.314462618 * sum(amount * math.log(amount) for amount in weights.values() if amount > 0)
    return {
        "atomic_number_mean": z_mean,
        "atomic_number_std": z_std,
        "atomic_mass_mean": mass_mean,
        "atomic_mass_std": mass_std,
        "vec_mean": vec_mean,
        "vec_std": vec_std,
        "covalent_radius_mean": radius_mean,
        "atomic_size_mismatch": size_mismatch,
        "mixing_entropy_J_molK": entropy,
        "gamma_prime_former_fraction": sum(weights[item] for item in ("Al", "Ti", "Ta", "Nb")),
        "refractory_fraction": sum(weights[item] for item in ("Re", "W", "Mo")),
        "chromium_fraction": weights["Cr"],
        "cobalt_fraction": weights["Co"],
        "minor_strengthener_fraction": sum(weights[item] for item in ("C", "B", "Hf")),
    }


def feature_vector(composition: Mapping[str, float], route: str, temperature_K: float, heat_treatment: str, *, include_m1: bool) -> tuple[list[str], list[float]]:
    values = base_feature_values(composition, route, temperature_K, heat_treatment)
    names = list(BASE_FEATURE_NAMES)
    if include_m1:
        values.update(m1_descriptor_values(composition))
        names.extend(M1_DESCRIPTOR_NAMES)
    return names, [values[name] for name in names]
