import math
import sqlite3

import numpy as np
import pandas as pd
from scipy import stats

from src import config

ALPHA = 0.05


# --- Testy ---------------------------------------------------------------------------------------

def mann_whitney(a: pd.Series, b: pd.Series) -> dict:
    """Test U Manna–Whitneya (przybliżenie normalne). Efekt: P(losowa wartość z a > z b) = U / (n_a·n_b)."""
    res = stats.mannwhitneyu(a, b, alternative="two-sided", method="asymptotic", use_continuity=False)
    z = math.copysign(stats.norm.isf(res.pvalue / 2), res.statistic - len(a) * len(b) / 2)
    return {"z": z, "p": res.pvalue, "efekt": res.statistic / (len(a) * len(b))}


def friedman(table: pd.DataFrame) -> dict:
    """Test Friedmana: wiersze = bloki (dni lub miesiące), kolumny = grupy (miasta).
    Efekt: W Kendalla (0 – kolejność miast losowa, 1 – w każdym bloku taka sama)."""
    n, k = table.shape
    res = stats.friedmanchisquare(*(table[c] for c in table.columns))
    return {"q": res.statistic, "df": k - 1, "p": res.pvalue, "efekt": res.statistic / (n * (k - 1)),
            "srednie_rangi": table.rank(axis=1).mean().sort_values(ascending=False)}


# --- Testy dla pytań badawczych ------------------------------------------------------------------

def pl(v: float, digits: int) -> str:
    """Liczba w polskim zapisie do opisów: przecinek dziesiętny i znak minus (−1,00)."""
    return f"{v:.{digits}f}".replace(".", ",").replace("-", "−")


def season_tests(days: pd.DataFrame, col: str, name: str) -> list[dict]:
    """Pytanie 1: czy zimą jest więcej pyłu niż latem."""
    winter, summer = days.loc[days["sezon"] == "grzewczy", col], days.loc[days["sezon"] == "letni", col]
    mw = mann_whitney(winter, summer)
    return [
        {"pytanie": 1, "wskaznik": name, "test": "U Manna–Whitneya (dni)", "h0": "zimą i latem jest tyle samo pyłu",
         "dane": f"{len(winter)} + {len(summer)} dni-miasto", "statystyka": f"z = {pl(mw['z'], 2)}", "p": mw["p"],
         "efekt_nazwa": "P(dzień zimowy > letni)", "efekt": mw["efekt"]},
    ]


def city_tests(days: pd.DataFrame, col: str, name: str) -> list[dict]:
    """Pytanie 3: czy miasta się różnią – te same dni w każdym mieście, więc test Friedmana."""
    rows = []
    for unit, table in [("dni", days.pivot(index="data", columns="miasto", values=col)),
                        ("średnie miesięczne", days.groupby(["miesiac_roku", "miasto"])[col].mean().unstack())]:
        t = friedman(table)
        rows.append({"pytanie": 3, "wskaznik": name, "test": f"Friedmana ({unit})",
                     "h0": "wszystkie miasta mają takie same stężenia",
                     "dane": f"{len(table)} {'dni' if unit == 'dni' else 'miesięcy'} × {table.shape[1]} miasta",
                     "statystyka": f"Q = {pl(t['q'], 1)}, df = {t['df']}", "p": t["p"],
                     "efekt_nazwa": "W Kendalla", "efekt": t["efekt"],
                     "uwagi": "kolejność: " + " > ".join(t["srednie_rangi"].index)})
    return rows


def correlation_tests(matrix: pd.DataFrame) -> list[dict]:
    """Pytanie 2: ile korelacji z macierzy jest istotnych (przy dużym n istotne są już bardzo słabe związki)."""
    rows = []
    for scope, g in matrix.groupby("zakres", sort=False):
        pairs = g[g["zmienna_a"] < g["zmienna_b"]]  # każda para raz
        n = int(g["n"].iloc[0])
        not_significant = pairs[pairs["p"] >= ALPHA]
        if len(not_significant):
            notes = "nieistotne: " + "; ".join(f"{a} – {b} (r = {pl(r, 3)})" for a, b, r in
                                               not_significant[["zmienna_a", "zmienna_b", "r"]].values)
        else:
            notes = f"wszystkie pary istotne ({len(pairs)})"
        rows.append({"pytanie": 2, "wskaznik": None, "test": "korelacja Spearmana",
                     "h0": "brak związku w parze zmiennych", "dane": f"{scope}: n = {n}",
                     "statystyka": f"|r| krytyczne = {pl(1.96 / math.sqrt(n - 2 + 1.96 ** 2), 3)}", "p": None,
                     "efekt_nazwa": "istotne pary", "efekt": len(pairs) - len(not_significant), "uwagi": notes})
    return rows


def significance_tests(days: pd.DataFrame, matrix: pd.DataFrame) -> pd.DataFrame:
    """Wszystkie testy w jednej tabeli z wnioskiem dla każdego."""
    days = days.assign(miesiac_roku=days["data"].dt.strftime("%Y-%m"))
    rows = []
    for col, name in config.POLLUTANTS.items():
        rows += season_tests(days, col, name)
        rows += city_tests(days, col, name)
    rows += correlation_tests(matrix)
    out = pd.DataFrame(rows)
    out["wniosek"] = np.where(out["p"].isna(), "zob. uwagi",
                              np.where(out["p"] < ALPHA, "odrzucamy H0", "brak podstaw do odrzucenia H0"))
    return out


def autocorrelation(days: pd.DataFrame) -> pd.DataFrame:
    """Korelacja dnia z następnym dniem – miara tego, jak bardzo kolejne dni nie są niezależne."""
    rows = []
    for city, g in days.groupby("miasto"):
        g = g.set_index("data").asfreq("D")  # dni usunięte przy filtracji zostają brakami, nie skracają odstępów
        for col, name in config.POLLUTANTS.items():
            rows.append({"miasto": city, "wskaznik": name, "r_lag1": g[col].autocorr(1)})
    return pd.DataFrame(rows)


def run() -> None:
    with sqlite3.connect(config.DB_PATH) as con:
        days = pd.read_sql("SELECT * FROM dzien_miasto", con, parse_dates=["data"])
        matrix = pd.read_sql("SELECT * FROM wyniki_macierz_korelacji", con)
        results = {
            "wyniki_testy": significance_tests(days, matrix),
            "wyniki_autokorelacja": autocorrelation(days),
        }
        for name, df in results.items():
            df.to_sql(name, con, if_exists="replace", index=False)
            print(f"  {name}: {len(df)} wierszy")
    con.close()
