# INPE SONDA Validation System

Automated quality control and validation system for solarimetric and meteorological data from INPE SONDA (Sistema de Organização Nacional de Dados Ambientais) stations across Brazil.

## Overview

The system reads station data from Parquet files, computes solar geometry, joins climatic normals, runs per-variable DQC algorithms, and writes monthly validated CSV files per station. Both solarimetric and meteorological variables are validated in a single optimized DuckDB query per domain.

## Project Structure

```
sonda-validation/
├── main.py                        # Main execution script (processes all stations)
├── pyproject.toml                 # Project configuration and dependencies
├── uv.lock                        # Dependency lock file
├── core/
│   ├── __init__.py
│   ├── sondaValidator.py          # SolarimetricValidator and MeteoValidator classes
│   └── sondaUtils.py              # auxFunctions: data loading, preprocessing, orchestration
├── data/
│   ├── metadata/
│   │   ├── INPESONDA_stations.csv # Station coordinates (station, latitude, longitude, altitude)
│   │   └── INPESONDA_normais.csv  # Climatic normals per station (tp_min/max, press_min/max, rain_max)
│   ├── raw/                       # Input Parquet files (Solarimetrica-001.parquet)
│   └── output/                    # Validated output CSVs, organized by station/month
└── validation.ipynb               # EDA and DQC visualization notebook
```

## Installation

### Prerequisites

- Python 3.12 or higher
- UV package manager (recommended) or pip

### Steps

```bash
# Clone and enter the repository
git clone <repository-url>
cd sonda-validation

# Install with UV (recommended)
uv sync

# Or with pip
pip install pandas duckdb numpy astral timezonefinder pyarrow
```

## Usage

### Run validation for all stations

```bash
python main.py
```

`main.py` reads all unique `acronym` values from the Parquet file and calls `rodar_validacao()` for each station sequentially. Outputs a timing summary at the end.

### Configuration in `main.py`

```python
PARQUET_FILE = os.path.join(SCRIPT_DIR, "data", "raw", "Solarimetrica-001.parquet")
OUTPUT_DIR   = os.path.join(SCRIPT_DIR, "data", "output")
N_ROWS = None   # Set to an integer to limit rows (useful for testing)
```

### Run validation programmatically

```python
from core.sondaUtils import auxFunctions

aux = auxFunctions()
aux.rodar_validacao(
    parquet_file="data/raw/Solarimetrica-001.parquet",
    OUTPUT_DIR="data/output",
    n_rows=None,     # None = all rows
    station="SBR",   # None = all stations in file
)
```

## Processing Pipeline

Each station is processed independently:

1. **Load data** — `carregar_dados()`: reads the Parquet file into a DuckDB in-memory table `solar_raw`, optionally filtered by station, row limit, or random sample.
2. **Preprocess** — `preprocess_conversion_data_fill_time()`: casts all measurement columns to `DOUBLE`, fills the time series to a regular 10-minute grid (inserting `NULL` rows for missing intervals), and flags complete rows with a `valid` boolean.
3. **Join metadata** — creates table `solar_with_meta` by joining:
   - `INPESONDA_stations.csv` → adds `latitude`, `longitude`
   - `INPESONDA_normais.csv` → adds `tp_min`, `tp_max`, `press_min`, `press_max`, `rain_max`
4. **Solar geometry** — `add_mu0_to_duckdb()`: computes per-row solar cosine zenith angle `mu0` and solar azimuth `azs` using the `astral` library. Processes in adaptive chunks (25K–100K rows) to control memory. Timezone is auto-detected from station coordinates via `timezonefinder`.
5. **Extraterrestrial radiation** — `add_sa_sum()`: adds columns:
   - `Sa = S0 / UA²` where `S0 = 1361 W/m²` (solar constant) and `UA = 1.0` AU
   - `Sum = dif_avg + dir_avg × mu0` (theoretical global from components)
6. **Solar validation** — `SolarimetricValidator.run_solar_validation()`: builds a single DuckDB `CREATE TABLE AS SELECT` that computes DQC flags for all present solar columns in one query pass.
7. **Meteorological validation** — `MeteoValidator.run_all()`: same pattern — single-query validation for all present meteorological columns.
8. **Consolidation** — merges the two validated tables (`solar_validated_solar` + `solar_validated_meteo`) with a `FULL OUTER JOIN` on `(acronym, timestamp)` into `final_consolidated`.
9. **Output** — saves one semicolon-delimited CSV per calendar month into `data/output/<station>/solar_validated_<station>_<YYYY-MM>.csv`.

## Validation Algorithms

### DQC Flag System

Each validated variable receives a DQC code: a 2- or 3-character string, one digit per algorithm.

| Digit value | Meaning |
|-------------|---------|
| `9` | Passed validation (good quality) |
| `2` | Failed validation (suspicious/flagged) |
| `5` | Missing data or insufficient context for validation |

Examples: `999` = all three algorithms passed; `295` = algorithm 1 passed, algorithm 2 flagged, algorithm 3 insufficient data; `55` = both algorithms have missing data.

---

### Solarimetric Variables

Input columns require both `*_avg` and `*_std` (standard deviation within the 10-minute interval). A zero standard deviation (`std = 0`) triggers a flag (`2`) on the first algorithm, indicating a stuck sensor.

#### Global Horizontal Irradiance — `glo_avg` → `glo_avg_dqc` (3 digits)

| Alg | Check | Flag |
|-----|-------|------|
| 1 | `NULL`/`NULL std` → 5; `std = 0` → 2; `glo_avg < -4` or `> Sa × 1.5 × mu0^1.2 + 100` → 2 | Physically possible limit |
| 2 | `NULL` → 5; `glo_avg < -2` or `> Sa × 1.2 × mu0^1.2 + 50` → 2 | Extremely rare limit |
| 3 | `NULL` or `Sum ≤ 50` → 5; `azs < 75°` and `|glo_avg/Sum - 1| > 0.10` → 2; `75° ≤ azs < 93°` and `> 0.15` → 2 | Component consistency (relaxed near horizon) |

#### Direct Normal Irradiance — `dir_avg` → `dir_avg_dqc` (3 digits)

| Alg | Check | Flag |
|-----|-------|------|
| 1 | `NULL`/`NULL std` → 5; `std = 0` → 2; `dir_avg < -4` or `> Sa` → 2 | Physically possible limit |
| 2 | `NULL` → 5; `dir_avg < -2` or `> Sa × 0.95 × mu0^0.2 + 10` → 2 | Extremely rare limit |
| 3 | `NULL` → 5; `(dir_avg × mu0 - 50) > (glo_avg - dir_avg)` or `(glo_avg - dir_avg) > (dir_avg × mu0 + 50)` → 2 | Diffuse component consistency |

#### Diffuse Horizontal Irradiance — `dif_avg` → `dif_avg_dqc` (3 digits)

| Alg | Check | Flag |
|-----|-------|------|
| 1 | `NULL`/`NULL std` → 5; `std = 0` → 2; `dif_avg < -4` or `> Sa × 0.95 × mu0^1.2 + 50` → 2 | Physically possible limit |
| 2 | `NULL` → 5; `dif_avg < -2` or `> Sa × 0.75 × mu0^1.2 + 30` → 2 | Extremely rare limit |
| 3 | `NULL` or `glo_avg ≤ 50` → 5; `azs < 75°` and `dif_avg / glo_avg > 1.05` → 2; `75° ≤ azs < 93°` and `> 1.10` → 2 | Diffuse fraction check |

#### Longwave Radiation — `lw_avg` → `lw_avg_dqc` (2 digits)

| Alg | Check | Flag |
|-----|-------|------|
| 1 | `NULL`/`NULL std` → 5; `std = 0` → 2; `lw_avg < 40` or `> 700` → 2 | Physically possible limit (W/m²) |
| 2 | `NULL` → 5; `lw_avg < 60` or `> 500` → 2 | Extremely rare limit |

#### PAR Radiation — `par_avg` → `par_avg_dqc` (2 digits)

Limits scaled from solar irradiance using a conversion factor of **2.07** (µmol/m²/s per W/m²).

| Alg | Check | Flag |
|-----|-------|------|
| 1 | `NULL`/`NULL std` → 5; `std = 0` → 2; `par_avg < -4` or `> 2.07 × (Sa × 1.5 × mu0^1.2 + 100)` → 2 | Physically possible limit |
| 2 | `NULL` → 5; `par_avg < -2` or `> 2.07 × (Sa × 1.2 × mu0^1.2 + 50)` → 2 | Extremely rare limit |

#### Illuminance — `lux_avg` → `lux_avg_dqc` (2 digits)

Limits scaled from solar irradiance using a conversion factor of **0.1125** (klux per W/m²).

| Alg | Check | Flag |
|-----|-------|------|
| 1 | `NULL`/`NULL std` → 5; `std = 0` → 2; `lux_avg < -4` or `> 0.1125 × (Sa × 1.5 × mu0^1.2 + 100)` → 2 | Physically possible limit |
| 2 | `NULL` → 5; `lux_avg < -2` or `> 0.1125 × (Sa × 0.95 × mu0^1.2 + 50)` → 2 | Extremely rare limit |

---

### Meteorological Variables

Limits (`tp_min`, `tp_max`, `press_min`, `press_max`, `rain_max`) come from `INPESONDA_normais.csv`, joined per station. Temporal checks use DuckDB window functions with `LAG()` partitioned by station.

**Note on source column names:** the Parquet file uses `tp_sfc`, `humid`, `press`, `ws10_avg`, `wd10_avg`, and `rain`. The output CSV renames them to `temp_avg`, `rh_avg`, `press_avg`, `ws_avg`, `wd_avg`, and `rain`.

#### Temperature — `tp_sfc` → `temp_avg` / `temp_avg_dqc` (3 digits)

| Alg | Window | Check | Flag |
|-----|--------|-------|------|
| 1 | — | `NULL` → 5; `tp_sfc < tp_min` or `> tp_max` → 2 | Climatic normals bounds |
| 2 | 6 steps back (1 h) | `NULL diff` → 5; `|Δ1h| ≥ 5 °C` → 2 | Unrealistic short-term jump |
| 3 | 72 steps back (12 h) | `NULL diff` → 5; `|Δ12h| ≤ 0.5 °C` → 2 | Persistence / stuck sensor |

#### Relative Humidity — `humid` → `rh_avg` / `rh_avg_dqc` (1 digit)

| Alg | Check | Flag |
|-----|-------|------|
| 1 | `humid ≥ 0` and `≤ 100` → 9; otherwise → 5 | Physical range |

#### Atmospheric Pressure — `press` → `press_avg` / `press_avg_dqc` (2 digits)

| Alg | Window | Check | Flag |
|-----|--------|-------|------|
| 1 | — | `NULL` → 5; `press < press_min` or `> press_max` → 2 | Climatic normals bounds |
| 2 | 18 steps back (3 h) | `NULL diff` → 5; `|Δ3h| < 6 hPa` → 2 | Insufficient variation (stuck) |

#### Wind Speed — `ws10_avg` → `ws_avg` / `ws_avg_dqc` (3 digits)

| Alg | Window | Check | Flag |
|-----|--------|-------|------|
| 1 | — | `NULL` → 5; `ws10_avg < 0` or `> 25 m/s` → 2 | Physical range |
| 2 | 18 steps back (3 h) | `NULL diff` → 5; `|Δ3h| ≤ 0.1 m/s` → 2 | Persistence check |
| 3 | 72 steps back (12 h) | `NULL diff` → 5; `|Δ12h| ≤ 0.5 m/s` → 2 | Long-term persistence |

#### Wind Direction — `wd10_avg` → `wd_avg` / `wd_avg_dqc` (3 digits)

| Alg | Window | Check | Flag |
|-----|--------|-------|------|
| 1 | — | `NULL` → 5; `wd10_avg < 0°` or `> 360°` → 2 | Physical range |
| 2 | 18 steps back (3 h) | `NULL diff` → 5; `|Δ3h| ≤ 1°` → 2 | Persistence check |
| 3 | 108 steps back (18 h) | `NULL diff` → 5; `|Δ18h| ≤ 10°` → 2 | Long-term persistence |

#### Precipitation — `rain` → `rain` / `rain_dqc` (3 digits)

| Alg | Window | Check | Flag |
|-----|--------|-------|------|
| 1 | — | `NULL` → 5; `rain < 0` or `> rain_max` → 2 | Climatic normals ceiling |
| 2 | 6 rows (1 h accumulation) | `NULL sum` → 5; `Σ1h > 25 mm` → 2 | Hourly accumulation limit |
| 3 | 144 rows (24 h accumulation) | `NULL sum` → 5; `Σ24h > 100 mm` → 2 | Daily accumulation limit |

---

## Input Data Format

### Parquet File

Required columns:

| Column | Type | Description |
|--------|------|-------------|
| `acronym` | string | Station identifier (e.g., `SBR`, `NAT`) |
| `timestamp` | datetime | Measurement timestamp (UTC, 10-minute resolution) |

Optional solarimetric columns (validated if present):

| Column | Unit | Description |
|--------|------|-------------|
| `glo_avg`, `glo_std` | W/m² | Global horizontal irradiance |
| `dir_avg`, `dir_std` | W/m² | Direct normal irradiance |
| `dif_avg`, `dif_std` | W/m² | Diffuse horizontal irradiance |
| `lw_avg`, `lw_std` | W/m² | Longwave radiation |
| `par_avg`, `par_std` | µmol/m²/s | Photosynthetically Active Radiation |
| `lux_avg`, `lux_std` | klux | Illuminance |

Optional meteorological columns (validated if present):

| Column | Unit | Description |
|--------|------|-------------|
| `tp_sfc` | °C | Air temperature at surface |
| `humid` | % | Relative humidity |
| `press` | hPa | Atmospheric pressure |
| `ws10_avg` | m/s | Wind speed at 10 m |
| `wd10_avg` | ° | Wind direction at 10 m |
| `rain` | mm | Precipitation |

### Metadata Files

**`data/metadata/INPESONDA_stations.csv`** — comma-separated:
```
station,latitude,longitude,altitude
SBR,-15.601,-47.713,1023
...
```

**`data/metadata/INPESONDA_normais.csv`** — semicolon-separated:
```
acronym;tp_min;tp_max;press_min;press_max;rain_max
SBR;12.0;32.0;880.0;960.0;150
...
```

## Output Format

Monthly CSV files per station, semicolon-delimited:

```
data/output/
├── SBR/
│   ├── solar_validated_SBR_2024-01.csv
│   ├── solar_validated_SBR_2024-02.csv
│   └── ...
├── NAT/
│   └── ...
```

### Output Columns

```
acronym;timestamp;year;day;min;
glo_avg;glo_avg_dqc;
dir_avg;dir_avg_dqc;
dif_avg;dif_avg_dqc;
lw_avg;lw_avg_dqc;
par_avg;par_avg_dqc;
lux_avg;lux_avg_dqc;
temp_avg;temp_avg_dqc;
rh_avg;rh_avg_dqc;
press_avg;press_avg_dqc;
ws_avg;ws_avg_dqc;
wd_avg;wd_avg_dqc;
rain;rain_dqc
```

Columns are only present if the corresponding source column existed in the input Parquet file.

### Example rows

```
acronym;timestamp;year;day;min;glo_avg;glo_avg_dqc;dir_avg;dir_avg_dqc;...
SBR;2024-01-01 00:00:00;2024;1;0;0.0;555;0.0;555;...
SBR;2024-01-01 06:10:00;2024;1;370;850.3;999;620.1;999;...
SBR;2024-01-01 06:20:00;2024;1;380;-5.1;299;0.0;999;...
```

## Validation Notebook (`validation.ipynb`)

Jupyter notebook for exploratory analysis of validated output:

- Reads a monthly CSV from `data/output/`
- Filters by date range
- Plots time series of any variable (e.g., `glo_avg`)
- **DQC heatmap**: 2D grid of days × time-of-day for each DQC column — blue cells = `999`/`99` (good data), red cells = any other code (suspect or missing)
- Prints per-variable DQC distribution and data availability percentage

## Performance

- **DuckDB in-memory**: all processing done in DuckDB without writing intermediate files
- **Single-query validation**: both solar and meteo validations use a single `CREATE TABLE AS SELECT` with all algorithm `CASE` expressions evaluated in one pass
- **Chunked solar angle calculation**: `mu0`/`azs` computed in chunks of 25K–100K rows (adaptive based on dataset size) to prevent memory exhaustion
- **Per-station isolation**: each station uses its own DuckDB connection, closed and garbage-collected after processing

### DuckDB settings (applied per station)

```python
SET memory_limit = '16GB'
SET threads = 4
SET preserve_insertion_order = false
SET enable_object_cache = true
```

## Troubleshooting

**`FileNotFoundError` for metadata CSVs** — the system looks in `data/metadata/` first, then falls back to the project root. Ensure `INPESONDA_stations.csv` and `INPESONDA_normais.csv` are in `data/metadata/`.

**Missing latitude/longitude after join** — the `acronym` in the Parquet file must match the `station` column in `INPESONDA_stations.csv` (case-insensitive, whitespace-trimmed).

**Memory issues** — reduce `N_ROWS` in `main.py` for testing, or reduce chunk size in `add_mu0_to_duckdb()`.

**Timezone detection failure** — `timezonefinder` requires valid coordinates. Check that the station exists in `INPESONDA_stations.csv` with non-null latitude/longitude.

## Dependencies

| Package | Purpose |
|---------|---------|
| `duckdb` | In-memory SQL engine for all data processing |
| `pandas` | DataFrame handling for metadata and chunked updates |
| `numpy` | Numeric operations in solar angle calculations |
| `astral` | Solar elevation and azimuth calculations |
| `timezonefinder` | Automatic timezone detection from coordinates |
| `pyarrow` | Parquet file reading |

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).

## Acknowledgments

- INPE (Instituto Nacional de Pesquisas Espaciais)
- SONDA (Sistema de Organização Nacional de Dados Ambientais)