"""
Tests for MeteoValidator.

Describes the NEW desired behaviour:
  - DQC digit order is REVERSED: position 1 = Alg3, pos 2 = Alg2, pos 3 = Alg1
  - Only flag=2 cascades forward: if pos N = 2, pos N+1 is forced to 2
  - flag=5 (missing/NULL LAG) does NOT cascade
  - A placeholder digit '0' is appended to every DQC code

DQC width by variable:
  temp_avg, ws_avg, wd_avg, rain  →  4 digits  (3 algs + placeholder)
  press_avg                        →  3 digits  (2 algs + placeholder)
  rh_avg                           →  2 digits  (1 alg  + placeholder)

LAG / window notes (10-minute data):
  LAG(6)   = 1 h back      LAG(18)  = 3 h back
  LAG(72)  = 12 h back     LAG(108) = 18 h back
  SUM(5 preceding)  = last 6 rows  ≈ 1 h
  SUM(143 preceding)= last 144 rows ≈ 24 h
"""
import pytest
from tests.conftest import meteo_row, run_meteo, make_timeseries, dqc


# ===========================================================================
# temp_avg  (tp_sfc → temp_avg)  — 3 algorithms → 4-digit DQC
#
# pos 1 (Alg3): 12-hour persistence  |tp[t] − tp[t−72]| ≤ 0.5°C  → 2
# pos 2 (Alg2): 1-hour jump          |tp[t] − tp[t−6]|  ≥ 5°C    → 2
# pos 3 (Alg1): climatic normals     tp < tp_min  OR  tp > tp_max → 2
# ===========================================================================

class TestTempAvg:

    def test_all_pass(self, con):
        # 73 rows with a clear 12-hour trend (no persistence) and no big jumps
        # Row 0: tp=20, Row 72: tp=25 → Δ12h=5 > 0.5 → pos1=9
        # Δ1h at last row ≈ 5/72*6 ≈ 0.4 < 5 → pos2=9
        rows = make_timeseries(
            meteo_row(tp_sfc=20.0), n=73,
            overrides={i: {"tp_sfc": 20.0 + i * (5.0 / 72)} for i in range(73)}
        )
        df = run_meteo(con, rows)
        assert dqc(df, "temp_avg_dqc", row=-1) == "999"

    def test_null_input(self, con):
        # Single row with NULL tp_sfc
        # All LAG-based algorithms return 5 (NULL diff), normals check also 5
        row = meteo_row(tp_sfc=None)
        df = run_meteo(con, [row])
        assert dqc(df, "temp_avg_dqc") == "555"

    def test_pos1_fails_persistence(self, con):
        # 73 rows at constant tp=25.0 → |Δ72|=0 ≤ 0.5 → Alg3=2 → cascade
        rows = make_timeseries(meteo_row(tp_sfc=25.0), n=73)
        df = run_meteo(con, rows)
        assert dqc(df, "temp_avg_dqc", row=-1) == "222"

    def test_pos2_fails_jump(self, con):
        # Rows 0–71: tp=20.0  |  Row 72: tp=26.0
        # Δ12h = 26−20 = 6 > 0.5 → pos1=9
        # Δ1h  = 26−20 = 6 ≥ 5   → pos2=2 → cascade pos3=2
        rows = make_timeseries(
            meteo_row(tp_sfc=20.0), n=73,
            overrides={72: {"tp_sfc": 26.0}}
        )
        df = run_meteo(con, rows)
        assert dqc(df, "temp_avg_dqc", row=-1) == "922"

    def test_pos3_fails_normals(self, con):
        # Single row: tp=45 > tp_max=40 → Alg1=2
        # No LAG data → Alg3=5, Alg2=5 (independently, no cascade from 5)
        row = meteo_row(tp_sfc=45.0)
        df = run_meteo(con, [row])
        assert dqc(df, "temp_avg_dqc") == "552"

    def test_no_cascade_from_5(self, con):
        # Single row: LAG values are NULL → pos1=5, pos2=5
        # tp_sfc in normals range → pos3=9 independently
        # flag=5 must NOT cascade to override pos3
        row = meteo_row(tp_sfc=25.0)
        df = run_meteo(con, [row])
        assert dqc(df, "temp_avg_dqc") == "559"


# ===========================================================================
# rh_avg  (humid → rh_avg)  — 1 algorithm → 2-digit DQC
#
# pos 1 (Alg1): physical range  0 ≤ humid ≤ 100  → 9, else 5
# pos 2:        placeholder     always '0'
# ===========================================================================

class TestRhAvg:

    def test_pass(self, con):
        row = meteo_row(humid=60.0)
        df = run_meteo(con, [row])
        assert dqc(df, "rh_avg_dqc") == "009"

    def test_out_of_range_high(self, con):
        row = meteo_row(humid=110.0)
        df = run_meteo(con, [row])
        assert dqc(df, "rh_avg_dqc") == "005"

    def test_out_of_range_low(self, con):
        row = meteo_row(humid=-5.0)
        df = run_meteo(con, [row])
        assert dqc(df, "rh_avg_dqc") == "005"

    def test_placeholder_always_zero(self, con):
        for humid in [60.0, 110.0, -5.0]:
            df = run_meteo(con, [meteo_row(humid=humid)])
            assert str(df["rh_avg_dqc"].iloc[0])[0] == "0"


# ===========================================================================
# press_avg  (press → press_avg)  — 2 algorithms → 3-digit DQC
#
# pos 1 (Alg2): 3-hour variation  |press[t] − press[t−18]| < 6 hPa → 2
# pos 2 (Alg1): climatic normals  press < press_min OR > press_max  → 2
# pos 3:        placeholder        always '0'
# ===========================================================================

class TestPressAvg:

    def test_all_pass(self, con):
        # 19 rows with clear 3h variation (10 hPa shift at row 18)
        rows = make_timeseries(
            meteo_row(press=950.0), n=19,
            overrides={18: {"press": 960.0}}
        )
        df = run_meteo(con, rows)
        assert dqc(df, "press_avg_dqc", row=-1) == "099"

    def test_null_input(self, con):
        row = meteo_row(press=None)
        df = run_meteo(con, [row])
        assert dqc(df, "press_avg_dqc") == "055"

    def test_pos1_fails_no_variation(self, con):
        # 19 rows at constant press=950 → |Δ18|=0 < 6 → Alg2=2 → cascade pos2=2
        rows = make_timeseries(meteo_row(press=950.0), n=19)
        df = run_meteo(con, rows)
        assert dqc(df, "press_avg_dqc", row=-1) == "022"

    def test_pos2_fails_normals(self, con):
        # Single row: press=800 < press_min=900 → Alg1=2
        # LAG=NULL → Alg2=5 (no cascade from 5)
        row = meteo_row(press=800.0)
        df = run_meteo(con, [row])
        assert dqc(df, "press_avg_dqc") == "052"


# ===========================================================================
# ws_avg  (ws10_avg → ws_avg)  — 3 algorithms → 4-digit DQC
#
# pos 1 (Alg3): 12-hour persistence  |ws[t] − ws[t−72]| ≤ 0.5  → 2
# pos 2 (Alg2): 3-hour persistence   |ws[t] − ws[t−18]| ≤ 0.1  → 2
# pos 3 (Alg1): physical range       ws < 0  OR  ws > 25        → 2
# ===========================================================================

class TestWsAvg:

    def test_all_pass(self, con):
        # Row 0 different, rows 1–72 constant → LAG(72) differs by >0.5
        # 3-hour change also present
        rows = make_timeseries(
            meteo_row(ws10_avg=5.0), n=73,
            overrides={0: {"ws10_avg": 2.0}}
        )
        df = run_meteo(con, rows)
        # Δ12h = |5.0 − 2.0| = 3.0 > 0.5 → pos1=9
        # Δ3h  = |5.0 − 5.0| = 0.0 ≤ 0.1 → pos2=2 ...
        # → actually need rows 1–72 to also have variation within 3h window
        # Use a cleaner setup: 73-row gradient
        rows = make_timeseries(
            meteo_row(ws10_avg=3.0), n=73,
            overrides={i: {"ws10_avg": 3.0 + i * (4.0 / 72)} for i in range(73)}
        )
        df = run_meteo(con, rows)
        assert dqc(df, "ws_avg_dqc", row=-1) == "999"

    def test_null_input(self, con):
        row = meteo_row(ws10_avg=None)
        df = run_meteo(con, [row])
        assert dqc(df, "ws_avg_dqc") == "555"

    def test_pos1_fails_persistence_12h(self, con):
        # 73 rows at constant ws=5.0 → Δ12h=0 ≤ 0.5 → Alg3=2 → cascade
        rows = make_timeseries(meteo_row(ws10_avg=5.0), n=73)
        df = run_meteo(con, rows)
        assert dqc(df, "ws_avg_dqc", row=-1) == "222"

    def test_pos2_fails_persistence_3h(self, con):
        # Row 0: ws=4.0 (makes Δ12h = |5.0−4.0|=1.0 > 0.5 → pos1=9)
        # Rows 1–72: ws=5.0 (Δ3h = |5.0−5.0|=0 ≤ 0.1 → pos2=2 → cascade pos3=2)
        rows = make_timeseries(
            meteo_row(ws10_avg=5.0), n=73,
            overrides={0: {"ws10_avg": 4.0}}
        )
        df = run_meteo(con, rows)
        assert dqc(df, "ws_avg_dqc", row=-1) == "922"

    def test_pos3_fails_range(self, con):
        # Single row: ws=30 > 25 → Alg1=2; LAG=NULL → pos1=5, pos2=5
        row = meteo_row(ws10_avg=30.0)
        df = run_meteo(con, [row])
        assert dqc(df, "ws_avg_dqc") == "552"


# ===========================================================================
# wd_avg  (wd10_avg → wd_avg)  — 3 algorithms → 4-digit DQC
#
# pos 1 (Alg3): 18-hour persistence  |wd[t] − wd[t−108]| ≤ 10° → 2
# pos 2 (Alg2): 3-hour persistence   |wd[t] − wd[t−18]|  ≤ 1°  → 2
# pos 3 (Alg1): physical range       wd < 0  OR  wd > 360       → 2
# ===========================================================================

class TestWdAvg:

    def test_null_input(self, con):
        row = meteo_row(wd10_avg=None)
        df = run_meteo(con, [row])
        assert dqc(df, "wd_avg_dqc") == "555"

    def test_pos1_fails_persistence_18h(self, con):
        # 109 rows at constant wd=180 → Δ18h=0 ≤ 10 → Alg3=2 → cascade
        rows = make_timeseries(meteo_row(wd10_avg=180.0), n=109)
        df = run_meteo(con, rows)
        assert dqc(df, "wd_avg_dqc", row=-1) == "222"

    def test_pos2_fails_persistence_3h(self, con):
        # Row 0: wd=90 (so Δ18h = |180−90|=90 > 10 → pos1=9)
        # Rows 1–108: wd=180 (Δ3h = 0 ≤ 1 → pos2=2 → cascade pos3=2)
        rows = make_timeseries(
            meteo_row(wd10_avg=180.0), n=109,
            overrides={0: {"wd10_avg": 90.0}}
        )
        df = run_meteo(con, rows)
        assert dqc(df, "wd_avg_dqc", row=-1) == "922"

    def test_pos3_fails_range(self, con):
        # Single row: wd=400 > 360 → Alg1=2; LAG=NULL → pos1=5, pos2=5
        row = meteo_row(wd10_avg=400.0)
        df = run_meteo(con, [row])
        assert dqc(df, "wd_avg_dqc") == "552"

    def test_all_pass(self, con):
        # Row 0: wd=90 → provides 18h variation; rows vary slightly within 3h
        rows = make_timeseries(
            meteo_row(wd10_avg=180.0), n=109,
            overrides={
                0:  {"wd10_avg": 90.0},
                # introduce small 3h variation at the last few rows
                **{i: {"wd10_avg": 180.0 + (i - 90) * 0.5}
                   for i in range(91, 109)}
            }
        )
        df = run_meteo(con, rows)
        # Δ18h at row 108: |last_wd − row0_wd| = |180+18*0.5−90| = |189−90|=99 > 10 → pos1=9
        # Δ3h  at row 108: |last_wd − row90_wd| = |189−180|=9 > 1 → pos2=9
        # range: 189 ≤ 360 → pos3=9
        assert dqc(df, "wd_avg_dqc", row=-1) == "999"


# ===========================================================================
# rain  — 3 algorithms → 4-digit DQC
#
# pos 1 (Alg3): 24-hour accumulation  SUM(143 preceding+current) > 100 → 2
# pos 2 (Alg2): 1-hour accumulation   SUM(5 preceding+current)   > 25  → 2
# pos 3 (Alg1): climatic normals      rain < 0 OR rain > rain_max      → 2
# ===========================================================================

class TestRain:

    def test_all_pass(self, con):
        # Small rain, single row: 1h sum=0.5 ≤ 25, 24h sum=0.5 ≤ 100, 0.5 ≤ 150
        row = meteo_row(rain=0.5)
        df = run_meteo(con, [row])
        # LAG-based SUM with 1 row → partial windows → still ≤ thresholds
        assert dqc(df, "rain_dqc") == "999"

    def test_null_input(self, con):
        row = meteo_row(rain=None)
        df = run_meteo(con, [row])
        assert dqc(df, "rain_dqc") == "555"

    def test_pos1_fails_24h_accumulation(self, con):
        # 144 rows × rain=1.0: 24h SUM=144 > 100 → Alg3=2 → cascade
        # 1h SUM at last row = 6 ≤ 25 → Alg2=9 (but overridden by cascade)
        rows = make_timeseries(meteo_row(rain=1.0), n=144)
        df = run_meteo(con, rows)
        assert dqc(df, "rain_dqc", row=-1) == "222"

    def test_pos2_fails_1h_accumulation(self, con):
        # 6 rows × rain=5.0: 1h SUM=30 > 25 → Alg2=2 → cascade pos3=2
        # 24h SUM=30 ≤ 100 → Alg3=9
        rows = make_timeseries(meteo_row(rain=5.0), n=6)
        df = run_meteo(con, rows)
        assert dqc(df, "rain_dqc", row=-1) == "922"

    def test_pos3_fails_normals(self, con):
        # rain=15 > rain_max=10 → Alg1=2
        # 1h SUM=15 ≤ 25 → Alg2=9; 24h SUM=15 ≤ 100 → Alg3=9
        row = meteo_row(rain=15.0, rain_max=10.0)
        df = run_meteo(con, [row])
        assert dqc(df, "rain_dqc") == "992"

    def test_no_cascade_from_5(self, con):
        # Single row: SUM windows have 1 row, both ≤ thresholds → pos1=9, pos2=9
        # rain in range → pos3=9
        row = meteo_row(rain=0.5)
        df = run_meteo(con, [row])
        assert dqc(df, "rain_dqc") == "999"


# ===========================================================================
# Structural tests
# ===========================================================================

class TestMeteoStructure:

    def test_output_has_expected_dqc_columns(self, con):
        df = run_meteo(con, [meteo_row()])
        for col in ["temp_avg_dqc", "rh_avg_dqc", "press_avg_dqc",
                    "ws_avg_dqc", "wd_avg_dqc", "rain_dqc"]:
            assert col in df.columns, f"Missing DQC column: {col}"

    def test_all_placeholders_are_zero(self, con):
        # 2-alg (press) and 1-alg (rh) variables carry a leading '0' placeholder.
        # 3-alg variables (temp, ws, wd, rain) have no placeholder digit.
        ONE_ALG = {"rh_avg_dqc"}
        TWO_ALG = {"press_avg_dqc"}
        rows_pass = [meteo_row()]
        rows_fail = make_timeseries(meteo_row(tp_sfc=25.0), n=73)
        df_pass = run_meteo(con, rows_pass)
        df_fail = run_meteo(con, rows_fail)
        for df in [df_pass, df_fail]:
            for col in [c for c in df.columns if c.endswith("_dqc")]:
                if col in ONE_ALG or col in TWO_ALG:
                    for val in df[col]:
                        assert str(val)[0] == "0", \
                            f"Leading placeholder not '0' in {col}: got '{val}'"
                else:
                    for val in df[col]:
                        assert len(str(val)) == 3, \
                            f"Expected 3 digits in {col}: got '{val}'"

    def test_missing_variable_columns_not_validated(self, con):
        # Table with only base columns (no tp_sfc, humid, etc.)
        from datetime import datetime
        bare = {
            "acronym": "TST",
            "timestamp": datetime(2024, 6, 15, 12, 0),
            "year": 2024, "day": 167, "min": 720,
            "latitude": -15.0, "longitude": -47.0,
            "tp_min": 10.0, "tp_max": 40.0,
            "press_min": 900.0, "press_max": 1020.0,
            "rain_max": 150.0,
        }
        df = run_meteo(con, [bare])
        for col in ["temp_avg_dqc", "rh_avg_dqc", "press_avg_dqc",
                    "ws_avg_dqc", "wd_avg_dqc", "rain_dqc"]:
            assert col not in df.columns, f"Unexpected column: {col}"