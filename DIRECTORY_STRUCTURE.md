# Directory Structure

This document explains the expected directory structure for the sonda-validation project.

## Required Directory Structure

```
sonda-validation/
├── main.py                          # Main execution script
├── core/                            # Core validation modules
│   ├── __init__.py
│   ├── sondaUtils.py               # Utility functions
│   └── sondaValidator.py           # Validation classes
├── data/                           # Data directory
│   ├── raw/                        # Raw input data
│   │   └── Solarimetrica-001.parquet  # Main input parquet file
│   ├── metadata/                   # Metadata files
│   │   ├── INPESONDA_stations.csv # Station metadata (lat, lon)
│   │   └── INPESONDA_normais.csv  # Climate normals
│   └── output/                     # Output directory
│       └── [station_name]/         # Per-station output folders
│           └── solar_validated_[station]_[YYYY-MM].csv
├── requirements.txt                # Python dependencies
└── README.md                       # Project documentation
```

## File Locations

### Input Data
- **Main data file**: `data/raw/Solarimetrica-001.parquet`
- **Station metadata**: `data/metadata/INPESONDA_stations.csv`
- **Climate normals**: `data/metadata/INPESONDA_normais.csv`

### Output Data
- **Validated data**: `data/output/[STATION_NAME]/solar_validated_[STATION]_[YYYY-MM].csv`

## Path Resolution

The project now uses relative paths that are resolved based on the script location:

1. **Script directory**: Determined by `os.path.dirname(os.path.abspath(__file__))`
2. **Project root**: Parent directory of the script location
3. **Data paths**: Constructed relative to project root using `os.path.join()`

This ensures the project works correctly regardless of where it's installed or executed from.

## Fallback Logic

The code includes fallback logic for metadata files:
1. First tries: `data/metadata/[filename]`
2. Falls back to: `[project_root]/[filename]`

This provides flexibility for different deployment scenarios.
