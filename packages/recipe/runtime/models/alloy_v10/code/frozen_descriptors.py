"""Element statistics and physical proxies; verified against frozen training rows."""
import math
import numpy as np

def descriptor_values(
    composition: dict[str, float], properties: dict[str, dict[str, float]]
) -> dict[str, float]:
    property_names = [
        "Number",
        "MendeleevNumber",
        "AtomicWeight",
        "MeltingT",
        "CovalentRadius",
        "Electronegativity",
        "NValence",
        "GSvolume_pa",
        "GSmagmom",
    ]
    elements = list(composition)
    weights = np.asarray([composition[element] for element in elements], dtype=float)
    weights /= weights.sum()
    result: dict[str, float] = {}
    for name in property_names:
        values = np.asarray([properties[element].get(name, float("nan")) for element in elements])
        valid = np.isfinite(values)
        if not valid.any():
            mean = std = span = 0.0
        else:
            valid_weights = weights[valid]
            valid_weights /= valid_weights.sum()
            valid_values = values[valid]
            mean = float(np.sum(valid_weights * valid_values))
            std = float(np.sqrt(np.sum(valid_weights * (valid_values - mean) ** 2)))
            span = float(np.max(valid_values) - np.min(valid_values))
        result[f"desc_{name}_mean"] = mean
        result[f"desc_{name}_std"] = std
        result[f"desc_{name}_span"] = span
    result["desc_component_count"] = float(len(composition))
    result["desc_mixing_entropy_R"] = float(
        -sum(fraction * math.log(fraction) for fraction in composition.values())
    )
    result["desc_max_atomic_fraction"] = float(max(composition.values()))

    ratios = {
        "desc_phys_atomic_size_delta_pct": ("CovalentRadius", 100.0),
        "desc_phys_electronegativity_cv": ("Electronegativity", 1.0),
        "desc_phys_melting_temperature_cv": ("MeltingT", 1.0),
        "desc_phys_atomic_weight_cv": ("AtomicWeight", 1.0),
        "desc_phys_valence_electron_cv": ("NValence", 1.0),
        "desc_phys_atomic_volume_cv": ("GSvolume_pa", 1.0),
    }
    for output_name, (source_name, scale) in ratios.items():
        mean = result[f"desc_{source_name}_mean"]
        std = result[f"desc_{source_name}_std"]
        result[output_name] = scale * std / max(abs(mean), 1e-8)
    result["desc_phys_entropy_to_component_count"] = (
        result["desc_mixing_entropy_R"] / max(result["desc_component_count"], 1.0)
    )
    result["desc_phys_dilute_fraction"] = 1.0 - result["desc_max_atomic_fraction"]
    return result
