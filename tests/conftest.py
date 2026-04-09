import pytest
import duckdb
import pandas as pd
from datetime import datetime, timedelta
from core.sondaValidator import SolarimetricValidator, MeteoValidator

BASE_TIME = datetime(2024, 6, 15, 12, 0, 0)

# ---------------------------------------------------------------------------
# Pre-computed limits  (mu0=0.80, Sa=1361.0)
#   mu0^1.2 ≈ 0.7652
#   mu0^0.2 ≈ 0.9564
# ---------------------------------------------------------------------------
MU0     = 0.80
SA      = 1361.0
MU0_12  = MU0 ** 1.2   # ≈ 0.7652
MU0_02  = MU0 ** 0.2   # ≈ 0.9564

GLO_ALG2_UPPER = SA * 1.2  * MU0_12 + 50   # ≈ 1299.8  (extremely rare)
GLO_ALG1_UPPER = SA * 1.5  * MU0_12 + 100  # ≈ 1662.3  (physically possible)

DIR_ALG2_UPPER = SA * 0.95 * MU0_02 + 10   # ≈ 1245.4
DIR_ALG1_UPPER = SA                          # = 1361.0

DIF_ALG2_UPPER = SA * 0.75 * MU0_12 + 30   # ≈ 810.5
DIF_ALG1_UPPER = SA * 0.95 * MU0_12 + 50   # ≈ 1038.8

PAR_ALG2_UPPER = 2.07 * (SA * 1.2  * MU0_12 + 50)   # ≈ 2690.6
PAR_ALG1_UPPER = 2.07 * (SA * 1.5  * MU0_12 + 100)  # ≈ 3441.0

LUX_ALG2_UPPER = 0.1125 * (SA * 0.95 * MU0_12 + 50)  # ≈ 116.9
LUX_ALG1_UPPER = 0.1125 * (SA * 1.5  * MU0_12 + 100) # ≈ 187.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def con():
    conn = duckdb.connect(":memory:")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Row factories — safe defaults produce rows that pass every algorithm
# ---------------------------------------------------------------------------

def solar_row(**kwargs):
    """One solar_with_meta row. Defaults pass all solar algorithms.

    Sum is auto-computed as dif_avg + dir_avg * mu0 unless overridden.
    Override both glo_avg AND Sum when testing glo consistency checks.
    """
    row = {
        "acronym": "TST",
        "timestamp": BASE_TIME,
        "year": 2024, "day": 167, "min": 720,
        # solar geometry (pre-computed — skips add_mu0_to_duckdb)
        "mu0": MU0, "azs": 45.0, "Sa": SA,
        # safe measurement defaults
        "glo_avg": 600.0,  "glo_std": 30.0,
        "dir_avg": 500.0,  "dir_std": 20.0,
        "dif_avg": 200.0,  "dif_std": 10.0,
        "lw_avg":  350.0,  "lw_std":   5.0,
        "par_avg": 1000.0, "par_std":  50.0,
        "lux_avg":  80.0,  "lux_std":   4.0,
        # temperature (used by lw Alg3 S-B consistency check)
        "tp_sfc": 25.0,
        # climatic metadata
        "latitude": -15.0, "longitude": -47.0,
        "tp_min": 10.0, "tp_max": 40.0,
        "press_min": 900.0, "press_max": 1020.0,
        "rain_max": 150.0,
    }
    row.update(kwargs)
    if "Sum" not in kwargs:
        if row["dif_avg"] is not None and row["dir_avg"] is not None:
            row["Sum"] = row["dif_avg"] + row["dir_avg"] * row["mu0"]
        else:
            row["Sum"] = None
    return row


def meteo_row(**kwargs):
    """One solar_with_meta row for meteo tests. Defaults pass all algorithms."""
    row = {
        "acronym": "TST",
        "timestamp": BASE_TIME,
        "year": 2024, "day": 167, "min": 720,
        "tp_sfc":   25.0,
        "humid":    60.0,
        "press":   950.0,
        "ws10_avg":  5.0,
        "wd10_avg": 180.0,
        "rain":      0.5,
        "latitude": -15.0, "longitude": -47.0,
        "tp_min": 10.0,  "tp_max": 40.0,
        "press_min": 900.0, "press_max": 1020.0,
        "rain_max": 150.0,
    }
    row.update(kwargs)
    return row


# ---------------------------------------------------------------------------
# Time-series helper
# ---------------------------------------------------------------------------

def make_timeseries(base_row, n, interval_minutes=10, overrides=None):
    """Return n rows with sequential timestamps based on base_row.

    overrides: dict {row_index: {col: val, ...}} applied after defaults.
    Row 0 is the earliest timestamp.
    """
    rows = []
    t0 = base_row["timestamp"]
    for i in range(n):
        r = dict(base_row)
        r["timestamp"] = t0 + timedelta(minutes=i * interval_minutes)
        r["min"] = r["timestamp"].hour * 60 + r["timestamp"].minute
        if overrides and i in overrides:
            r.update(overrides[i])
        rows.append(r)
    return rows


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------

def _load(con, rows, table="solar_with_meta"):
    df = pd.DataFrame(rows if isinstance(rows, list) else [rows])
    con.register("_tmp", df)
    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _tmp")


def run_solar(con, rows):
    _load(con, rows)
    v = SolarimetricValidator(con, "solar_with_meta", "solar_out")
    v.run_solar_validation()
    tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
    if "solar_out" not in tables:
        return pd.DataFrame()
    return con.execute("SELECT * FROM solar_out").df()


def run_meteo(con, rows):
    _load(con, rows)
    v = MeteoValidator(con, "solar_with_meta", "meteo_out")
    v.run_all()
    tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
    if "meteo_out" not in tables:
        return pd.DataFrame()
    return con.execute("SELECT * FROM meteo_out").df()


def dqc(df, col, row=0):
    """Return DQC value as a string, normalising int/str output from DuckDB."""
    return str(df[col].iloc[row])
