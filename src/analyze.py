"""

1. Sezon grzewczy a letni: statystyki sezonowe i średnie miesięczne.
2. Pogoda a pył: korelacje Pearsona i Spearmana oraz pełna macierz korelacji.
3. Ranking miast; najnowsze odczyty z API GIOŚ na tle historii.
4. Klasy jakości dnia: ile dni dobrych, umiarkowanych i złych (zliczenia według progów PM10).

"""
import sqlite3

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src import config
from src.plots import CITY_COLORS, CLASS_COLORS, DIVERGING, GRID, SEASON_COLORS, SURFACE, TEXT, TEXT_MUTED, save
from src.significance import spearman_p

POLLUTANTS, WEATHER, SEASONS = config.POLLUTANTS, config.WEATHER, config.SEASONS
VARIABLES = {**POLLUTANTS, **WEATHER}  # zmienne macierzy korelacji
LIMIT = config.PM10_DAILY_LIMIT


def load(con: sqlite3.Connection) -> pd.DataFrame:
    """Dni do analizy (pył + pogoda + sezon + klasa)."""
    return pd.read_sql("SELECT * FROM dzien_miasto", con, parse_dates=["data"])


# --- sezon grzewczy a letni ----------------------------------------------------------

def seasonal_stats(days: pd.DataFrame) -> pd.DataFrame:
    """Statystyki PM10/PM2.5 dla każdego miasta i sezonu (+ wiersz „Wszystkie miasta”)."""
    rows = []
    groups = [(city, g) for city, g in days.groupby("miasto")] + [("Wszystkie miasta", days)]
    for city, g in groups:
        for col, name in POLLUTANTS.items():
            for season in SEASONS:
                v = g.loc[g["sezon"] == season, col].dropna()
                if v.empty:
                    continue
                rows.append({"miasto": city, "wskaznik": name, "sezon": season, "n_dni": len(v),
                             "srednia": v.mean(), "mediana": v.median(), "odch_std": v.std(),
                             "p90": v.quantile(0.9),
                             "dni_powyzej_50": int((v > LIMIT).sum()) if col == "pm10" else None})
    df = pd.DataFrame(rows)
    # Ile razy wyższe stężenie w sezonie grzewczym niż w letnim
    means = df.pivot_table(index=["miasto", "wskaznik"], columns="sezon", values="srednia")
    ratio = (means["grzewczy"] / means["letni"]).rename("iloraz_grzewczy_letni").reset_index()
    return df.merge(ratio, on=["miasto", "wskaznik"]).round(2)


def monthly_means(days: pd.DataFrame) -> pd.DataFrame:
    """Średnie miesięczne dla każdego miasta i dla wszystkich miast razem."""
    df = days.assign(miesiac=days["data"].dt.month)
    per_city = df.groupby(["miasto", "miesiac"])[list(POLLUTANTS)].mean().reset_index()
    together = df.groupby("miesiac")[list(POLLUTANTS)].mean().reset_index().assign(miasto="Wszystkie miasta")
    return pd.concat([per_city, together[per_city.columns]], ignore_index=True).round(2)


# --- Pytanie 2: pogoda a pył --------------------------------------------------------------------

def scopes(days: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Zakresy, dla których liczymy korelacje: cały rok i każdy sezon osobno."""
    return {"cały rok": days, **{season: days[days["sezon"] == season] for season in SEASONS}}


def correlations(days: pd.DataFrame) -> pd.DataFrame:
    """Korelacje Pearsona i Spearmana pyłu z każdą zmienną pogodową (dane z wszystkich miast razem)."""
    rows = []
    for scope, g in scopes(days).items():
        for method in ["pearson", "spearman"]:
            for col, name in POLLUTANTS.items():
                for wcol, wname in WEATHER.items():
                    pair = g[[col, wcol]].dropna()
                    rows.append({"zakres": scope, "metoda": method, "wskaznik": name, "zmienna": wname,
                                 "r": pair[col].corr(pair[wcol], method=method), "n": len(pair)})
    return pd.DataFrame(rows).round(3)


def correlation_matrix(days: pd.DataFrame) -> pd.DataFrame:
    """Macierz korelacji Spearmana wszystkich par zmiennych (pył i pogoda), z p-wartością każdej pary."""
    rows = []
    for scope, g in scopes(days).items():
        g = g[list(VARIABLES)].dropna().rename(columns=VARIABLES)  # te same dni dla każdej pary
        m = g.corr(method="spearman").rename_axis(index="zmienna_a", columns="zmienna_b")
        rows.append(m.stack().rename("r").reset_index().assign(zakres=scope, n=len(g)))
    # Bez zaokrąglania: strona i wykres zaokrąglają raz do 2 miejsc (−0,3047 → −0,30, a nie −0,305 → −0,31)
    out = pd.concat(rows, ignore_index=True)[["zakres", "zmienna_a", "zmienna_b", "r", "n"]]
    out["p"] = [spearman_p(r, n) for r, n in zip(out["r"], out["n"])]
    return out


# --- Pytanie 3: ranking miast -------------------------------------------------------------------

def city_ranking(days: pd.DataFrame) -> pd.DataFrame:
    """Miasta od najwyższego średniego PM10."""
    years = days["data"].dt.year.nunique()
    r = days.groupby("miasto").agg(
        pm10_srednia=("pm10", "mean"), pm10_mediana=("pm10", "median"), pm25_srednia=("pm25", "mean"),
        dni_pm10=("pm10", "count"),
        dni_powyzej_50=("pm10", lambda s: int((s > LIMIT).sum())),
        dni_zla_pct=("klasa", lambda s: 100 * (s == config.PM10_CLASS_LABELS[2]).sum() / s.notna().sum()),
    )
    r["dni_powyzej_50_rocznie"] = r["dni_powyzej_50"] / years
    r = r.sort_values("pm10_srednia", ascending=False)
    r.insert(0, "pozycja", range(1, len(r) + 1))
    return r.reset_index().round(2)


def current_vs_history(days: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """Najnowsze odczyty z API GIOŚ na tle historii: średnia z ostatnich 24 h w każdym mieście
    i jaki odsetek dni tego samego miesiąca w latach 2023–2025 miał niższą średnią dobową."""
    rows = []
    for city, cur in current.groupby("miasto"):
        end = cur["czas"].max()
        window = cur[cur["czas"] > end - pd.Timedelta(hours=24)]
        month = end.month
        history = days[(days["miasto"] == city) & (days["data"].dt.month == month)]
        for col, name in POLLUTANTS.items():
            values = window.loc[window["wskaznik"] == name, "wartosc"].dropna()
            if len(values) < config.MIN_HOURS_PER_DAY:  # ten sam próg co dla średniej dobowej
                continue
            mean, past = values.mean(), history[col].dropna()
            rows.append({
                "miasto": city, "wskaznik": col, "od": window["czas"].min().strftime("%Y-%m-%d %H:%M"),
                "do": end.strftime("%Y-%m-%d %H:%M"), "godzin": len(values), "srednia_24h": mean,
                "miesiac": month, "dni_historia": len(past), "mediana_historia": past.median(),
                "nizej_pct": 100 * (past < mean).mean(),  # % dni z historii z niższą średnią
                # Klasa dnia jest określona tylko dla PM10 (progi jak w widoku dzien_miasto)
                "klasa": (config.PM10_CLASS_LABELS[np.searchsorted(config.PM10_CLASS_BINS, mean)]
                          if col == "pm10" else None),
            })
    return pd.DataFrame(rows, columns=["miasto", "wskaznik", "od", "do", "godzin", "srednia_24h", "miesiac",
                                       "dni_historia", "mediana_historia", "nizej_pct", "klasa"])


# --- Pytanie 4: klasy jakości dnia (zliczenia według progów PM10) --------------------------------

def class_counts(days: pd.DataFrame) -> pd.DataFrame:
    """Ile dni dobrych, umiarkowanych i złych: wszystkie dni razem, każde miasto, sezon i miesiąc."""
    d = days.dropna(subset=["klasa"]).assign(miesiac=days["data"].dt.month, wszystkie="wszystkie dni")
    rows = []
    for split in ["wszystkie", "miasto", "sezon", "miesiac"]:
        table = pd.crosstab(d[split], d["klasa"]).reindex(columns=config.PM10_CLASS_LABELS, fill_value=0)
        for group, counts in table.iterrows():
            for label, n in counts.items():
                rows.append({"podzial": split, "grupa": str(group), "klasa": label,
                             "dni": int(n), "udzial_pct": 100 * n / counts.sum()})
    return pd.DataFrame(rows)


# --- Wykresy -------------------------------------------------------------------------------------

def plot_seasonal(seasonal: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    cities = list(config.CITIES)
    for ax, name in zip(axes, POLLUTANTS.values()):
        sub = seasonal[(seasonal["wskaznik"] == name) & seasonal["miasto"].isin(cities)]
        x = np.arange(len(cities))
        for i, season in enumerate(SEASONS):
            vals = sub[sub["sezon"] == season].set_index("miasto")["srednia"].reindex(cities)
            bars = ax.bar(x + (i - 0.5) * 0.38, vals, width=0.36, color=SEASON_COLORS[season],
                          label=f"sezon {season}", edgecolor=SURFACE, linewidth=2)
            ax.bar_label(bars, fmt="%.0f", padding=2, color=TEXT, fontsize=9)
        ax.set_xticks(x, cities)
        ax.set_title(f"Średnie dobowe {name} wg sezonu")
        ax.grid(axis="x", visible=False)
        for i, c in enumerate(cities):
            if sub[sub["miasto"] == c].empty:
                ax.text(i, 1, "brak\npomiaru", ha="center", va="bottom", color=TEXT_MUTED, fontsize=8)
    axes[0].set_ylabel("µg/m³")
    axes[0].set_ylim(top=seasonal.loc[seasonal["miasto"].isin(cities), "srednia"].max() * 1.12)
    fig.legend(*axes[0].get_legend_handles_labels(), frameon=False, ncol=2,
               loc="lower center", bbox_to_anchor=(0.5, -0.06))
    save(fig, "sezonowe.png")


def plot_monthly(monthly: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.axvspan(0.5, 3.5, color="#fde3d6", zorder=0, lw=0)
    ax.axvspan(9.5, 12.5, color="#fde3d6", zorder=0, lw=0)
    ax.text(2, 1, "sezon grzewczy", ha="center", color=TEXT_MUTED, fontsize=8)
    ax.text(11, 1, "sezon grzewczy", ha="center", color=TEXT_MUTED, fontsize=8)
    ends = {}
    for city, color in CITY_COLORS.items():
        s = monthly[monthly["miasto"] == city].set_index("miesiac")["pm10"]
        ax.plot(s.index, s.values, color=color, lw=2, marker="o", ms=4, label=city)
        ends[city] = s.loc[12]
    # Etykiety na końcu linii rozsunięte w pionie, żeby się nie nakładały
    min_gap, prev = 1.3, -np.inf
    for city, y in sorted(ends.items(), key=lambda kv: kv[1]):
        y_label = max(y, prev + min_gap)
        ax.annotate(city, (12, y), xytext=(12.25, y_label), textcoords="data", va="center", color=TEXT, fontsize=9)
        prev = y_label
    ax.set_xticks(range(1, 13), ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"])
    ax.set_xlim(0.5, 13.3)
    ax.set_ylim(bottom=0)
    ax.set_ylabel("µg/m³")
    ax.set_title("Średnie miesięczne PM10 (2023–2025)")
    ax.legend(frameon=False, ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.1))
    save(fig, "miesieczne_pm10.png")


def plot_correlations(matrix: pd.DataFrame) -> None:
    names = list(VARIABLES.values())
    short = ["PM10", "PM2.5", "temp.", "wiatr", "opad", "wilg.", "ciśn."]
    cmap = DIVERGING.copy()
    cmap.set_bad(GRID)  # przekątna (zawsze 1) na szaro, żeby nie odciągała uwagi
    diagonal = np.eye(len(names), dtype=bool)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), sharey=True)
    fig.subplots_adjust(top=0.87, bottom=0.07, wspace=0.06, left=0.09, right=0.93)
    for ax, scope in zip(axes, ["cały rok", "grzewczy", "letni"]):  # kolejność jak w przełączniku na stronie
        sub = matrix[matrix["zakres"] == scope]
        m = sub.pivot(index="zmienna_a", columns="zmienna_b", values="r").reindex(index=names, columns=names)
        image = ax.imshow(np.ma.masked_where(diagonal, m.values), cmap=cmap, vmin=-1, vmax=1)
        for (i, j), v in np.ndenumerate(m.values):
            ax.text(j, i, "1" if i == j else f"{v:+.2f}", ha="center", va="center", fontsize=8.5,
                    color=TEXT_MUTED if i == j else "white" if abs(v) > 0.55 else TEXT)
        # Linie oddzielają blok pyłu (PM10, PM2.5) od zmiennych pogodowych
        ax.axhline(len(POLLUTANTS) - 0.5, color=SURFACE, lw=3)
        ax.axvline(len(POLLUTANTS) - 0.5, color=SURFACE, lw=3)
        ax.set_xticks(range(len(names)), short)
        ax.set_yticks(range(len(names)), names)
        ax.set_title(f"{scope} (n = {sub['n'].iloc[0]} dni-miasto)")
        ax.grid(False)
        for s in ax.spines.values():
            s.set_visible(False)
    fig.colorbar(image, ax=axes, shrink=0.75, pad=0.015, label="r Spearmana")
    fig.suptitle(f"Macierz korelacji Spearmana: pył i pogoda (dane dobowe, {len(config.CITIES)} miasta)",
                 x=0.01, y=0.98, ha="left", fontweight="bold", color=TEXT)
    save(fig, "korelacje.png")


def plot_scatter(days: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    for season in SEASONS:
        g = days[days["sezon"] == season].dropna(subset=["pm10", "temp_sr"])
        ax.scatter(g["temp_sr"], g["pm10"], s=10, alpha=0.35, color=SEASON_COLORS[season],
                   label=f"sezon {season}", linewidths=0)
    ax.axhline(LIMIT, color=TEXT_MUTED, lw=1, ls="--")
    ax.text(ax.get_xlim()[1], LIMIT, " norma dobowa 50", va="bottom", ha="right", color=TEXT_MUTED, fontsize=8)
    ax.set_xlabel("średnia dobowa temperatura [°C]")
    ax.set_ylabel("PM10 [µg/m³]")
    ax.set_title(f"PM10 a temperatura (dni × {len(config.CITIES)} miasta)")
    ax.legend(frameon=False, markerscale=2)
    save(fig, "rozrzut_pm10_temp.png")


def plot_ranking(ranking: pd.DataFrame) -> None:
    r = ranking.sort_values("pm10_srednia")
    fig, ax = plt.subplots(figsize=(8, 3.5))
    bars = ax.barh(r["miasto"], r["pm10_srednia"], color=[CITY_COLORS[c] for c in r["miasto"]],
                   height=0.6, edgecolor=SURFACE, linewidth=2)
    ax.bar_label(bars, labels=[f"{v:.1f} µg/m³  ·  {d:.0f} dni >50/rok"
                               for v, d in zip(r["pm10_srednia"], r["dni_powyzej_50_rocznie"])],
                 padding=4, color=TEXT, fontsize=9)
    ax.set_xlim(0, r["pm10_srednia"].max() * 1.6)
    ax.set_xlabel("średnie dobowe PM10 2023–2025 [µg/m³]")
    ax.set_title("Ranking miast wg średniego PM10")
    ax.grid(axis="y", visible=False)
    save(fig, "ranking_pm10.png")


def plot_classes(classes: pd.DataFrame) -> None:
    """Udział klas dni w każdym mieście i w każdym miesiącu (słupki do 100%)."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.2), gridspec_kw={"width_ratios": [1, 1.6]})
    for ax, split, labels in [(axes[0], "miasto", list(config.CITIES)),
                              (axes[1], "miesiac", [str(m) for m in range(1, 13)])]:
        shares = (classes[classes["podzial"] == split]
                  .pivot(index="grupa", columns="klasa", values="udzial_pct").reindex(labels))
        bottom = np.zeros(len(labels))
        for label in config.PM10_CLASS_LABELS:
            ax.bar(range(len(labels)), shares[label], bottom=bottom, color=CLASS_COLORS[label], label=label,
                   width=0.7, edgecolor=SURFACE, linewidth=1)
            bottom += shares[label].values
        ax.set_xticks(range(len(labels)), labels if split == "miasto" else
                      ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"])
        ax.set_ylim(0, 100)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("% dni")
    axes[0].set_title("Klasy dni w miastach")
    axes[1].set_title("Klasy dni w miesiącach (4 miasta razem)")
    fig.legend(*axes[0].get_legend_handles_labels(), frameon=False, ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.06))
    save(fig, "klasy_dni.png")


# --- Cały etap -----------------------------------------------------------------------------------

def run() -> None:
    with sqlite3.connect(config.DB_PATH) as con:
        days = load(con)
        results = {
            "wyniki_sezonowe": seasonal_stats(days),                 # pytanie 1
            "wyniki_miesieczne": monthly_means(days),
            "wyniki_korelacje": correlations(days),                  # pytanie 2
            "wyniki_macierz_korelacji": correlation_matrix(days),
            "wyniki_ranking": city_ranking(days),                    # pytanie 3
            "wyniki_klasy": class_counts(days),                      # pytanie 4
        }
        current = pd.read_sql("SELECT b.czas, s.miasto, b.wskaznik, b.wartosc FROM pm_biezace b "
                              "JOIN stacje s USING (kod_stacji)", con, parse_dates=["czas"])
        results["wyniki_biezace"] = current_vs_history(days, current)  # odczyty z API na tle historii
        for name, df in results.items():
            df.to_sql(name, con, if_exists="replace", index=False)
            print(f"  {name}: {len(df)} wierszy")
    con.close()
    plot_seasonal(results["wyniki_sezonowe"])
    plot_monthly(results["wyniki_miesieczne"])
    plot_correlations(results["wyniki_macierz_korelacji"])
    plot_scatter(days)
    plot_ranking(results["wyniki_ranking"])
    plot_classes(results["wyniki_klasy"])
    print(f"  wykresy PNG: {config.DATA_PROCESSED.relative_to(config.ROOT)}")


if __name__ == "__main__":
    run()
