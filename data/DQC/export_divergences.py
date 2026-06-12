"""
Exports rows where our DQC output diverges from the reference into
data/DQC/divergences_CPA1811.csv for use in test development.
"""
import os, sys
import duckdb
import pandas as pd
from datetime import datetime, timedelta

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPT_DIR, "..", "..")
sys.path.insert(0, PROJECT_DIR)

from core.sondaValidator import SolarimetricValidator, MeteoValidator

INPUT_CSV    = os.path.join(SCRIPT_DIR, "CPA1812ED.csv")
REF_CSV      = os.path.join(SCRIPT_DIR, "CPA1812ED_DQC.csv")
STATIONS_CSV = os.path.join(SCRIPT_DIR, "metadata", "INPESONDA_stations.csv")
NORMAIS_CSV  = os.path.join(PROJECT_DIR, "data", "metadata", "INPESONDA_normais_climatology.csv")
OUT_CSV      = os.path.join(SCRIPT_DIR, "divergences_CPA1812.csv")

ED_COLS = ["id", "year", "day", "min",
           "glo_avg", "dir_avg", "diff_avg", "lw_avg", "par_avg", "lux_avg",
           "tp_sfc", "humid", "press", "rain", "ws_10m", "wd_10m"]

DQC_REF_COLS = ["id", "year", "day", "min",
                "glo_avg_dqc", "dir_avg_dqc", "dif_avg_dqc", "lw_avg_dqc",
                "par_avg_dqc", "lux_avg_dqc", "temp_avg_dqc", "rh_avg_dqc",
                "press_avg_dqc", "ws_avg_dqc", "wd_avg_dqc", "rain_dqc"]

COMPARE_VARS = ["glo_avg_dqc", "dir_avg_dqc", "dif_avg_dqc", "lw_avg_dqc",
                "temp_avg_dqc", "rh_avg_dqc", "press_avg_dqc"]

ED_RENAME = {"diff_avg": "dif_avg", "ws_10m": "ws10_avg", "wd_10m": "wd10_avg"}


def load_csv(path, col_names):
    return pd.read_csv(path, sep=";", header=None, names=col_names,
                       na_values=["N/S"], dtype=str)


def build_timestamp(row):
    base = datetime(int(row["year"]), 1, 1) + timedelta(days=int(row["day"]) - 1)
    return base + timedelta(minutes=int(row["min"]))


def prepare_input(df_raw):
    df = df_raw.rename(columns=ED_RENAME).copy()
    numeric = ["year", "day", "min", "glo_avg", "dir_avg", "dif_avg", "lw_avg",
               "par_avg", "lux_avg", "tp_sfc", "humid", "press", "rain",
               "ws10_avg", "wd10_avg"]
    for col in numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["acronym"]   = "CPA"
    df["timestamp"] = df.apply(build_timestamp, axis=1)
    stations = pd.read_csv(STATIONS_CSV)
    cpa = stations[stations["station"].str.upper() == "CPA"].iloc[0]
    df["latitude"]  = float(cpa["latitude"])
    df["longitude"] = float(cpa["longitude"])
    normais = pd.read_csv(NORMAIS_CSV, sep=";")
    cpa_n = normais[normais["acronym"].str.upper() == "CPA"].iloc[0]
    for col in ["tp_min", "tp_max", "press_min", "press_max", "rain_max"]:
        df[col] = float(cpa_n[col]) if pd.notna(cpa_n[col]) else None
    for col in ["glo_std", "dir_std", "dif_std", "lw_std", "par_std", "lux_std",
                "temp_std", "ws_std", "wd_std"]:
        df[col] = 1.0
    return df


def run_validation(df_input):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE solar_with_meta AS SELECT * FROM df_input")
    sv = SolarimetricValidator(con, "solar_with_meta", "solar_out", freq_min=1)
    sv.add_mu0_to_duckdb(con=con, table_name="solar_with_meta")
    sv.add_sa_sum(con, table_name="solar_with_meta")
    sv.run_solar_validation()
    mv = MeteoValidator(con, "solar_with_meta", "meteo_out", freq_min=1)
    mv.run_all()
    tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
    df_solar = con.execute("SELECT * FROM solar_out").df() if "solar_out" in tables else pd.DataFrame()
    df_meteo = con.execute("SELECT * FROM meteo_out").df() if "meteo_out" in tables else pd.DataFrame()
    if not df_solar.empty and not df_meteo.empty:
        meteo_dqc = [c for c in df_meteo.columns if c.endswith("_dqc")]
        df_out = pd.merge(df_solar,
                          df_meteo[["acronym", "timestamp"] + meteo_dqc],
                          on=["acronym", "timestamp"], how="outer")
    elif not df_solar.empty:
        df_out = df_solar
    else:
        df_out = df_meteo
    con.close()
    return df_out


if __name__ == "__main__":
    df_raw   = load_csv(INPUT_CSV, ED_COLS)
    df_ref   = load_csv(REF_CSV,   DQC_REF_COLS)
    df_input = prepare_input(df_raw)
    df_our   = run_validation(df_input)

    key = ["year", "day", "min"]
    for col in key:
        df_our[col] = df_our[col].astype(int)
        df_ref[col] = pd.to_numeric(df_ref[col], errors="coerce").astype("Int64")

    our_dqc = [c for c in df_our.columns if c.endswith("_dqc")]
    ref_dqc = [c for c in df_ref.columns  if c.endswith("_dqc")]

    merged = pd.merge(
        df_our[key + our_dqc + [c for c in ["glo_avg", "dir_avg", "dif_avg", "lw_avg",
                                              "temp_avg", "press_avg"] if c in df_our.columns]],
        df_ref[key + ref_dqc],
        on=key, suffixes=("_our", "_ref"),
    )

    # Flag rows with any divergence in the compared variables
    divergence_mask = pd.Series(False, index=merged.index)
    for col in COMPARE_VARS:
        our_col = col + "_our"
        ref_col = col + "_ref"
        if our_col in merged.columns and ref_col in merged.columns:
            our = merged[our_col].astype(str)
            ref = merged[ref_col].astype(str)
            ref_valid = ref.notna() & (ref != "nan") & (ref != "<NA>")
            divergence_mask |= (ref_valid & (our != ref))

    divs = merged[divergence_mask].copy()

    # Add a human-readable timestamp
    divs["timestamp"] = divs.apply(
        lambda r: (datetime(int(r["year"]), 1, 1)
                   + timedelta(days=int(r["day"]) - 1)
                   + timedelta(minutes=int(r["min"]))).isoformat(),
        axis=1
    )

    divs.to_csv(OUT_CSV, index=False, sep=";")
    print(f"Saved {len(divs):,} divergent rows → {OUT_CSV}")

    # Summary per variable
    print(f"\n{'─'*55}")
    print(f"  {'Variable':<20} {'Divergences':>12}  {'Our → Ref (top pairs)':}")
    print(f"{'─'*55}")
    for col in COMPARE_VARS:
        our_col = col + "_our"
        ref_col = col + "_ref"
        if our_col not in merged.columns:
            continue
        our = merged[our_col].astype(str)
        ref = merged[ref_col].astype(str)
        ref_valid = ref.notna() & (ref != "nan") & (ref != "<NA>")
        mask = ref_valid & (our != ref)
        n = mask.sum()
        if n == 0:
            continue
        top = (merged.loc[mask, our_col] + "→" + merged.loc[mask, ref_col]
               ).value_counts().head(3).to_dict()
        print(f"  {col:<20} {n:>12,}  {top}")
    print(f"{'─'*55}")
