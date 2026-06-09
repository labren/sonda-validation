"""
DQC Comparison Script
=====================
Runs the validator on CPA1811ED.csv and compares output against CPA1811ED_DQC.csv.
Climatic normals are sourced from INPESONDA_normais_climatology.csv (station-specific,
±20% margins derived from sonda_climatology.csv) instead of the uniform sea-level defaults.

Usage (from project root):
    python data/DQC/compare_dqc.py

ED format (16 columns, semicolon-separated, no header, N/S = missing):
  id | year | day | min | glo_avg | dir_avg | diff_avg | lw_avg | par_avg | lux_avg
  | tp_sfc | humid | press | rain | ws_10m | wd_10m

  Note: old header had a 'datetm' column between 'day' and 'min' that was dropped.

Column name mapping (ED → validator):
  diff_avg → dif_avg    (SolarimetricValidator expects 'dif_avg')
  ws_10m   → ws10_avg   (MeteoValidator expects 'ws10_avg')
  wd_10m   → wd10_avg   (MeteoValidator expects 'wd10_avg')
  tp_sfc, humid, press  kept as-is (MeteoValidator checks for these exact names)
"""

import os
import sys
import duckdb
import pandas as pd
from datetime import datetime, timedelta

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPT_DIR, "..", "..")
sys.path.insert(0, PROJECT_DIR)

from core.sondaValidator import SolarimetricValidator, MeteoValidator

INPUT_CSV    = os.path.join(SCRIPT_DIR, "CPA1811ED.csv")
REF_CSV      = os.path.join(SCRIPT_DIR, "CPA1811ED_DQC.csv")
STATIONS_CSV = os.path.join(PROJECT_DIR, "data", "metadata", "INPESONDA_stations.csv")
NORMAIS_CSV  = os.path.join(PROJECT_DIR, "data", "metadata", "INPESONDA_normais_climatology.csv")

# ── column definitions ────────────────────────────────────────────────────────
# Raw ED file columns (datetm was removed from the old header)
ED_COLS = [
    "id", "year", "day", "min",
    "glo_avg", "dir_avg", "diff_avg", "lw_avg", "par_avg", "lux_avg",
    "tp_sfc", "humid", "press", "rain", "ws_10m", "wd_10m",
]

# DQC reference file columns (same layout, values are 3-digit DQC strings)
DQC_REF_COLS = [
    "id", "year", "day", "min",
    "glo_avg_dqc", "dir_avg_dqc", "dif_avg_dqc", "lw_avg_dqc",
    "par_avg_dqc", "lux_avg_dqc", "temp_avg_dqc", "rh_avg_dqc",
    "press_avg_dqc", "ws_avg_dqc", "wd_avg_dqc", "rain_dqc",
]

# Rename ED columns to match what each validator expects
ED_RENAME = {
    "diff_avg": "dif_avg",    # SolarimetricValidator
    "ws_10m":   "ws10_avg",   # MeteoValidator
    "wd_10m":   "wd10_avg",   # MeteoValidator
    # tp_sfc, humid, press, rain kept as-is
}

# Variables to compare (must exist in both our output and the reference)
COMPARE_VARS = [
    "glo_avg_dqc",
    "dir_avg_dqc",
    "dif_avg_dqc",
    "lw_avg_dqc",
    "temp_avg_dqc",
    "rh_avg_dqc",
    "press_avg_dqc",
]


def load_csv(path, col_names):
    return pd.read_csv(
        path, sep=";", header=None, names=col_names,
        na_values=["N/S"], dtype=str,   # keep DQC strings as-is, not floats
    )


def build_timestamp(row):
    base = datetime(int(row["year"]), 1, 1) + timedelta(days=int(row["day"]) - 1)
    return base + timedelta(minutes=int(row["min"]))


def prepare_input(df_raw):
    df = df_raw.rename(columns=ED_RENAME).copy()

    # Convert numeric columns from string (dtype=str was used for safe DQC reading)
    numeric = ["year", "day", "min", "glo_avg", "dir_avg", "dif_avg", "lw_avg",
               "par_avg", "lux_avg", "tp_sfc", "humid", "press", "rain",
               "ws10_avg", "wd10_avg"]
    for col in numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["acronym"]   = "CPA"
    df["timestamp"] = df.apply(build_timestamp, axis=1)

    # Load station coordinates from metadata
    stations = pd.read_csv(STATIONS_CSV)
    cpa_station = stations[stations["station"].str.upper() == "CPA"].iloc[0]
    df["latitude"]  = float(cpa_station["latitude"])
    df["longitude"] = float(cpa_station["longitude"])

    # Load climate normals from metadata
    normais = pd.read_csv(NORMAIS_CSV, sep=";")
    cpa_normais = normais[normais["acronym"].str.upper() == "CPA"].iloc[0]
    df["tp_min"]    = float(cpa_normais["tp_min"])
    df["tp_max"]    = float(cpa_normais["tp_max"])
    df["press_min"] = float(cpa_normais["press_min"])
    df["press_max"] = float(cpa_normais["press_max"])
    df["rain_max"]  = float(cpa_normais["rain_max"])

    # ED format is 1-minute raw data — no std columns.
    # Set dummy non-null, non-zero values so the validator evaluates the physical
    # range checks (Alg1) rather than short-circuiting to 5 (insufficient data).
    # The std=0 failure check is not comparable against a reference produced with
    # real std data.
    for col in ["glo_std", "dir_std", "dif_std", "lw_std", "par_std", "lux_std",
                "temp_std", "ws_std", "wd_std"]:
        df[col] = 1.0

    return df


def run_validation(df_input):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE solar_with_meta AS SELECT * FROM df_input")

    sv = SolarimetricValidator(con, "solar_with_meta", "solar_out", freq_min=1)

    print("  Computing solar geometry (mu0, azs)...")
    sv.add_mu0_to_duckdb(con=con, table_name="solar_with_meta")

    print("  Computing Sa and Sum...")
    sv.add_sa_sum(con, table_name="solar_with_meta")

    print("  Running solar validation...")
    sv.run_solar_validation()

    print("  Running meteo validation...")
    mv = MeteoValidator(con, "solar_with_meta", "meteo_out", freq_min=1)
    mv.run_all()

    tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
    df_solar = con.execute("SELECT * FROM solar_out").df() if "solar_out" in tables else pd.DataFrame()
    df_meteo = con.execute("SELECT * FROM meteo_out").df() if "meteo_out" in tables else pd.DataFrame()

    if not df_solar.empty and not df_meteo.empty:
        # MeteoValidator recomputes year/day/min via EXTRACT (day-of-month, minute-of-hour),
        # so they differ from the solar output. Merge on timestamp instead.
        meteo_dqc = [c for c in df_meteo.columns if c.endswith("_dqc")]
        df_out = pd.merge(
            df_solar,
            df_meteo[["acronym", "timestamp"] + meteo_dqc],
            on=["acronym", "timestamp"],
            how="outer",
        )
    elif not df_solar.empty:
        df_out = df_solar
    else:
        df_out = df_meteo

    con.close()
    return df_out


def compare(df_our, df_ref):
    # df_our has year/day/min from solar output (year=2018, day=julian, min=minute-of-day)
    # df_ref has year/day/min from the ED file (same meaning)
    key = ["year", "day", "min"]

    # Convert key columns to int for matching
    for col in key:
        df_our = df_our.copy()
        df_our[col] = df_our[col].astype(int)
        df_ref = df_ref.copy()
        df_ref[col] = pd.to_numeric(df_ref[col], errors="coerce").astype("Int64")

    our_dqc_cols = [c for c in df_our.columns if c.endswith("_dqc")]
    ref_dqc_cols = [c for c in df_ref.columns  if c.endswith("_dqc")]

    merged = pd.merge(
        df_our[key + our_dqc_cols],
        df_ref[key + ref_dqc_cols],
        on=key,
        suffixes=("_our", "_ref"),
    )

    print(f"\n{'─'*82}")
    print(f"  {'Variable':<20} {'Match%':>7}  {'Our values':<30} Ref values")
    print(f"{'─'*82}")

    for col in COMPARE_VARS:
        our_col = col + "_our"
        ref_col = col + "_ref"
        if our_col not in merged.columns or ref_col not in merged.columns:
            print(f"  {col:<20}  {'N/A':>6}   (column not found in output)")
            continue

        our = merged[our_col].astype(str)
        ref = merged[ref_col].astype(str)
        mask = ref.notna() & (ref != "nan") & (ref != "<NA>")
        n = mask.sum()
        pct = (our[mask] == ref[mask]).sum() / n * 100 if n > 0 else float("nan")

        our_vals = sorted(our[mask].unique())[:5]
        ref_vals = sorted(ref[mask].unique())[:5]
        print(f"  {col:<20} {pct:>6.1f}%  {str(our_vals):<30} {ref_vals}")

    print(f"{'─'*82}\n")


if __name__ == "__main__":
    print("Loading input data...")
    df_raw   = load_csv(INPUT_CSV, ED_COLS)
    df_ref   = load_csv(REF_CSV,   DQC_REF_COLS)
    df_input = prepare_input(df_raw)
    print(f"  {len(df_input):,} rows")

    print("Running validator...")
    df_our = run_validation(df_input)
    print(f"  Output: {len(df_our):,} rows, {len(df_our.columns)} columns")

    print("Comparing against reference...")
    compare(df_our, df_ref)