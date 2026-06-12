"""
Tests for SolarimetricValidator.

Describes the NEW desired behaviour:
  - DQC digit order is REVERSED: position 1 = Alg3, pos 2 = Alg2, pos 3 = Alg1
  - Only flag=2 cascades forward: if pos N = 2, pos N+1 is forced to 2
  - flag=5 (missing) does NOT cascade
  - A 4th placeholder digit is always '0'

DQC width by variable:
  glo_avg, dir_avg, dif_avg  →  4 digits  (3 algs + placeholder)
  lw_avg, par_avg, lux_avg   →  3 digits  (2 algs + placeholder)
"""
import pytest
from tests.conftest import (
    solar_row, run_solar, dqc,
    MU0, SA, MU0_12, MU0_02,
    GLO_ALG2_UPPER, GLO_ALG1_UPPER,
    DIR_ALG2_UPPER,
    DIF_ALG2_UPPER,
    PAR_ALG2_UPPER,
    LUX_ALG2_UPPER,
)


# ===========================================================================
# glo_avg — 3 algorithms → 4-digit DQC
#
# pos 1 (Alg3): component consistency  |glo/Sum − 1| > threshold
# pos 2 (Alg2): extremely rare range   glo > Sa×1.2×mu0^1.2 + 50
# pos 3 (Alg1): physically possible    glo > Sa×1.5×mu0^1.2 + 100  OR  std=0
# pos 4:        placeholder            always '0'
# ===========================================================================

class TestGloAvg:

    def test_all_pass(self, con):
        row = solar_row(glo_avg=600.0, glo_std=30.0, Sum=600.0)
        df = run_solar(con, [row])
        assert dqc(df, "glo_avg_dqc") == "999"

    def test_null_input(self, con):
        # All algorithms independently return 5 for NULL input
        row = solar_row(glo_avg=None, glo_std=None, Sum=None)
        df = run_solar(con, [row])
        assert dqc(df, "glo_avg_dqc") == "555"

    def test_pos1_fails_consistency(self, con):
        # Sum=100 (> 50 so not missing), |600/100 − 1| = 5.0 > 0.10 → Alg3=2
        # cascade → pos2=2, pos3=2
        row = solar_row(glo_avg=600.0, glo_std=30.0, Sum=100.0)
        df = run_solar(con, [row])
        assert dqc(df, "glo_avg_dqc") == "222"

    def test_pos2_fails_rare_range(self, con):
        # glo > Alg2 limit but < Alg1 limit; Sum=glo so consistency passes
        glo = round(GLO_ALG2_UPPER + 20.0, 1)   # ≈ 1320
        row = solar_row(glo_avg=glo, glo_std=30.0, Sum=glo)
        df = run_solar(con, [row])
        assert dqc(df, "glo_avg_dqc") == "922"

    def test_pos3_fails_std_zero(self, con):
        # std=0 triggers only Alg1; value and consistency are fine
        row = solar_row(glo_avg=600.0, glo_std=0.0, Sum=600.0)
        df = run_solar(con, [row])
        assert dqc(df, "glo_avg_dqc") == "992"

    def test_no_cascade_from_5(self, con):
        # Sum ≤ 50 → Alg3 returns 5 (missing context), NOT 2
        # flag=5 must not cascade; Alg2 and Alg1 evaluate independently → 9
        row = solar_row(glo_avg=600.0, glo_std=30.0, Sum=30.0)
        df = run_solar(con, [row])
        assert dqc(df, "glo_avg_dqc") == "599"

    def test_dqc_is_3_digits(self, con):
        # glo_avg is a 3-alg variable — no placeholder digit, always 3 chars
        cases = [
            solar_row(glo_avg=600.0, glo_std=30.0, Sum=600.0),
            solar_row(glo_avg=600.0, glo_std=30.0, Sum=100.0),
            solar_row(glo_avg=round(GLO_ALG2_UPPER + 20, 1),
                      glo_std=30.0, Sum=round(GLO_ALG2_UPPER + 20, 1)),
        ]
        df = run_solar(con, cases)
        for val in df["glo_avg_dqc"]:
            assert len(str(val)) == 3, f"Expected 3 digits, got: {val}"


# ===========================================================================
# dir_avg — 3 algorithms → 4-digit DQC
#
# pos 1 (Alg3): diffuse consistency  (dir×mu0 − 50) > (glo − dir)
#                                  OR (glo − dir) > (dir×mu0 + 50)
# pos 2 (Alg2): extremely rare       dir > Sa×0.95×mu0^0.2 + 10
# pos 3 (Alg1): physically possible  dir > Sa  OR  std=0
#
# Consistency check uses glo_avg, so glo_avg must be in the test table.
# Set Sum=glo_avg to keep glo's own consistency check passing.
# ===========================================================================

class TestDirAvg:

    def _closure_glo(self, dir_v, dif_v=200.0):
        """glo satisfying the dir Alg3 closure  GHI = DHI + DNI·cos(z):
        glo = dif + dir·mu0  (so |glo − dir·mu0 − dif| / glo = 0 ≤ 0.10).
        dif_v defaults to the solar_row default (200.0)."""
        return dif_v + dir_v * MU0

    def test_all_pass(self, con):
        dir_v = 500.0
        glo_v = self._closure_glo(dir_v)   # = 200 + 400 = 600.0
        row = solar_row(dir_avg=dir_v, dir_std=20.0, glo_avg=glo_v, Sum=glo_v)
        df = run_solar(con, [row])
        assert dqc(df, "dir_avg_dqc") == "999"

    def test_null_input(self, con):
        row = solar_row(dir_avg=None, dir_std=None)
        df = run_solar(con, [row])
        assert dqc(df, "dir_avg_dqc") == "555"

    def test_pos1_fails_consistency(self, con):
        # glo - dir = 1000 - 500 = 500 > dir*mu0 + 50 = 450 → Alg3=2 → cascade
        row = solar_row(dir_avg=500.0, dir_std=20.0, glo_avg=1000.0, Sum=1000.0)
        df = run_solar(con, [row])
        assert dqc(df, "dir_avg_dqc") == "222"

    def test_pos2_fails_rare_range(self, con):
        # dir > Alg2 limit but < Sa; set glo so the closure (Alg3) passes
        dir_v = round(DIR_ALG2_UPPER + 5.0, 1)    # ≈ 1250.4
        glo_v = self._closure_glo(dir_v)           # ≈ 1200.3
        row = solar_row(dir_avg=dir_v, dir_std=20.0, glo_avg=glo_v, Sum=glo_v)
        df = run_solar(con, [row])
        assert dqc(df, "dir_avg_dqc") == "922"

    def test_pos3_fails_std_zero(self, con):
        dir_v = 500.0
        glo_v = self._closure_glo(dir_v)
        row = solar_row(dir_avg=dir_v, dir_std=0.0, glo_avg=glo_v, Sum=glo_v)
        df = run_solar(con, [row])
        assert dqc(df, "dir_avg_dqc") == "992"

    def test_negative_dir_passes_consistency(self, con):
        # dir_avg=-0.5 (instrument noise at dawn/dusk) would spuriously inflate
        # glo - dir above the consistency window if not guarded.
        # Guard: dir_avg <= 0 → Alg3 = 9 (no consistency check), no cascade.
        row = solar_row(dir_avg=-0.5, dir_std=0.1, glo_avg=50.5, Sum=50.5)
        df = run_solar(con, [row])
        assert dqc(df, "dir_avg_dqc") == "999"


# ===========================================================================
# dif_avg — 3 algorithms → 4-digit DQC
#
# pos 1 (Alg3): diffuse fraction  dif/glo > 1.05  (azs < 75°)
# pos 2 (Alg2): extremely rare    dif > Sa×0.75×mu0^1.2 + 30
# pos 3 (Alg1): physically poss.  dif > Sa×0.95×mu0^1.2 + 50  OR  std=0
#
# Consistency check uses glo_avg.
# ===========================================================================

class TestDifAvg:

    def test_all_pass(self, con):
        # 200/600 ≈ 0.33 ≤ 1.05 → pos1 passes
        row = solar_row(dif_avg=200.0, dif_std=10.0, glo_avg=600.0, Sum=600.0)
        df = run_solar(con, [row])
        assert dqc(df, "dif_avg_dqc") == "999"

    def test_null_input(self, con):
        row = solar_row(dif_avg=None, dif_std=None)
        df = run_solar(con, [row])
        assert dqc(df, "dif_avg_dqc") == "555"

    def test_pos1_fails_diffuse_fraction(self, con):
        # 700/600 ≈ 1.167 > 1.05 (azs=45 < 75) → Alg3=2 → cascade
        row = solar_row(dif_avg=700.0, dif_std=10.0, glo_avg=600.0, Sum=600.0)
        df = run_solar(con, [row])
        assert dqc(df, "dif_avg_dqc") == "222"

    def test_pos2_fails_rare_range(self, con):
        # dif > Alg2 limit; dif/glo ≤ 1.05 so pos1 passes
        dif_v = round(DIF_ALG2_UPPER + 10.0, 1)   # ≈ 820
        glo_v = 900.0                               # 820/900 ≈ 0.91
        row = solar_row(dif_avg=dif_v, dif_std=10.0, glo_avg=glo_v, Sum=glo_v)
        df = run_solar(con, [row])
        assert dqc(df, "dif_avg_dqc") == "922"

    def test_pos3_fails_std_zero(self, con):
        row = solar_row(dif_avg=200.0, dif_std=0.0, glo_avg=600.0, Sum=600.0)
        df = run_solar(con, [row])
        assert dqc(df, "dif_avg_dqc") == "992"

    def test_no_cascade_from_5_glo_too_low(self, con):
        # glo_avg ≤ 50 → Alg3 returns 5 (insufficient context, not failure)
        # flag=5 must not cascade; Alg2 and Alg1 evaluate independently
        row = solar_row(dif_avg=200.0, dif_std=10.0, glo_avg=40.0, Sum=40.0)
        df = run_solar(con, [row])
        assert dqc(df, "dif_avg_dqc") == "599"


# ===========================================================================
# lw_avg — 3 algorithms → 3-digit DQC (when tp_sfc is available)
#
# pos 1 (Alg3): S-B temp consistency  returns 5 if any tp algorithm fires
# pos 2 (Alg2): stricter range        lw < 60  OR  lw > 500
# pos 3 (Alg1): physical range        lw < 40  OR  lw > 700  OR  std=0
#
# solar_row() default includes tp_sfc=25.0 (within tp_min=10/tp_max=40),
# so Alg3 = 9 for all passing-temp cases (single row → LAG returns NULL → tp_r2=5,
# which is not 2, so it does not trigger the Alg3=5 condition).
# ===========================================================================

class TestLwAvg:

    def test_all_pass(self, con):
        row = solar_row(lw_avg=350.0, lw_std=5.0)
        df = run_solar(con, [row])
        assert dqc(df, "lw_avg_dqc") == "999"

    def test_null_input(self, con):
        row = solar_row(lw_avg=None, lw_std=None)
        df = run_solar(con, [row])
        assert dqc(df, "lw_avg_dqc") == "555"

    def test_pos1_fails_stricter_range(self, con):
        # 55 < 60 → Alg2=2 → cascade Alg1=2; Alg3=9 (tp passing)
        row = solar_row(lw_avg=55.0, lw_std=5.0)
        df = run_solar(con, [row])
        assert dqc(df, "lw_avg_dqc") == "922"

    def test_pos2_fails_std_zero(self, con):
        # std=0 triggers only Alg1; 350 inside stricter range → Alg2=9; Alg3=9
        row = solar_row(lw_avg=350.0, lw_std=0.0)
        df = run_solar(con, [row])
        assert dqc(df, "lw_avg_dqc") == "992"

    def test_alg3_returns_5_when_temp_fails(self, con):
        # tp_sfc < tp_min triggers tp_r1=2 → lw Alg3=5 (S-B reference unreliable)
        # Alg3=5 does NOT cascade into Alg2/Alg1 (only flag=2 cascades)
        row = solar_row(lw_avg=350.0, lw_std=5.0, tp_sfc=5.0)  # below tp_min=10
        df = run_solar(con, [row])
        assert dqc(df, "lw_avg_dqc") == "599"

    def test_dqc_is_3_digits(self, con):
        cases = [
            solar_row(lw_avg=350.0, lw_std=5.0),
            solar_row(lw_avg=55.0,  lw_std=5.0),
        ]
        df = run_solar(con, cases)
        for val in df["lw_avg_dqc"]:
            assert len(str(val)) == 3, f"Expected 3 digits, got: {val}"


# ===========================================================================
# par_avg — 2 algorithms → 3-digit DQC
#
# pos 1 (Alg2): extremely rare  par > 2.07×(Sa×1.2×mu0^1.2 + 50)
# pos 2 (Alg1): physically poss par > 2.07×(Sa×1.5×mu0^1.2 + 100)  OR  std=0
# pos 3:        placeholder      always '0'
# ===========================================================================

class TestParAvg:

    def test_all_pass(self, con):
        row = solar_row(par_avg=1000.0, par_std=50.0)
        df = run_solar(con, [row])
        assert dqc(df, "par_avg_dqc") == "099"

    def test_null_input(self, con):
        row = solar_row(par_avg=None, par_std=None)
        df = run_solar(con, [row])
        assert dqc(df, "par_avg_dqc") == "055"

    def test_pos1_fails_rare_range(self, con):
        # par > Alg2 limit → pos1=2 → cascade pos2=2
        par_v = round(PAR_ALG2_UPPER + 20.0, 1)
        row = solar_row(par_avg=par_v, par_std=50.0)
        df = run_solar(con, [row])
        assert dqc(df, "par_avg_dqc") == "022"

    def test_pos2_fails_std_zero(self, con):
        # std=0 triggers only Alg1; value inside Alg2 range → pos1=9
        row = solar_row(par_avg=1000.0, par_std=0.0)
        df = run_solar(con, [row])
        assert dqc(df, "par_avg_dqc") == "092"


# ===========================================================================
# lux_avg — 2 algorithms → 3-digit DQC
#
# pos 1 (Alg2): extremely rare  lux > 0.1125×(Sa×0.95×mu0^1.2 + 50)
# pos 2 (Alg1): physically poss lux > 0.1125×(Sa×1.5×mu0^1.2 + 100)  OR  std=0
# pos 3:        placeholder      always '0'
# ===========================================================================

class TestLuxAvg:

    def test_all_pass(self, con):
        row = solar_row(lux_avg=80.0, lux_std=4.0)
        df = run_solar(con, [row])
        assert dqc(df, "lux_avg_dqc") == "099"

    def test_null_input(self, con):
        row = solar_row(lux_avg=None, lux_std=None)
        df = run_solar(con, [row])
        assert dqc(df, "lux_avg_dqc") == "055"

    def test_pos1_fails_rare_range(self, con):
        lux_v = round(LUX_ALG2_UPPER + 5.0, 1)
        row = solar_row(lux_avg=lux_v, lux_std=4.0)
        df = run_solar(con, [row])
        assert dqc(df, "lux_avg_dqc") == "022"

    def test_pos2_fails_std_zero(self, con):
        row = solar_row(lux_avg=80.0, lux_std=0.0)
        df = run_solar(con, [row])
        assert dqc(df, "lux_avg_dqc") == "092"


# ===========================================================================
# Structural tests
# ===========================================================================

class TestSolarStructure:

    def test_output_has_expected_dqc_columns(self, con):
        df = run_solar(con, [solar_row()])
        for col in ["glo_avg_dqc", "dir_avg_dqc", "dif_avg_dqc",
                    "lw_avg_dqc", "par_avg_dqc", "lux_avg_dqc"]:
            assert col in df.columns, f"Missing DQC column: {col}"

    def test_missing_variable_columns_not_validated(self, con):
        # Table with no solar measurement columns → no DQC columns in output
        from datetime import datetime
        bare = {
            "acronym": "TST",
            "timestamp": datetime(2024, 6, 15, 12, 0),
            "year": 2024, "day": 167, "min": 720,
            "mu0": MU0, "azs": 45.0, "Sa": SA, "Sum": 0.0,
        }
        df = run_solar(con, [bare])
        for col in ["glo_avg_dqc", "dir_avg_dqc", "dif_avg_dqc",
                    "lw_avg_dqc", "par_avg_dqc", "lux_avg_dqc"]:
            assert col not in df.columns, f"Unexpected column: {col}"

    def test_all_placeholders_are_zero(self, con):
        # 2-alg variables (par, lux) carry a leading '0' placeholder.
        # 3-alg variables (glo, dir, dif, lw) have no placeholder — all 3 digits meaningful.
        TWO_ALG = {"par_avg_dqc", "lux_avg_dqc"}
        rows = [
            solar_row(),                                                      # all pass
            solar_row(glo_avg=600.0, glo_std=30.0, Sum=100.0),              # glo pos1 fails
            solar_row(glo_avg=round(GLO_ALG2_UPPER + 20, 1),
                      glo_std=30.0,
                      Sum=round(GLO_ALG2_UPPER + 20, 1)),                    # glo pos2 fails
            solar_row(lw_avg=55.0, lw_std=5.0),                             # lw pos1 fails
        ]
        df = run_solar(con, rows)
        dqc_cols = [c for c in df.columns if c.endswith("_dqc")]
        assert dqc_cols, "No DQC columns found"
        for col in dqc_cols:
            if col in TWO_ALG:
                for val in df[col]:
                    assert str(val)[0] == "0", \
                        f"Leading placeholder not '0' in {col}: got '{val}'"
            else:
                for val in df[col]:
                    assert len(str(val)) == 3, \
                        f"Expected 3 meaningful digits in {col}: got '{val}'"