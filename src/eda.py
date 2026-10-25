"""TK04 – eksploracja danych (EDA): jak wyglądają dane, zanim wybierzemy metody analizy.

Dla każdej zmiennej liczymy:
- miary położenia: średnia, mediana, dominanta (najczęstszy przedział histogramu),
- miary rozrzutu: odchylenie standardowe, wariancja, współczynnik zmienności,
- kształt rozkładu: skośność, kurtoza i słowna ocena,
- regułę 68–95–99,7: ile danych leży w ±1σ, ±2σ, ±3σ od średniej.

Wyniki: tabele wyniki_opisowe, wyniki_histogramy, wyniki_regula_3sigma i wykres rozklady.png.
"""
import sqlite3

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src import config
from src.plots import TEXT, save

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
            # Skośność po logarytmie log(1 + x) – czy przekształcenie usuwa skośność prawostronną
            "skosnosc_po_log": np.log1p(values).skew() if skew >= 1 and values.min() >= 0 else np.nan,
            "udzial_zer_pct": 100 * (values == 0).mean(),
            # Odchylenie w obrębie jednego miasta / stacji: ile rozrzutu to różnice między miejscami
            "odch_std_w_grupie": np.sqrt(values.groupby(groups).var().mean()),
            "q1": values.quantile(0.25), "q3": values.quantile(0.75), "min": values.min(), "max": values.max(),
        })
        hist += [{"zmienna": name, "od": edges[i], "do": edges[i + 1], "n": int(c)} for i, c in enumerate(counts)]
    return pd.DataFrame(rows), pd.DataFrame(hist)


def sigma_rule(data: dict[str, pd.DataFrame], stats: pd.DataFrame) -> pd.DataFrame:
    """Reguła 68–95–99,7: odsetek danych w ±1σ, ±2σ, ±3σ od średniej.
    Dla zmiennych mocno skośnych także po przekształceniu log(1 + x)."""
    rows = []
    for col, (name, *_) in VARIABLES.items():
        values = data[col][col]
        variants = [("bez zmian", values)]
        if not pd.isna(stats.loc[stats["zmienna"] == name, "skosnosc_po_log"].iloc[0]):
            variants.append(("log(1 + x)", np.log1p(values)))
        for transform, v in variants:
            distance = (v - v.mean()).abs() / v.std()  # odległość od średniej w odchyleniach standardowych
            rows.append({"zmienna": name, "przeksztalcenie": transform,
                         **{f"w_{k}_sigma_pct": 100 * (distance <= k).mean() for k in (1, 2, 3)},
                         "poza_3_sigma": int((distance > 3).sum()), "n": len(v)})
    return pd.DataFrame(rows)


def plot_distributions(stats: pd.DataFrame, hist: pd.DataFrame) -> None:
    """Histogramy wszystkich zmiennych: średnia (linia ciągła), mediana (przerywana), dominanta (ciemny słupek)."""
    fig, axes = plt.subplots(2, 4, figsize=(16, 7))
    fig.subplots_adjust(hspace=0.6, wspace=0.22, top=0.85, left=0.04, right=0.99)
    for ax, (_, st) in zip(axes.flat, stats.iterrows()):
        h = hist[hist["zmienna"] == st["zmienna"]]
        modal = h["od"] == st["dominanta_od"]
        ax.bar(h["od"], h["n"], width=h["do"] - h["od"], align="edge", color=np.where(modal, "#1c5cab", "#86b6ef"))
        ax.axvline(st["srednia"], color="#eb6834", lw=2, label="średnia")
        ax.axvline(st["mediana"], color=TEXT, lw=1.5, ls="--", label="mediana")
        if st["skosnosc"] >= 1:  # długi ogon: oś do 99,5 percentyla, żeby było widać kształt
            ax.set_xlim(h["od"].min(), np.interp(0.995, h["n"].cumsum() / h["n"].sum(), h["do"]))
        ax.set_title(f"{st['zmienna']} [{st['jednostka']}] · skośność {st['skosnosc']:.2f}\n{st['ksztalt']}", fontsize=9.5)
        ax.grid(axis="x", visible=False)
    axes.flat[-1].axis("off")
    axes.flat[0].legend(fontsize=8, frameon=False)
    fig.suptitle("Rozkłady zmiennych (EDA): pył – dni-miasto, pogoda – dni-stacja; ciemny słupek = przedział modalny",
                 x=0.01, ha="left", fontweight="bold", color=TEXT)
    save(fig, "rozklady.png")


def run() -> None:
    with sqlite3.connect(config.DB_PATH) as con:
        data = load(con)
        stats, hist = descriptive_stats(data)
        results = {
            "wyniki_opisowe": stats,
            "wyniki_histogramy": hist,
            "wyniki_regula_3sigma": sigma_rule(data, stats),
        }
        for name, df in results.items():
            df.to_sql(name, con, if_exists="replace", index=False)
            print(f"  {name}: {len(df)} wierszy")
    con.close()
    plot_distributions(stats, hist)


if __name__ == "__main__":
    run()
