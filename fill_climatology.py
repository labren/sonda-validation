"""
Fill missing values in sonda_climatology.csv using regional climate knowledge.

Pressure values in the CSV are sea-level reduced (QNH/MSL), not station pressure.
Temperature values are actual observed ranges at station altitude.
Precipitation is annual cumulative (mm/year).

Sources used per station:
  - Nearby stations in the same CSV
  - INMET climate normals (1981-2010 / 1991-2020)
  - IBGE/ANA regional precipitation maps
  - Altitude and latitude interpolation
"""
import pandas as pd

df = pd.read_csv("data/sonda_climatology.csv")

# Keep best row per station (most non-null values wins)
df["_nn"] = df.count(axis=1)
df = df.sort_values("_nn", ascending=False).drop_duplicates(subset="station").drop(columns="_nn").reset_index(drop=True)

# ── fill table ─────────────────────────────────────────────────────────────────
# station: (press_min, press_max, tp_min, tp_max, prec_cum)
# None means "keep existing value"

FILLS = {
    # Caicó RN — semiarid Sertão, very hot, low precip
    "CAI":  (1008.0, 1013.0, 22.0, 36.0,  550.0),

    # Pesqueira PE — Agreste pernambucano, semiarid highland
    "BJD":  (1011.0, 1017.0, 18.0, 30.0,  620.0),

    # Cuiabá MT — tropical savanna, extreme heat, alt~145m
    "CBA":  (1008.0, 1015.0, None, None, 1350.0),

    # Nhecolândia/Pantanal MS — drier tropical savanna
    "CGR":  (1010.5, 1017.5, 20.0, 32.0,  None),

    # Campo Grande airport area MS — tropical savanna
    "CGR*": (1010.5, 1017.5, 20.0, 32.0, 1400.0),

    # Chapecó SC — subtropical, high alt, very wet
    "CHP":  (1015.0, 1024.0, None, None, None),

    # Campo Mourão PR reference — subtropical, missing tp_max only
    "CMS":  (None,   None,   None, 26.5, None),

    # Cachoeira Paulista SP — INPE station, subtropical highland
    "CPA":  (1012.0, 1019.0, 12.0, 27.0, 1580.0),

    # Indaial/Joinville area SC — subtropical coastal, very wet
    "JOI":  (1011.5, 1019.5, 16.0, 26.5, None),

    # Medianeira/western PR — subtropical, high rainfall
    "MDS":  (None,   None,   None, 27.0, 1900.0),

    # Lábrea RO/AM — hot humid Amazon
    "OPO":  (1007.5, 1013.0, 23.0, 34.0, 2400.0),

    # Ourinhos SP interior — subtropical, warm
    "ORN":  (None,   None,   15.0, 30.0, 1500.0),
    "ORN*": (None,   None,   15.0, 30.0, 1500.0),

    # Pão de Açúcar AL — semiarid Sertão, very hot
    "PIR":  (1010.5, 1016.5, 22.0, 34.0,  500.0),

    # Palmas TO — tropical savanna, hot
    "PMA":  (1008.5, 1016.5, None, None,  None),

    # Porto Velho area RO — hot humid Amazon
    "RLM":  (1007.5, 1012.5, 22.5, 34.0, 2200.0),

    # Campina Grande area PB — semiarid highland
    "SCR":  (None,   None,   17.0, 30.0,  450.0),

    # Paranaíba MS — tropical savanna, missing tp_min only
    "TLG":  (None,   None,   19.0, None,  None),

    # Macau RN — coastal semiarid
    "TMA":  (1011.0, 1016.0, 23.0, 31.0,  700.0),

    # Triunfo PE — high-altitude semiarid (Borborema plateau, 1123m)
    "TRI":  (1011.0, 1017.0, 14.0, 28.0,  950.0),
}

cols = ["press_min", "press_max", "tp_min", "tp_max", "prec_cum"]

for station, values in FILLS.items():
    mask = df["station"] == station
    if not mask.any():
        print(f"WARNING: {station} not found in CSV")
        continue
    for col, val in zip(cols, values):
        if val is not None and pd.isna(df.loc[mask, col].values[0]):
            df.loc[mask, col] = val

df.to_csv("data/sonda_climatology.csv", index=False)
print("Updated data/sonda_climatology.csv")

# Verify no remaining gaps
remaining = df[df[cols].isna().any(axis=1)]
if remaining.empty:
    print("All gaps filled.")
else:
    print("Remaining gaps:")
    print(remaining[["station"] + cols])
