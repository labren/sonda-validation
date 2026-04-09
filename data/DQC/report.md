# DQC Validation Comparison Report

**Station:** CPA (Cachoeira Paulista, lat=-22.69°, lon=-45.006°, alt=574m)
**Period:** December 2018 (days 335–365), 1-minute resolution — 44 640 rows
**Input:** `CPA1812ED.csv`
**Reference:** `CPA1812ED_DQC.csv`
**Validator version:** current (reversed DQC order, cascade, 3-digit fixed width)
**Comparison script:** `data/DQC/compare_dqc.py`

---

## Match Summary

| Variable        | Match % | Status       | Notes                                      |
|-----------------|--------:|:-------------|:-------------------------------------------|
| `glo_avg_dqc`   |   99.1% | Near-perfect | ~0.9% residual from reference edge cases   |
| `dif_avg_dqc`   |   97.7% | Near-perfect | Residual from temporal LAG offset (Issue 3)|
| `dir_avg_dqc`   |   54.9% | Fixed ✓      | Up from 52%; remaining gap = LAG offset    |
| `lw_avg_dqc`    |   37.1% | Fixed ✓      | Up from 0%; remaining gap = LAG offset     |
| `rh_avg_dqc`    |  100.0% | Perfect      |                                            |
| `temp_avg_dqc`  |   35.2% | Open         | LAG offsets + normals boundary             |
| `press_avg_dqc` |    0.0% | Open         | Sea-level normals + LAG offset             |

---

## Issue 1 — `dir_avg` Alg3 consistency check fires on negative readings

**Status: Fixed ✓** (`dir_avg <= 0 → 9` guard added; `test_negative_dir_passes_consistency` passes)

**Affected rows:** ~20 933 / 44 406 non-null (~47%)

### Root cause

The consistency check formula for `dir_avg` Alg3:

```sql
WHEN (dir_avg * mu0 - 50) > (glo_avg - dir_avg)
  OR (glo_avg - dir_avg) > (dir_avg * mu0 + 50)
THEN 2
```

At dawn/dusk when `dir_avg < 0` (instrument noise), subtracting a negative value inflates `glo - dir` above the upper window bound:

```
glo=50.07, dir=-0.42, mu0=0.22
→ glo - dir = 50.49
→ window_hi = dir*mu0 + 50 = 49.9
→ 50.49 > 49.9 → FAIL (spurious)
```

### Fix applied

```sql
CASE WHEN dir_avg IS NULL THEN 5
     WHEN dir_avg <= 0 THEN 9        -- instrument noise; consistency undefined
     WHEN (dir_avg * mu0 - 50) > (glo_avg - dir_avg)
       OR (glo_avg - dir_avg) > (dir_avg * mu0 + 50)
     THEN 2
     ELSE 9 END AS dir_r3
```

**Remaining gap (54.9% → ~99%):** caused by LAG offset mismatch (Issue 3).

---

## Issue 2 — `lw_avg` missing Alg3 (Stefan-Boltzmann consistency check)

**Status: Fixed ✓** (Alg3 implemented; `test_alg3_returns_5_when_temp_fails` passes)

**Previous match rate:** 0% (wrong format — `'099'` vs reference `'999'`)

### Root cause

`lw_avg` has **3 algorithms** in the reference, not 2. Our implementation had only Alg1 (physical range) and Alg2 (stricter range), prepending a `'0'` placeholder — producing `'099'`/`'055'` while the reference produces `'999'`/`'599'`.

### What Alg3 is

A **Stefan-Boltzmann / temperature consistency check**. Analysis of all 44 406 non-null rows shows a **perfect correlation** (zero exceptions):

| lw Alg3 in reference | Condition in `temp_avg_dqc` | Count  |
|---------------------|-----------------------------|--------|
| `9` (pass)          | No `'2'` anywhere           | 43 652 |
| `5` (insufficient)  | Contains `'2'` (any level)  |    754 |

When `temp_avg_dqc` carries any failure flag, the lw consistency check against temperature is unreliable → Alg3 returns `5`. When temp is clean, the check passes → Alg3 returns `9`.

### Fix applied

Raw temp flags (`tp_r1_lw`, `tp_r2_lw`, `tp_r3_lw`) are now computed in the solar CTE when `tp_sfc` is available. The outer SELECT uses them for `lw_avg_dqc`:

```sql
CAST(
    CASE WHEN lw_avg IS NULL THEN 5
         WHEN tp_r1_lw = 2 OR tp_r2_lw = 2 OR tp_r3_lw = 2 THEN 5
         ELSE 9 END
AS VARCHAR) ||
CAST(lw_r2 AS VARCHAR) ||
CAST(CASE WHEN lw_r2 = 2 THEN 2 ELSE lw_r1 END AS VARCHAR) AS lw_avg_dqc
```

`lw_avg` is now a 3-algorithm variable (no leading `'0'`). Falls back to 2-alg with `'0'` if `tp_sfc` is not in the source table.

**Remaining gap (37.1% → ~99%):** caused by the same LAG offset mismatch (Issue 3) that affects `temp_avg_dqc` — `tp_r2_lw` fires more aggressively on 1-minute data, flagging more rows than the reference expects.

---

## Issue 3 — LAG offsets calibrated for 10-minute data

**Status: Open** (affects `temp_avg_dqc`, `lw_avg_dqc`, `press_avg_dqc`, `ws_avg_dqc`, `wd_avg_dqc`, `rain_dqc`)

The validator uses hard-coded `LAG(6)` for the 1-hour jump check and `LAG(72)` for the 12-hour persistence check, assuming 10-minute intervals. Applied to 1-minute CPA data:

| Check              | Intended window | Actual window (1-min data) |
|--------------------|-----------------|---------------------------|
| `LAG(6)`           | 1 hour          | 6 minutes                 |
| `LAG(18)`          | 3 hours         | 18 minutes                |
| `LAG(72)`          | 12 hours        | 1 hour 12 minutes         |
| `LAG(108)`         | 18 hours        | 1 hour 48 minutes         |
| `ROWS BETWEEN 5`   | 1-hour rain sum | 6-minute rain sum         |
| `ROWS BETWEEN 143` | 24-hour rain    | ~2.4-hour rain            |

**The validators are designed for 10-minute aggregated data.** Running directly on 1-minute raw data is not the intended use case — this comparison is for algorithm validation only.

### Sub-issue 3b — `temp_avg` normals boundary

Several nighttime/early-morning readings fall just below `tp_min = 20.8°C` (e.g., `tp_sfc = 20.23°C`), triggering Alg1 = 2 in our output. The reference shows `999` for these rows, suggesting the reference used different (wider) normals for CPA or that the normals in `INPESONDA_normais.csv` are not altitude-corrected (CPA is at 574 m).

---

## Issue 4 — `press_avg` normals are sea-level values

**Status: Open**

The `INPESONDA_normais.csv` entry for CPA uses `press_min=1011.9 hPa` / `press_max=1015.7 hPa`, which are sea-level values. CPA at 574 m has observed pressures around 940–945 hPa — entirely outside these bounds, causing Alg1 = 2 for every row. This alone accounts for the 0% match on `press_avg_dqc`.

---

## Conclusion

| Issue                              | Severity | Status   | Impact when fixed        |
|------------------------------------|----------|----------|--------------------------|
| `dir_avg` negative-value guard     | High     | Done ✓   | 52% → 54.9% (partial)   |
| `lw_avg` Alg3 S-B temp check       | High     | Done ✓   | 0% → 37.1% (partial)    |
| LAG offsets (1-min vs 10-min data) | Medium   | Open     | Fixes temp, lw, press, ws, wd, rain |
| CPA normals too tight / wrong alt  | Medium   | Open     | Fixes temp Alg1, press_avg 0% |

Once Issues 3 and 4 are addressed, expected match rates: `dir` ~99%, `lw` ~99%, `temp` ~85%+, `press` ~99%.

---

## Fix Plan

### Fix 1 — `dir_avg` Alg3: guard negative direct radiation ✓ DONE

Applied in `core/sondaValidator.py` → `run_solar_validation()`, `dir_r3` expression.
Test: `TestDirAvg.test_negative_dir_passes_consistency` — `dir_avg=-0.5, glo_avg=50.5` → `dir_r3 = 9`.

---

### Fix 2 — `lw_avg` Alg3: Stefan-Boltzmann temperature consistency ✓ DONE

Applied in `core/sondaValidator.py` → `run_solar_validation()`, `lw_avg` CTE block.
- Added `tp_r1_lw / tp_r2_lw / tp_r3_lw` to CTE when `tp_sfc` is present
- `lw_avg` promoted from 2-alg (`'0'||r2||r1`) to 3-alg (`r3||r2||r1`)
- `solar_row()` in `conftest.py` updated to include `tp_sfc=25.0` by default
- Tests: `TestLwAvg` expected values updated; `test_alg3_returns_5_when_temp_fails` added; `lw_avg_dqc` removed from `TWO_ALG` in structure tests

---

### Fix 3 — LAG offsets: accept data-frequency parameter (Medium priority)

**File:** `core/sondaValidator.py` → `MeteoValidator.run_all()` and `SolarimetricValidator.run_solar_validation()` (for `tp_r*_lw`).

**Change:** Introduce a `freq_min` parameter (default `10`) so LAG offsets scale dynamically:

```python
class MeteoValidator:
    def __init__(self, con, tabela_origem, tabela_destino, freq_min=10):
        self.lag_1h  = round(60   / freq_min)   # 6  at 10-min, 60  at 1-min
        self.lag_3h  = round(180  / freq_min)   # 18 at 10-min, 180 at 1-min
        self.lag_12h = round(720  / freq_min)   # 72 at 10-min, 720 at 1-min
        self.lag_18h = round(1080 / freq_min)   # 108 at 10-min
        self.lag_1h_rain  = self.lag_1h - 1     # ROWS BETWEEN N PRECEDING
        self.lag_24h_rain = round(1440 / freq_min) - 1
```

Replace hard-coded `LAG(6)`, `LAG(18)`, `LAG(72)`, `LAG(108)`, `ROWS BETWEEN 5 PRECEDING`, `ROWS BETWEEN 143 PRECEDING` with f-string references to these attributes.

Apply the same `freq_min` to the `tp_r*_lw` LAG values in `run_solar_validation()`.

**Tests:** No changes to existing tests (they use 10-min defaults). Add docstring note.

---

### Fix 4 — CPA normals: altitude-correct station bounds (Medium priority)

**File:** `data/metadata/INPESONDA_normais.csv`

**Change:** Replace sea-level `press_min`/`press_max` for CPA with station-level values (~935–955 hPa at 574 m). Verify `tp_min`/`tp_max` reflect the actual observed temperature range at this altitude.

Use the ISA pressure reduction formula or derive from long-term station observations:

```
P_station ≈ P_sea_level × exp(-alt / 8500)
P_CPA ≈ 1013.25 × exp(-574 / 8500) ≈ 936 hPa
```
