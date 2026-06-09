import pandas as pd

df = pd.read_csv("data/sonda_climatology.csv")

# Keep the row with the most non-null values per station
df["_non_null"] = df.count(axis=1)
df = df.sort_values("_non_null", ascending=False).drop_duplicates(subset="station").drop(columns="_non_null")

out = pd.DataFrame({
    "acronym": df["station"],
    "tp_min": (df["tp_min"] * 0.8).round(1),
    "tp_max": (df["tp_max"] * 1.2).round(1),
    "press_min": (df["press_min"] * 0.8).round(1),
    "press_max": (df["press_max"] * 1.2).round(1),
    "rain_max": df["prec_cum"],
})

out = out.sort_values("acronym").reset_index(drop=True)
out.to_csv("data/metadata/INPESONDA_normais_climatology.csv", sep=";", index=False)
print(out.to_string())
