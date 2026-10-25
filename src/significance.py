"""TK04 – testy istotności: czy wyniki pytań 1–3 mogą być przypadkiem.

- Pytanie 1 (sezony):   test U Manna–Whitneya na dniach i test permutacyjny na średnich miesięcznych.
- Pytanie 2 (pogoda):   istotność każdej korelacji z macierzy korelacji.
- Pytanie 3 (miasta):   test Friedmana (te same dni we wszystkich miastach).

Biblioteki scipy nie ma na liście zależności, więc rozkłady liczymy wzorami: normalny przez
math.erfc, chi-kwadrat wzorem dla całkowitej liczby stopni swobody, a test permutacyjny przez
losowanie (numpy). Kolejne dni są do siebie podobne (smog trwa kilka dni), więc testy z danych
dziennych powtarzamy na średnich miesięcznych.

Wyniki: tabele wyniki_testy i wyniki_autokorelacja.
"""
import math
import sqlite3

import numpy as np
import pandas as pd

from src import config

ALPHA = 0.05  # poziom istotności


# --- Rozkłady i testy ----------------------------------------------------------------------------

def p_normal(z: float) -> float:
    """Dwustronna p-wartość dla statystyki z o rozkładzie normalnym N(0, 1)."""
    return math.erfc(abs(z) / math.sqrt(2))


def chi2_sf(x: float, df: int) -> float:
    """P(χ²(df) > x) dla całkowitego df – wzory zamknięte (parzyste df: szereg Poissona; nieparzyste: erfc + szereg)."""
    h = x / 2
    if df % 2 == 0:
        return math.exp(-h) * sum(h ** i / math.factorial(i) for i in range(df // 2))
    return math.erfc(math.sqrt(h)) + math.exp(-h) * sum(h ** (i - 0.5) / math.gamma(i + 0.5)
                                                       for i in range(1, (df - 1) // 2 + 1))


def spearman_p(r: float, n: int) -> float:
    """p-wartość dla H0: brak korelacji; t = r·√((n−2)/(1−r²)), przy n w setkach t ≈ N(0, 1)."""
    return 0.0 if abs(r) >= 1 else p_normal(r * math.sqrt((n - 2) / (1 - r * r)))


def mann_whitney(a: pd.Series, b: pd.Series) -> dict:
    """Test U Manna–Whitneya (przybliżenie normalne, poprawka na równe wartości).
    Efekt: P(losowa wartość z a > z b) = U / (n_a·n_b); 0,5 = brak różnicy."""
    x = pd.concat([a, b], ignore_index=True)
    n1, n2, n = len(a), len(b), len(a) + len(b)
    u = x.rank().iloc[:n1].sum() - n1 * (n1 + 1) / 2
    ties = x.value_counts().values
    sd = math.sqrt(n1 * n2 / 12 * ((n + 1) - (ties ** 3 - ties).sum() / (n * (n - 1))))
    z = (u - n1 * n2 / 2) / sd
    return {"z": z, "p": p_normal(z), "efekt": u / (n1 * n2)}


def permutation_test(x: np.ndarray, group: np.ndarray, n_perm: int = 20000) -> dict:
    """Test permutacyjny różnicy średnich dwóch grup – bez założeń o rozkładzie."""
    rng = np.random.default_rng(42)

    def mean_difference(g: np.ndarray) -> float:
        return x[g].mean() - x[~g].mean()

    observed = mean_difference(group)
    shuffled = np.array([mean_difference(rng.permutation(group)) for _ in range(n_perm)])
    return {"roznica": observed, "p": ((np.abs(shuffled) >= abs(observed)).sum() + 1) / (n_perm + 1)}


def friedman(table: pd.DataFrame) -> dict:
    """Test Friedmana: wiersze = bloki (dni lub miesiące), kolumny = grupy (miasta); poprawka na równe wartości.
    Efekt: W Kendalla (0 – kolejność miast losowa, 1 – w każdym bloku taka sama)."""
    n, k = table.shape
    ranks = table.rank(axis=1)
    q = 12 / (n * k * (k + 1)) * (ranks.sum() ** 2).sum() - 3 * n * (k + 1)
    ties = 0
    for _, row in table.iterrows():
        counts = row.value_counts().values
        ties += (counts ** 3 - counts).sum()
    q /= 1 - ties / (n * k * (k * k - 1))
    return {"q": q, "df": k - 1, "p": chi2_sf(q, k - 1), "efekt": q / (n * (k - 1)),
            "srednie_rangi": ranks.mean().sort_values(ascending=False)}


# --- Testy dla pytań badawczych ------------------------------------------------------------------

def pl(v: float, digits: int) -> str:
    """Liczba w polskim zapisie do opisów: przecinek dziesiętny i znak minus (−1,00)."""
    return f"{v:.{digits}f}".replace(".", ",").replace("-", "−")


def season_tests(days: pd.DataFrame, col: str, name: str) -> list[dict]:
    """Pytanie 1: czy zimą jest więcej pyłu niż latem (dni i średnie miesięczne)."""
    winter, summer = days.loc[days["sezon"] == "grzewczy", col], days.loc[days["sezon"] == "letni", col]
    mw = mann_whitney(winter, summer)
    monthly = days.groupby(["miesiac_roku", "sezon"])[col].mean().reset_index()
    is_winter = (monthly["sezon"] == "grzewczy").values
    perm = permutation_test(monthly[col].values, is_winter)
    return [
        {"pytanie": 1, "wskaznik": name, "test": "U Manna–Whitneya (dni)", "h0": "zimą i latem jest tyle samo pyłu",
         "dane": f"{len(winter)} + {len(summer)} dni-miasto", "statystyka": f"z = {pl(mw['z'], 2)}", "p": mw["p"],
         "efekt_nazwa": "P(dzień zimowy > letni)", "efekt": mw["efekt"]},
        {"pytanie": 1, "wskaznik": name, "test": "permutacyjny (średnie miesięczne)", "h0": "średnia zimą = średnia latem",
         "dane": f"{len(monthly)} miesięcy ({is_winter.sum()} grzewczych)",
         "statystyka": f"różnica {pl(perm['roznica'], 2)} µg/m³", "p": perm["p"],
         "efekt_nazwa": "różnica średnich [µg/m³]", "efekt": perm["roznica"]},
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


if __name__ == "__main__":
    run()
