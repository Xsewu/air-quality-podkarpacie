"""

1. Sezon grzewczy a letni: statystyki sezonowe i średnie miesięczne.
2. Pogoda a pył: korelacje Pearsona i Spearmana oraz pełna macierz korelacji.
3. Ranking miast; najnowsze odczyty z API GIOŚ na tle historii.
4. Klasy jakości dnia: ile dni dobrych, umiarkowanych i złych (zliczenia według progów PM10).

"""
import sqlite3

import numpy as np
import pandas as pd

from src import config
from src.plots import plot_classes, plot_correlations, plot_monthly, plot_ranking, plot_scatter, plot_seasonal
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
