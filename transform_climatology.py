import pandas as pd

df = pd.read_csv("data/sonda_climatology.csv")

# Keep the row with the most non-null values per station
df["_non_null"] = df.count(axis=1)
df = df.sort_values("_non_null", ascending=False).drop_duplicates(subset="station").drop(columns="_non_null")

# Inflation factors applied to the long-term climatology to obtain DQC bounds.
# tp_max uses 1.35 (not 1.2): the reference DQC only flags CPA temperatures above
# ~36.45 C, which corresponds to base tp_max 27 C * 1.35. The 1.2 factor produced
# 32.4 C and over-flagged ~2.5k afternoon rows. See data/DQC/report.md (temp Alg1).
out = pd.DataFrame({
    "acronym": df["station"],
    "tp_min": (df["tp_min"] * 0.8).round(1),
    "tp_max": (df["tp_max"] * 1.35).round(2),
    "press_min": (df["press_min"] * 0.8).round(1),
    "press_max": (df["press_max"] * 1.2).round(1),
    "rain_max": df["prec_cum"],
})

out = out.sort_values("acronym").reset_index(drop=True)
out.to_csv("data/metadata/INPESONDA_normais_climatology.csv", sep=";", index=False)
print(out.to_string())
