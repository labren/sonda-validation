# INPE SONDA Validation System

A comprehensive data validation system for solarimetric and meteorological data from INPE SONDA (Sistema de Organização Nacional de Dados Ambientais) stations.

## Overview

This project provides automated quality control and validation for solarimetric and meteorological data collected from INPE SONDA weather stations across Brazil. The system performs comprehensive data quality checks using multiple validation algorithms and generates quality control flags (DQC - Data Quality Control) for each measurement.

## Features

### Solarimetric Data Validation
- **Global Solar Radiation (glo_avg)**: Validates global horizontal irradiance measurements
- **Direct Solar Radiation (dir_avg)**: Validates direct normal irradiance measurements  
- **Diffuse Solar Radiation (dif_avg)**: Validates diffuse horizontal irradiance measurements
- **Longwave Radiation (lw_avg)**: Validates longwave radiation measurements
- **PAR Radiation (par_avg)**: Validates Photosynthetically Active Radiation measurements
- **Lux Measurements (lux_avg)**: Validates illuminance measurements

### Meteorological Data Validation
- **Temperature (temp_avg, temp_max, temp_min)**: Validates air temperature measurements
- **Relative Humidity (rh_avg)**: Validates humidity measurements
- **Atmospheric Pressure (press_avg)**: Validates barometric pressure measurements
- **Wind Speed (ws_avg)**: Validates wind speed measurements
- **Wind Direction (wd_avg)**: Validates wind direction measurements
- **Precipitation (rain)**: Validates rainfall measurements

### Quality Control Features
- **Multi-algorithm validation**: Each variable is validated using multiple algorithms
- **Climatic normals comparison**: Data is compared against regional climatic normals
- **Temporal consistency checks**: Validates data consistency over time
- **Solar geometry calculations**: Automatically calculates solar angles and extraterrestrial radiation
- **Station metadata integration**: Uses station coordinates and metadata for validation

## Installation

### Prerequisites
- Python 3.12 or higher
- UV package manager (recommended) or pip

### Dependencies

The project requires the following Python packages:

```bash
# Core dependencies
pandas>=2.0.0
duckdb>=0.9.0
numpy>=1.24.0

# Solar calculations
astral>=3.0.0
timezonefinder>=6.0.0

# Optional: for better performance
pyarrow>=12.0.0
```

### Installation Steps

1. **Clone the repository:**
```bash
git clone <repository-url>
cd sonda-validation
```

2. **Install dependencies using UV (recommended):**
```bash
uv sync
```

3. **Or install using pip:**
```bash
pip install pandas duckdb numpy astral timezonefinder pyarrow
```

## Project Structure

```
sonda-validation/
├── main.py                    # Main execution script
├── pyproject.toml            # Project configuration
├── uv.lock                   # Dependency lock file
├── core/                     # Core validation modules
│   ├── sondaUtils.py         # Utility functions
│   └── sondaValidator.py     # Validation algorithms
├── data/                     # Data directory
│   ├── raw/                  # Input parquet files
│   └── output/               # Validated data output
├── INPESONDA_stations.csv    # Station metadata
├── INPESONDA_normais.csv     # Climatic normals
└── README.md                 # This file
```

## Usage

### Basic Usage

The main script processes all stations in the input parquet file:

```bash
python main.py
```

### Configuration

Before running, ensure the following paths are correctly configured in `main.py`:

```python
# Input data file
PARQUET_FILE = "/path/to/your/Solarimetrica-001.parquet"

# Output directory
OUTPUT_DIR = "/path/to/output/directory"

# Number of rows to process (None for all data)
N_ROWS = None
```

### Processing Individual Stations

To process a specific station, modify the main script:

```python
# Process only one station
stations = ["SBR"]  # Replace with desired station code
```

### Custom Validation

You can also use the validation classes directly:

```python
import duckdb
from core.sondaValidator import SolarimetricValidator, MeteoValidator
from core.sondaUtils import auxFunctions

# Initialize database connection
con = duckdb.connect(database=":memory:")

# Load and preprocess data
auxFunctions.carregar_dados(con, "path/to/data.parquet")
# ... additional preprocessing steps

# Run solarimetric validation
solar_validator = SolarimetricValidator(con, "input_table", "output_table")
solar_validator.run_solar_validation()

# Run meteorological validation
meteo_validator = MeteoValidator(con, "input_table", "output_table")
meteo_validator.run_all()
```

## Data Quality Control (DQC) System

The validation system generates DQC flags using a three-digit code system:

### DQC Flag Structure
- **Digit 1**: Algorithm 1 validation result
- **Digit 2**: Algorithm 2 validation result  
- **Digit 3**: Algorithm 3 validation result

### DQC Flag Values
- **9**: Data passed validation (good quality)
- **2**: Data failed validation (suspicious/flagged)
- **5**: Data missing or insufficient for validation

### Example DQC Codes
- `999`: All algorithms passed - highest quality
- `992`: First two algorithms passed, third flagged
- `555`: All data missing
- `222`: All algorithms flagged - lowest quality

## Input Data Format

### Required Parquet File Structure
The input parquet file should contain the following columns:

**Station Information:**
- `acronym`: Station identifier (string)
- `timestamp`: Measurement timestamp (datetime)

**Solarimetric Variables (optional):**
- `glo_avg`, `glo_std`: Global solar radiation (W/m²)
- `dir_avg`, `dir_std`: Direct solar radiation (W/m²)
- `dif_avg`, `dif_std`: Diffuse solar radiation (W/m²)
- `lw_avg`, `lw_std`: Longwave radiation (W/m²)
- `par_avg`, `par_std`: PAR radiation (μmol/m²/s)
- `lux_avg`, `lux_std`: Illuminance (lux)

**Meteorological Variables (optional):**
- `temp_avg`, `temp_max`, `temp_min`: Air temperature (°C)
- `rh_avg`: Relative humidity (%)
- `press_avg`: Atmospheric pressure (hPa)
- `ws_avg`: Wind speed (m/s)
- `wd_avg`: Wind direction (degrees)
- `rain`: Precipitation (mm)

## Output Format

### File Structure
Validated data is saved as CSV files organized by station and date:

```
output/
├── SBR/
│   ├── solar_validated_SBR_2024-01-01.csv
│   ├── solar_validated_SBR_2024-01-02.csv
│   └── ...
├── NAT/
│   ├── solar_validated_NAT_2024-01-01.csv
│   └── ...
└── ...
```

### Output File Format
Each output CSV file contains:
- Original measurement values
- Corresponding DQC flags for each variable
- Station metadata (latitude, longitude)
- Temporal information (year, day, minute)

**Example output columns:**
```
acronym,timestamp,year,day,min,glo_avg,glo_avg_dqc,dir_avg,dir_avg_dqc,temp_avg,temp_avg_dqc,...
SBR,2024-01-01 00:00:00,2024,1,0,0.0,555,0.0,555,25.3,999,...
```

## Validation Algorithms

### Solarimetric Validation
1. **Range Validation**: Checks if values are within physically reasonable limits
2. **Extraterrestrial Comparison**: Compares with calculated extraterrestrial radiation
3. **Component Consistency**: Validates relationships between global, direct, and diffuse radiation

### Meteorological Validation
1. **Climatic Normals**: Compares against regional climatic normals
2. **Temporal Consistency**: Checks for unrealistic temporal variations
3. **Physical Limits**: Validates against known physical constraints

## Performance Considerations

### Memory Management
- The system uses DuckDB for efficient in-memory processing
- Data is processed in chunks to manage memory usage
- Automatic garbage collection is performed after processing

### Optimization Settings
```python
# DuckDB optimization settings
con.execute("PRAGMA max_temp_directory_size='100GB'")
con.execute("SET memory_limit='32GB'")
con.execute("SET threads=4")
```

## Troubleshooting

### Common Issues

1. **File Not Found Errors**
   - Ensure the parquet file path is correct
   - Check that station metadata files are in the project root

2. **Memory Issues**
   - Reduce `N_ROWS` parameter for testing
   - Increase system memory or reduce chunk sizes

3. **Missing Dependencies**
   - Install all required packages using `uv sync` or `pip install -r requirements.txt`

4. **Timezone Issues**
   - The system automatically detects timezones using coordinates
   - Ensure station coordinates are accurate in the metadata file

### Debug Mode
To run with verbose output, modify the main script to include debug prints or use Python's logging module.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Contact

For questions or support, please contact the INPE SONDA team or create an issue in the repository.

## Acknowledgments

- INPE (Instituto Nacional de Pesquisas Espaciais)
- SONDA (Sistema de Organização Nacional de Dados Ambientais)
- Brazilian meteorological and solarimetric data collection network
