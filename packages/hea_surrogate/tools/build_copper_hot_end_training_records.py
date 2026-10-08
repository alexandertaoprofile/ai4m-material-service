"""Build auditable copper-alloy tensile records from primary source tables.

Each emitted row retains its alloy/state/table/page locator and must remain
grouped by alloy-state in every validation split.  Table averages never become
invented specimen-level repetitions.
"""
from __future__ import annotations
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/processed/copper_hot_end_training_v0_20260910/nasa_tm_2007_tensile_records.csv"
SOURCE = "NASA/TM-2007-214663, pp. 12-13, Tables 1, 3, 4"
COMPOSITION = {
    "GRCop-84": {"Cu":87.50,"Cr":6.65,"Nb":5.85},
    "AMZIRC": {"Cu":99.85,"Zr":0.15},
    "GlidCop Al-15": {"Cu":99.68,"Al":0.15,"O":0.17},
    "Cu-1Cr-0.1Zr": {"Cu":98.90,"Cr":1.00,"Zr":0.10},
    "Cu-0.9Cr": {"Cu":99.10,"Cr":0.90},
    "GRCop-42": {"Cu":94.10,"Cr":3.20,"Nb":2.70},
    "NARloy-Z": {"Cu":96.50,"Ag":3.00,"Zr":0.50},
    "NASA-1-1": {"Cu":98.80,"Ag":1.10,"Zr":0.10},
    # NASA-CR-134628 reports 0.2 wt.% Al2O3.  The element columns use
    # stoichiometric mass partitioning (Al 52.93%, O 47.07%) and retain the
    # original oxide notation in the state/source locator.
    "GlidCop AL-10": {"Cu":99.80,"Al":0.1059,"O":0.0941},
    "sputtered Cu-0.14Zr": {"Cu":99.86,"Zr":0.14},
    "R-2 Zr-Cu": {"Cu":99.794,"Zr":0.20,"Ni":0.002,"Fe":0.004},
    "Cu-2Cr-1Nb SPS": {"Cu":97.19,"Cr":1.54,"Nb":1.26,"O":0.01},
    "Cu-Cr-Nb-Ti SLM": {"Cu":96.88,"Cr":1.65,"Nb":1.35,"Ti":0.12},
    "Cu-0.5Cr-0.08Zr": {"Cu":99.42,"Cr":0.50,"Zr":0.08},
    "Cu-0.82Cr-0.48Zr": {"Cu":98.70,"Cr":0.82,"Zr":0.48},
    "Cu-0.4Cr-0.3Zr": {"Cu":99.30,"Cr":0.40,"Zr":0.30},
    "ITER Cu-0.65Cr-0.08Zr": {"Cu":99.27,"Cr":0.65,"Zr":0.08},
    "Cu-0.95Cr-0.08Zr LAHAM": {"Cu":98.97,"Cr":0.95,"Zr":0.08},
    "Cu-26Ag": {"Cu":74.00,"Ag":26.00},
    "Cu-26Ag-0.1Fe": {"Cu":73.90,"Ag":26.00,"Fe":0.10},
}
# Values are transcribed only where Table 3/4 gives a numeric average.
TABLE = {
 "GRCop-84": {"as_received": [(25,368.0,196.2),(200,257.8,172.3),(400,203.1,139.5),(600,105.9,87.1),(800,36.2,27.1)], "braze_935C": [(25,361.0,187.0),(200,245.4,157.6),(400,196.2,125.9),(600,100.0,83.0),(800,34.1,28.2)]},
 "AMZIRC": {"as_received": [(25,510.9,501.9),(200,444.4,438.5),(400,345.2,342.9),(500,265.3,262.9),(600,45.3,39.8),(800,15.1,14.0)], "braze_935C": [(25,233.0,32.8),(200,180.7,26.9),(400,153.2,24.5),(500,105.9,25.2),(600,54.2,22.7),(800,16.9,10.1)]},
 # At 650 C the PDF text layer shifts the GlidCop columns and produces
 # impossible UTS < 0.2%-yield pairs.  Those cells are deliberately kept
 # out of the admitted table pending a page-image transcription review.
 "GlidCop Al-15": {"as_received": [(25,464.5,464.5),(200,338.3,338.3),(500,184.2,184.2),(800,70.4,70.4)], "braze_935C": [(25,408.1,408.1),(200,302.2,302.2),(500,174.5,174.5),(800,69.7,69.7)]},
 "Cu-1Cr-0.1Zr": {"as_received": [(25,564.4,549.9),(200,487.2,452.2),(500,293.9,283.2),(650,119.5,112.6),(800,69.7,19.9)], "braze_935C": [(25,266.3,105.9),(200,205.9,93.8),(500,89.4,66.4),(650,46.3,35.9),(800,21.2,19.2)]},
 "Cu-0.9Cr": {"as_received": [(25,495.2,441.8),(200,408.1,396.1),(500,206.5,206.4),(650,104.0,100.5),(800,25.3,21.7)], "braze_935C": [(25,245.1,89.7),(200,186.0,77.6),(500,62.5,42.7),(650,32.0,23.4),(800,17.4,14.6)]},
}

def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["alloy_name","alloy_state","test_temperature_C","test_strain_rate_per_s","uts_MPa","yield_0p2_MPa","Cu_wt_percent","Cr_wt_percent","Nb_wt_percent","Zr_wt_percent","Ag_wt_percent","Al_wt_percent","O_wt_percent","Ni_wt_percent","Fe_wt_percent","Ti_wt_percent","Hf_wt_percent","Co_wt_percent","V_wt_percent","Be_wt_percent","B_wt_percent","C_wt_percent","N_wt_percent","S_wt_percent","source_locator","label_type"]
    with OUT.open("w", newline="", encoding="utf8") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for alloy, states in TABLE.items():
            for state, rows in states.items():
                for temp, uts, ys in rows:
                    writer.writerow({"alloy_name":alloy,"alloy_state":state,"test_temperature_C":temp,"uts_MPa":uts,"yield_0p2_MPa":ys,"source_locator":SOURCE,"label_type":"source_table_average", **{f"{e}_wt_percent":v for e,v in COMPOSITION[alloy].items()}})
        # NASA/TM-2020-220725, p. 6, Tables 3-4: individual HIPed DED
        # GRCop-42 room-temperature UTS measurements.  Yield was not reported
        # and is intentionally blank instead of inferred from the curves.
        for orientation, values in {"DED_HIP_longitudinal":[289.48,304.41,282.91,302.38,305.95], "DED_HIP_lateral":[271.42,283.40,282.43,289.18,291.62]}.items():
            for uts in values:
                writer.writerow({"alloy_name":"GRCop-42","alloy_state":orientation,"test_temperature_C":25,"uts_MPa":uts,"yield_0p2_MPa":"","source_locator":"NASA/TM-2020-220725, p. 6, Tables 3-4","label_type":"individual_specimen", **{f"{e}_wt_percent":v for e,v in COMPOSITION["GRCop-42"].items()}})
        # NASA public report Table 2 gives lot averages for L-PBF material.  A
        # table average is one label, never expanded into fictitious specimens;
        # ksi values are converted with the exact 6.89476 MPa/ksi factor.
        for vendor, mesh, uts_ksi, ys_ksi in [
            ("vendor_1", "minus_325_without_fines", 38.4, 22.2),
            ("vendor_1", "minus_270_without_fines", 34.0, 19.9),
            ("vendor_1", "minus_325_with_fines", 52.1, 25.1),
            ("vendor_2", "minus_325_without_fines", 49.6, 27.0),
            ("vendor_2", "minus_325_with_fines", 60.7, 31.6),
            ("vendor_3", "minus_325_without_fines", 51.5, 26.4),
        ]:
            writer.writerow({"alloy_name":"GRCop-42","alloy_state":f"LPBF_{vendor}__{mesh}","test_temperature_C":21,"uts_MPa":uts_ksi * 6.89476,"yield_0p2_MPa":ys_ksi * 6.89476,"source_locator":"NASA GRCop-42 Development and Hot-fire Testing, 2019, p. 12, Table 2 (ksi converted to MPa by 6.89476)","label_type":"source_table_average", **{f"{e}_wt_percent":v for e,v in COMPOSITION["GRCop-42"].items()}})
        # NASA/TM-2012-217108, Tables 7--10 are independent material forms and
        # cold-work conditions.  Retain their reported orientations rather than
        # pooling them into fictitious replicate specimens.
        grcop84_2012 = [
            ("foil_90pct_cold_rolled_long_transverse", 25, 441.7, 324.1, "p. 58, Table 7"),
            ("foil_90pct_cold_rolled_longitudinal", 25, 402.3, 316.2, "p. 58, Table 7"),
            ("sheet_77pct_cold_worked", 25, 579.5, 500.4, "p. 59, Table 8"),
            ("foil_90pct_cold_rolled", 25, 409.1, 329.8, "p. 59, Table 8"),
            ("foil_90pct_cold_rolled_long_transverse", 500, 110.8, 96.3, "p. 60, Table 9"),
            ("foil_90pct_cold_rolled_longitudinal", 500, 103.0, 89.8, "p. 60, Table 9"),
            ("production_tube", 25, 417.0, 246.0, "p. 62, Table 10, sample 1"),
            ("production_tube", 25, 422.0, 243.0, "p. 62, Table 10, sample 2"),
            ("production_tube", 25, 421.0, 248.0, "p. 62, Table 10, sample 3"),
        ]
        for state, temp, uts, ys, locator in grcop84_2012:
            writer.writerow({"alloy_name":"GRCop-84","alloy_state":state,"test_temperature_C":temp,"uts_MPa":uts,"yield_0p2_MPa":ys,"source_locator":f"NASA/TM-2012-217108, {locator}","label_type":"source_table_average" if "sample" not in locator else "individual_specimen", **{f"{e}_wt_percent":v for e,v in COMPOSITION["GRCop-84"].items()}})
        # NASA-CR-134627 Table 1 gives individual NARloy-Z short-term tensile
        # tests.  Nominal 3Ag-0.5Zr chemistry is explicitly identified in
        # NASA/TM-2007-214663 Table 1; the condition in this report is
        # centrifugally cast, hot rolled, solution annealed and aged plate.
        narloy = [
            (25, 2e-3, 196.5, 316.5), (25, 2e-3, 200.0, 315.8),
            (482, 2e-3, 149.6, 182.0), (482, 2e-3, 148.0, 177.0),
            (538, 2e-3, 131.0, 153.0), (538, 2e-3, 129.0, 152.4),
            (593, 2e-3, 102.7, 114.5), (593, 2e-3, 110.3, 121.4),
            (538, 1e-2, 130.3, 163.4), (538, 1e-2, 132.4, 168.9),
            (538, 4e-4, 122.0, 134.4), (538, 4e-4, 124.0, 134.0),
        ]
        for temp, rate, ys, uts in narloy:
            writer.writerow({"alloy_name":"NARloy-Z","alloy_state":"centrifugal_cast_hot_rolled_solution_annealed_aged_plate","test_temperature_C":temp,"test_strain_rate_per_s":rate,"uts_MPa":uts,"yield_0p2_MPa":ys,"source_locator":"NASA-CR-134627, p. 18, Table 1; composition: NASA/TM-2007-214663, p. 12, Table 1","label_type":"individual_specimen", **{f"{e}_wt_percent":v for e,v in COMPOSITION["NARloy-Z"].items()}})
        # NASA-CR-134628 Tables 1--3.  Exclude R-22-1 because its UTS was not
        # obtained, and retain the report's 'approximate'/anisotropic flag as
        # review-only rather than silently mixing it into the primary labels.
        primary_1975 = [
            ("NASA-1-1A", "aged_455C_4h_air_cooled", "NASA-1-1", 538, 2e-3, 66.0, 110.9, "p. 10, Table 2, R-21-1"),
            ("NASA-1-1A", "aged_455C_4h_air_cooled", "NASA-1-1", 538, 2e-3, 68.8, 114.4, "p. 10, Table 2, R-21-2"),
            ("NASA-1-1B", "as_received_not_aged", "NASA-1-1", 538, 2e-3, 211.3, 224.0, "p. 10, Table 2, R-22-2"),
            ("NASA-1-1B", "as_received_not_aged", "NASA-1-1", 538, 2e-3, 209.2, 225.1, "p. 10, Table 2, R-22-3"),
            ("GlidCop AL-10", "cold_reduced_rod__nominal_0p2wtpct_Al2O3", "GlidCop AL-10", 538, 2e-3, 129.9, 147.4, "p. 10, Table 2, R-23-1"),
            ("GlidCop AL-10", "cold_reduced_rod__nominal_0p2wtpct_Al2O3", "GlidCop AL-10", 538, 2e-3, 129.2, 146.2, "p. 10, Table 2, R-23-2"),
            ("GlidCop AL-10", "cold_reduced_rod__nominal_0p2wtpct_Al2O3", "GlidCop AL-10", 538, 2e-3, 122.9, 141.1, "p. 10, Table 2, R-23-3"),
            ("NASA-1-1B", "as_received_not_aged", "NASA-1-1", 482, 2e-3, 244.0, 252.8, "p. 11, Table 3, R-22-10"),
            ("NASA-1-1B", "as_received_not_aged", "NASA-1-1", 538, 4e-4, 180.8, 187.8, "p. 11, Table 3, R-22-15"),
            ("NASA-1-1B", "as_received_not_aged", "NASA-1-1", 538, 4e-4, 180.8, 193.4, "p. 11, Table 3, R-22-16"),
            ("NASA-1-1B", "as_received_not_aged", "NASA-1-1", 538, 1e-2, 210.6, 223.6, "p. 11, Table 3, R-22-13"),
            ("NASA-1-1B", "as_received_not_aged", "NASA-1-1", 538, 1e-2, 212.4, 221.2, "p. 11, Table 3, R-22-14"),
        ]
        for alloy, state, composition, temp, rate, ys, uts, locator in primary_1975:
            writer.writerow({"alloy_name":alloy,"alloy_state":state,"test_temperature_C":temp,"test_strain_rate_per_s":rate,"uts_MPa":uts,"yield_0p2_MPa":ys,"source_locator":f"NASA-CR-134628, {locator}; composition: p. 7, Table 1","label_type":"individual_specimen", **{f"{e}_wt_percent":v for e,v in COMPOSITION[composition].items()}})
        for alloy, state, ys, uts, locator in [
            ("sputtered Cu-0.14Zr", "sputtered_annealed_593C_1h_air_cooled", 103.9, 112.8, "p. 10, Table 2, R-25-4"),
            ("sputtered Cu-0.14Zr", "sputtered_annealed_593C_1h_air_cooled", 101.1, 102.5, "p. 10, Table 2, R-25-5"),
            ("sputtered Cu-0.14Zr", "as_sputtered", 77.0, 84.7, "p. 10, Table 2, R-26-1"),
        ]:
            writer.writerow({"alloy_name":alloy,"alloy_state":state,"test_temperature_C":538,"test_strain_rate_per_s":2e-3,"uts_MPa":uts,"yield_0p2_MPa":ys,"source_locator":f"NASA-CR-134628, {locator} (approximate, anisotropic); composition: p. 7, Table 1","label_type":"review_only_approximate", **{f"{e}_wt_percent":v for e,v in COMPOSITION[alloy].items()}})
        # NASA-CR-121260, Table 1.  The R-2 chemistry is explicitly specified
        # on p. 7 (0.20Zr-0.002Ni-0.004Fe, Cu balance, wt.%) and each pair
        # below is a direct tensile result, not a graph digitisation.
        r2_zrcu = [
            (482, 2e-3, 222.0, 240.5), (482, 2e-3, 229.0, 242.5),
            (593, 2e-3, 156.9, 168.5), (593, 2e-3, 156.5, 165.0),
            (538, 1e-2, 211.0, 225.5), (538, 1e-2, 203.5, 225.0),
            (538, 4e-4, 182.5, 185.0), (538, 4e-4, 102.5, 191.0),
        ]
        for temp, rate, ys, uts in r2_zrcu:
            writer.writerow({"alloy_name":"R-2 Zr-Cu","alloy_state":"half_hard_50pct_reduction_plus_aged","test_temperature_C":temp,"test_strain_rate_per_s":rate,"uts_MPa":uts,"yield_0p2_MPa":ys,"source_locator":"NASA-CR-121260, p. 23, Table 1; composition: p. 7","label_type":"individual_specimen", **{f"{e}_wt_percent":v for e,v in COMPOSITION["R-2 Zr-Cu"].items()}})
        # Direct results from the authors' own measurements in Materials 2020,
        # 13, 2860.  The report calls this property tensile strength; it is
        # recorded in the UTS column because it is taken from tensile curves,
        # while `label_type` preserves that provenance distinction.
        cu2cr1nb = [
            ("SPS__vacuum_age_500C_1h_water_quenched", 25, 338.0, "p. 6, Sec. 3.3"),
            ("SPS__vacuum_age_500C_2h_water_quenched", 25, 332.0, "p. 6, Sec. 3.3; p. 8, Table 2"),
            ("SPS__vacuum_age_500C_5h_water_quenched", 25, 325.0, "p. 6, Sec. 3.3"),
            ("SPS__vacuum_age_500C_72h_water_quenched", 25, 319.0, "p. 6, Sec. 3.3"),
            ("SPS__vacuum_age_500C_2h_water_quenched", 700, 76.0, "p. 6, Sec. 3.3; p. 8, Table 2"),
        ]
        for state, temp, uts, locator in cu2cr1nb:
            writer.writerow({"alloy_name":"Cu-2Cr-1Nb SPS","alloy_state":state,"test_temperature_C":temp,"uts_MPa":uts,"yield_0p2_MPa":"","source_locator":f"Materials 2020, 13, 2860, {locator}; composition: p. 2, Table 1","label_type":"source_text_tensile_strength", **{f"{e}_wt_percent":v for e,v in COMPOSITION["Cu-2Cr-1Nb SPS"].items()}})
        # Materials 2023, 16, 2912 Table 4: as-built SLM results at room
        # temperature.  Process parameters remain in the state rather than
        # being lost as a generic 'as built' category.
        cucrnbti = [(325,500,396.0),(325,800,416.0),(325,1100,404.0),(300,800,412.0),(400,800,403.0)]
        for power, speed, uts in cucrnbti:
            writer.writerow({"alloy_name":"Cu-Cr-Nb-Ti SLM","alloy_state":f"as_built_SLM__{power}W_{speed}mm_per_s__30um_layer_0p1mm_hatch","test_temperature_C":25,"uts_MPa":uts,"yield_0p2_MPa":"","source_locator":"Materials 2023, 16, 2912, p. 16, Table 4; composition: p. 3, Table 1","label_type":"source_table_tensile_strength", **{f"{e}_wt_percent":v for e,v in COMPOSITION["Cu-Cr-Nb-Ti SLM"].items()}})
        # Materials 2023, 16, 105 Table 1.  The age schedule is explicit for
        # the quenched row (500 C, 2 h); for other rows the source table merely
        # says 'after aging', so only the fully specified quenched-aged row and
        # non-aged rows are admitted.
        cu05cr008zr = [
            ("quenched_1000C_2h_water_cooled", 72.0, 227.0, "p. 8, Table 1"),
            ("quenched_1000C_2h_water_cooled__aged_500C_2h", 348.0, 442.0, "p. 8, Table 1; p. 8, Sec. 3"),
            ("extrusion_800C__quench_1000C_2h_water_cooled__RS_strain_2p77", 426.0, 433.0, "p. 8, Table 1"),
            ("extrusion_800C__RS_strain_2p77", 592.0, 597.0, "p. 8, Table 1"),
        ]
        for state, ys, uts, locator in cu05cr008zr:
            writer.writerow({"alloy_name":"Cu-0.5Cr-0.08Zr","alloy_state":state,"test_temperature_C":25,"uts_MPa":uts,"yield_0p2_MPa":ys,"source_locator":f"Materials 2023, 16, 105, {locator}; composition: p. 3","label_type":"source_table_average", **{f"{e}_wt_percent":v for e,v in COMPOSITION["Cu-0.5Cr-0.08Zr"].items()}})
        # Materials 2024, 17, 624 Table 4.  The two LPBF processing routes are
        # intentionally separate states and include paired measured thermal
        # conductivity for later property-scope coverage, although this CSV is
        # currently the tensile-model admission table.
        for state, energy, ys, uts in [
            ("LPBF_non_remelting__energy_density_524J_per_mm3", 524, 214.0, 326.5),
            ("LPBF_remelting__energy_density_305J_per_mm3", 305, 222.0, 328.9),
        ]:
            writer.writerow({"alloy_name":"Cu-0.82Cr-0.48Zr","alloy_state":state,"test_temperature_C":25,"test_strain_rate_per_s":2.5e-4,"uts_MPa":uts,"yield_0p2_MPa":ys,"source_locator":f"Materials 2024, 17, 624, p. 13, Table 4; composition: p. 3; tensile conditions: p. 4","label_type":"source_table_average", **{f"{e}_wt_percent":v for e,v in COMPOSITION["Cu-0.82Cr-0.48Zr"].items()}})
        # Vacuum 167 (2019) 329--335 Table 1.  Each process definition and
        # tensile rate is stated in Sec. 2, allowing the full conditions to be
        # recorded rather than treating the four results as generic CuCrZr.
        cu04cr03zr = [
            ("hot_rolled_water_quenched", 212.0, 234.0),
            ("hot_rolled_water_quenched__cold_roll_50pct__age_450C_3h", 377.0, 415.0),
            ("hot_rolled_water_quenched__age_450C_3h__cold_roll_50pct", 485.0, 508.0),
            ("hot_rolled_water_quenched__age_450C_3h__cold_roll_80pct", 545.0, 568.0),
        ]
        for state, ys, uts in cu04cr03zr:
            writer.writerow({"alloy_name":"Cu-0.4Cr-0.3Zr","alloy_state":state,"test_temperature_C":25,"test_strain_rate_per_s":1e-3,"uts_MPa":uts,"yield_0p2_MPa":ys,"source_locator":"Vacuum 167 (2019) 329-335, p. 4, Table 1; composition/process: pp. 2-3, Sec. 2","label_type":"source_table_average", **{f"{e}_wt_percent":v for e,v in COMPOSITION["Cu-0.4Cr-0.3Zr"].items()}})
        # Author-archived manuscript, Table 1: ITER-grade Cu-0.65Cr-0.08Zr.
        # The chemistry, ECAP conditions and room-temperature true strain rate
        # are all explicit in the manuscript, so its two thermal conditions per
        # ECAP route are admissible rather than graph-digitised.
        iter_cucrzr = [
            ("extruded_as_received", 436.0, 524.0, "as_processed"),
            ("ECAP_400C_1pass_route_unspecified", 443.0, 508.0, "as_processed"),
            ("ECAP_400C_4pass_route_Bc", 485.0, 542.0, "as_processed"),
            ("ECAP_400C_4pass_route_C", 461.0, 522.0, "as_processed"),
            ("extruded_as_received__vacuum_annealed_450C_1h", 442.0, 533.0, "annealed"),
            ("ECAP_400C_1pass_route_unspecified__vacuum_annealed_450C_1h", 428.0, 507.0, "annealed"),
            ("ECAP_400C_4pass_route_Bc__vacuum_annealed_450C_1h", 470.0, 529.0, "annealed"),
            ("ECAP_400C_4pass_route_C__vacuum_annealed_450C_1h", 450.0, 522.0, "annealed"),
        ]
        for state, ys, uts, table_column in iter_cucrzr:
            writer.writerow({"alloy_name":"ITER Cu-0.65Cr-0.08Zr","alloy_state":state,"test_temperature_C":25,"test_strain_rate_per_s":1e-4,"uts_MPa":uts,"yield_0p2_MPa":ys,"source_locator":f"Author manuscript: Microstructure and mechanical properties of an ITER-grade Cu-Cr-Zr alloy processed by ECAP, p. 7, Table 1 ({table_column}); composition/test conditions: pp. 3-4","label_type":"source_table_average", **{f"{e}_wt_percent":v for e,v in COMPOSITION["ITER Cu-0.65Cr-0.08Zr"].items()}})
        # Coatings 2023, 13, 1228, Tables 1--2.  This source reports UTS but
        # not yield strength, which is left blank instead of reconstructed from
        # the stress--strain figure.
        for speed, heat_input, uts in [(250, 287, 311.3), (300, 239, 291.3), (350, 205, 276.7)]:
            writer.writerow({"alloy_name":"Cu-0.95Cr-0.08Zr LAHAM","alloy_state":f"LAHAM_preheat_200C__140A_150W__{speed}mm_per_min__{heat_input}J_per_mm","test_temperature_C":25,"uts_MPa":uts,"yield_0p2_MPa":"","source_locator":"Coatings 2023, 13, 1228, p. 10, Table 2; composition/test conditions: pp. 2-4, Tables 1-2","label_type":"source_table_average", **{f"{e}_wt_percent":v for e,v in COMPOSITION["Cu-0.95Cr-0.08Zr LAHAM"].items()}})
        # Materials 2017, 10, 1383, Table 2.  The article is CC BY 4.0 and
        # explicitly calls these the actual chemical compositions.  Tensile
        # testing was at room temperature and 1e-4/s; the heat-treatment
        # schedule is recorded in the state, rather than flattened away.
        cuag_states = [
            ("as_solid_solution__homogenized_760C_24h_water_quenched", 180.4, 113.6),
            ("as_solid_solution__aged_450C_2h", 260.3, 201.1),
            ("as_solid_solution__aged_550C_4h", 224.7, 187.3),
        ]
        for alloy in ("Cu-26Ag", "Cu-26Ag-0.1Fe"):
            for state, uts, ys in cuag_states:
                if alloy == "Cu-26Ag-0.1Fe":
                    values = {
                        "as_solid_solution__homogenized_760C_24h_water_quenched": (225.4, 126.1),
                        "as_solid_solution__aged_450C_2h": (315.8, 284.2),
                        "as_solid_solution__aged_550C_4h": (293.6, 252.2),
                    }
                    uts, ys = values[state]
                writer.writerow({"alloy_name":alloy,"alloy_state":state,"test_temperature_C":25,"test_strain_rate_per_s":1e-4,"uts_MPa":uts,"yield_0p2_MPa":ys,"source_locator":"Materials 2017, 10, 1383, p. 10, Table 2; composition: p. 2; tensile conditions: p. 2; CC BY 4.0","label_type":"source_table_average", **{f"{e}_wt_percent":v for e,v in COMPOSITION[alloy].items()}})
        # NASA-CR-185144 (1992), p. 88, Table 20: averaged direct tensile
        # measurements at 705 C.  The alloy formulations are stated in the
        # report's powder-metallurgy alloy descriptions (pp. 80--117).  Keep
        # each processing condition as one table-average observation; do not
        # expand the reported number of tests into fabricated specimens.
        cr185144_compositions = {
            "RSR-993 Cu-1.1Hf": {"Cu": 98.9, "Hf": 1.1},
            "RSR-995 Cu-0.6Zr": {"Cu": 99.4, "Zr": 0.6},
            "RSR-996 Cu-0.5Zr-3Ag": {"Cu": 96.5, "Zr": 0.5, "Ag": 3.0},
            "RSR-999 Cu-0.5Zr-3Ag": {"Cu": 96.5, "Zr": 0.5, "Ag": 3.0},
            "NASA-Z Cu-0.5Zr-3Ag": {"Cu": 96.5, "Zr": 0.5, "Ag": 3.0},
            "RSR-997 Cu-0.55Zr-0.05B": {"Cu": 99.4, "Zr": 0.55, "B": 0.05},
            "RSR-1032 Cu-0.7Ti-0.8Hf": {"Cu": 98.5, "Ti": 0.7, "Hf": 0.8},
            "RSR-1035 Cu-1Cr-0.6Zr": {"Cu": 98.4, "Cr": 1.0, "Zr": 0.6},
            "RSR-1036 Cu-0.6Zr-1.1Hf": {"Cu": 98.3, "Zr": 0.6, "Hf": 1.1},
            "RSR-1038 Cu-0.6Zr-3Ag-1Cr": {"Cu": 95.4, "Zr": 0.6, "Ag": 3.0, "Cr": 1.0},
            "RSR-1042 Cu-1.4Co-0.4V": {"Cu": 98.2, "Co": 1.4, "V": 0.4},
            "RSR-1043 Cu-1Cr-3Ag": {"Cu": 96.0, "Cr": 1.0, "Ag": 3.0},
        }
        cr185144_rows = [
            ("RSR-993 Cu-1.1Hf", "as_extruded", 0.06, 49.5, 62.5), ("RSR-993 Cu-1.1Hf", "solution_cold_work_aged_211", 0.06, 60.5, 73.0), ("RSR-993 Cu-1.1Hf", "solution_cold_work_aged_3", 0.10, 139.9, 139.9), ("RSR-993 Cu-1.1Hf", "hot_work_solution_aged", 0.10, 62.7, 74.4),
            ("RSR-995 Cu-0.6Zr", "as_extruded_2", 0.06, 77.2, 97.8), ("RSR-995 Cu-0.6Zr", "solution_cold_work_aged_211", 0.06, 43.4, 74.4), ("RSR-995 Cu-0.6Zr", "as_extruded_3", 0.10, 76.5, 90.3),
            ("RSR-996 Cu-0.5Zr-3Ag", "as_extruded", 0.06, 62.0, 92.3),
            ("RSR-999 Cu-0.5Zr-3Ag", "solution_aged", 0.06, 62.0, 96.5), ("RSR-999 Cu-0.5Zr-3Ag", "solution_cold_work_aged_211", 0.06, 65.5, 100.6), ("RSR-999 Cu-0.5Zr-3Ag", "hot_worked", 0.10, 112.3, 128.2), ("RSR-999 Cu-0.5Zr-3Ag", "hot_work_solution_aged", 0.10, 73.0, 114.4),
            # The adjacent 0.10/s NASA-Z cell scans as UTS 80.5 MPa despite a
            # listed 84.0 MPa yield strength.  It is held out pending page-image
            # review rather than silently correcting an internally inconsistent
            # source cell.
            ("NASA-Z Cu-0.5Zr-3Ag", "material_1", 0.06, 73.0, 107.5),
            ("RSR-997 Cu-0.55Zr-0.05B", "as_extruded", 0.06, 73.0, 91.6), ("RSR-997 Cu-0.55Zr-0.05B", "solution_cold_work_aged_111", 0.06, 48.2, 73.7),
            ("RSR-1032 Cu-0.7Ti-0.8Hf", "as_extruded", 0.10, 88.2, 115.8), ("RSR-1032 Cu-0.7Ti-0.8Hf", "solution_cold_work_aged_1", 0.10, 99.2, 149.5),
            ("RSR-1035 Cu-1Cr-0.6Zr", "as_extruded", 0.10, 91.6, 166.0), ("RSR-1035 Cu-1Cr-0.6Zr", "electron_beam_processed", 0.10, 96.5, 184.0),
            ("RSR-1036 Cu-0.6Zr-1.1Hf", "as_extruded", 0.10, 88.2, 125.4),
            ("RSR-1038 Cu-0.6Zr-3Ag-1Cr", "as_extruded", 0.10, 92.3, 134.4), ("RSR-1038 Cu-0.6Zr-3Ag-1Cr", "solution_aged", 0.10, 86.1, 116.4),
            ("RSR-1042 Cu-1.4Co-0.4V", "as_extruded", 0.10, 92.3, 114.4),
            ("RSR-1043 Cu-1Cr-3Ag", "as_extruded", 0.10, 72.3, 110.2), ("RSR-1043 Cu-1Cr-3Ag", "solution_cold_work_aged", 0.10, 72.3, 107.4),
        ]
        # Table 24 identifies these Table 20 condition averages as having been
        # run before the furnace thermal-gradient issue was discovered.  Keep
        # their source evidence but do not allow it into a release diagnostic.
        cr185144_gradient_review = {
            ("RSR-993 Cu-1.1Hf", "solution_cold_work_aged_3"),
            ("RSR-993 Cu-1.1Hf", "hot_work_solution_aged"),
            ("RSR-995 Cu-0.6Zr", "as_extruded_3"),
            ("RSR-1032 Cu-0.7Ti-0.8Hf", "as_extruded"),
            ("RSR-1032 Cu-0.7Ti-0.8Hf", "solution_cold_work_aged_1"),
            ("RSR-1035 Cu-1Cr-0.6Zr", "as_extruded"),
            ("RSR-1035 Cu-1Cr-0.6Zr", "electron_beam_processed"),
            ("RSR-1038 Cu-0.6Zr-3Ag-1Cr", "as_extruded"),
            ("RSR-1038 Cu-0.6Zr-3Ag-1Cr", "solution_aged"),
        }
        for alloy, state, rate, ys, uts in cr185144_rows:
            writer.writerow({"alloy_name":alloy, "alloy_state":state, "test_temperature_C":705, "test_strain_rate_per_s":rate, "uts_MPa":uts, "yield_0p2_MPa":ys, "source_locator":"NASA-CR-185144 (1992), p. 88, Table 20; nominal formulation: pp. 80-117; gradient audit: p. 134-136, Table 24", "label_type":"review_only_suspected_thermal_gradient" if (alloy, state) in cr185144_gradient_review else "source_table_average", **{f"{element}_wt_percent": value for element, value in cr185144_compositions[alloy].items()}})
        # The same report, p. 124, Table 21, adds a second directly tabulated
        # high-temperature level.  Its 705 C entries duplicate the Table 20
        # states after rounding, so only the non-duplicate 540 C rows are kept.
        for alloy, state, ys, uts in [
            ("NASA-Z Cu-0.5Zr-3Ag", "material_1", 125.0, 186.0),
            ("RSR-993 Cu-1.1Hf", "cold_work_conditioned_aged_recrystallized", 188.0, 228.0),
            ("RSR-995 Cu-0.6Zr", "as_extruded", 121.0, 157.0),
            ("RSR-1035 Cu-1Cr-0.6Zr", "as_extruded", 171.0, 219.0),
        ]:
            writer.writerow({"alloy_name":alloy, "alloy_state":state, "test_temperature_C":540, "test_strain_rate_per_s":0.10, "uts_MPa":uts, "yield_0p2_MPa":ys, "source_locator":"NASA-CR-185144 (1992), p. 124, Table 21; nominal formulation: pp. 80-117", "label_type":"source_table_average", **{f"{element}_wt_percent": value for element, value in cr185144_compositions[alloy].items()}})
        # NASA-CR-133890, Appendix IV, Table C-1: direct individual tension
        # tests of the documented Alloy 10 heat (0.61 Be, 2.70 Co, Cu balance,
        # wt.%).  The source gives 0.01 in/min crosshead rate and 2.50 in
        # effective tension gage length; the retained per-second value is its
        # dimensional conversion, not an inferred material property.
        beco = {"Cu": 96.69, "Be": 0.61, "Co": 2.70}
        for temp_f, specimen, ys_psi, uts_psi in [
            (75, "T-1", 87700, 112700), (75, "T-2", 85800, 117700),
            (250, "T-3", 87200, 111300), (500, "T-4", 80800, 93800),
            (500, "T-5", 76000, 90000), (1250, "T-8", 19430, 20130),
            (1250, "T-30", 20950, 21590), (1600, "T-31", 2700, 3300),
            (1600, "T-11", 3320, 3750),
        ]:
            writer.writerow({"alloy_name":"Alloy 10 Cu-0.61Be-2.70Co", "alloy_state":"forged_solution_927C_2h_water_quenched__aged_482C_3h", "test_temperature_C":(temp_f - 32) * 5 / 9, "test_strain_rate_per_s":0.01 / 2.5 / 60, "uts_MPa":uts_psi * 0.00689476, "yield_0p2_MPa":ys_psi * 0.00689476, "source_locator":f"NASA-CR-133890 (1973), Appendix IV, Table C-1, tension specimen {specimen}; heat chemistry/treatment: pp. 15-16; psi converted to MPa by 0.00689476", "label_type":"individual_specimen", **{f"{element}_wt_percent": value for element, value in beco.items()}})
        # Crystals 2025, 15, 547, Tables 1 and 4.  Unlike a nominal GRCop
        # label, this is the authors' ICP-OES powder composition.  Trace C/N/S
        # are retained as measured rather than being folded into Cu balance.
        grcop42_lded = {"Cu": 93.9599, "Cr": 3.24, "Nb": 2.76, "O": 0.035, "N": 0.001, "C": 0.0029, "S": 0.0012}
        for state, temp, ys, uts in [
            ("LDED_1500W", 25, 201.0, 213.0),
            ("LDED_2000W", 25, 236.0, 319.0),
            ("LDED_2500W", 25, 164.0, 247.0),
            ("LDED_2000W", 600, 89.0, 98.0),
        ]:
            writer.writerow({"alloy_name":"LDED GRCop-42 ICP-OES heat", "alloy_state":state, "test_temperature_C":temp, "test_strain_rate_per_s":1e-3, "uts_MPa":uts, "yield_0p2_MPa":ys, "source_locator":"Crystals 2025, 15, 547, p. 4, Table 1 and p. 6, Table 4; CC BY 4.0", "label_type":"source_table_average", **{f"{element}_wt_percent": value for element, value in grcop42_lded.items()}})
    print(OUT)

if __name__ == "__main__": main()
