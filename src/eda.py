import sqlite3

import numpy as np
import pandas as pd

from src import config
from src.plots import plot_distributions

# Zmienna: (nazwa, jednostka, szerokość przedziału histogramu, czy współczynnik zmienności ma sens).
# Temperatura w °C nie ma prawdziwego zera, więc stosunek odchylenia do średniej nic dla niej nie znaczy.
VARIABLES = {
    "pm10": ("PM10", "µg/m³", 2, True), "pm25": ("PM2.5", "µg/m³", 2, True),
    "temp_sr": ("temperatura", "°C", 1, False), "wiatr_sr": ("prędkość wiatru", "m/s", 0.5, True),
    "opad": ("opad", "mm", 1, True), "wilgotnosc": ("wilgotność", "%", 2, True), "cisnienie": ("ciśnienie", "hPa", 2, True),
}


def load(con: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    """Wartości każdej zmiennej i grupa (miasto albo stacja IMGW).

    Pył: dni-miasto. Pogoda: dni na każdej stacji IMGW (każde miasto ma swoją).
    """
    pm = pd.read_sql("SELECT miasto AS grupa, pm10, pm25 FROM dzien_miasto", con)
    weather = pd.read_sql("SELECT imgw_kod AS grupa, temp_sr, wiatr_sr, opad, wilgotnosc, cisnienie "
                          "FROM pogoda_dobowa JOIN dni_kompletne USING (data)", con)
    return {col: (pm if col in config.POLLUTANTS else weather)[["grupa", col]].dropna() for col in VARIABLES}


def distribution_shape(skew: float, kurt: float) -> str:
    """Słowna ocena kształtu ze skośności i kurtozy (rozkład normalny ma obie równe 0)."""
    side = "prawostronnie" if skew > 0 else "lewostronnie"
    if abs(skew) >= 1:
        return f"silnie skośny {side}" + (", ciężki ogon" if kurt > 3 else "")
    if abs(skew) >= 0.5:
        return f"umiarkowanie skośny {side}"
    if kurt < -0.5:
        return "symetryczny, spłaszczony"
    if kurt > 1:
        return "symetryczny, ciężkie ogony"
    return "zbliżony do normalnego"


def descriptive_stats(data: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Statystyki opisowe i histogram każdej zmiennej."""
    rows, hist = [], []
    for col, (name, unit, width, ratio_scale) in VARIABLES.items():
        values, groups = data[col][col], data[col]["grupa"]
        edges = np.round(np.arange(np.floor(values.min() / width) * width, values.max() + width, width), 6)
        counts, edges = np.histogram(values, bins=edges)
        modal = counts.argmax()  # dominanta danych ciągłych = najczęstszy przedział
        skew, kurt = values.skew(), values.kurt()
        rows.append({
            "zmienna": name, "jednostka": unit, "n": len(values),
            "srednia": values.mean(), "mediana": values.median(),
            "dominanta_od": edges[modal], "dominanta_do": edges[modal + 1],
            "odch_std": values.std(), "wariancja": values.var(),
            "wsp_zmiennosci_pct": 100 * values.std() / values.mean() if ratio_scale else np.nan,
            "skosnosc": skew, "kurtoza": kurt, "ksztalt": distribution_shape(skew, kurt),
            "udzial_zer_pct": 100 * (values == 0).mean(),
            # Odchylenie w obrębie jednego miasta / stacji: ile rozrzutu to różnice między miejscami
            "odch_std_w_grupie": np.sqrt(values.groupby(groups).var().mean()),
            "q1": values.quantile(0.25), "q3": values.quantile(0.75), "min": values.min(), "max": values.max(),
        })
        hist += [{"zmienna": name, "od": edges[i], "do": edges[i + 1], "n": int(c)} for i, c in enumerate(counts)]
    return pd.DataFrame(rows), pd.DataFrame(hist)


def sigma_rule(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Reguła 68–95–99,7: odsetek danych w ±1σ, ±2σ, ±3σ od średniej.
    Dla pyłu także po przekształceniu log(1 + x) – po nim rozkład jest prawie normalny."""
    rows = []
    for col, (name, *_) in VARIABLES.items():
        values = data[col][col]
        variants = [("bez zmian", values)]
        if col in config.POLLUTANTS:
            variants.append(("log(1 + x)", np.log1p(values)))
        for transform, v in variants:
            distance = (v - v.mean()).abs() / v.std()
            rows.append({"zmienna": name, "przeksztalcenie": transform,
                         **{f"w_{k}_sigma_pct": 100 * (distance <= k).mean() for k in (1, 2, 3)},
                         "poza_3_sigma": int((distance > 3).sum()), "n": len(v)})
    return pd.DataFrame(rows)


def run() -> None:
    with sqlite3.connect(config.DB_PATH) as con:
        data = load(con)
        stats, hist = descriptive_stats(data)
        results = {
            "wyniki_opisowe": stats,
            "wyniki_histogramy": hist,
            "wyniki_regula_3sigma": sigma_rule(data),
        }
        for name, df in results.items():
            df.to_sql(name, con, if_exists="replace", index=False)
            print(f"  {name}: {len(df)} wierszy")
    con.close()
    plot_distributions(stats, hist)
