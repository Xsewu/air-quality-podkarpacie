"""TK04 – analiza: statystyki opisowe i rozkłady (EDA), statystyki sezonowe, korelacje z pogodą, ranking miast, wykresy.

Źródłem jest widok dzien_miasto (stacja główna miasta + pogoda) oraz tabela
gus_powiaty. Wyniki trafiają do tabel wyniki_* w SQLite i do wykresów PNG
w data/processed/.
"""
import itertools
import math
import sqlite3

import matplotlib

matplotlib.use("Agg")  # renderowanie do plików, bez okna
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

from src import config

POLLUTANTS = {"pm10": "PM10", "pm25": "PM2.5"}
WEATHER = {"temp_sr": "temperatura", "wiatr_sr": "prędkość wiatru", "opad": "opad",
           "wilgotnosc": "wilgotność", "cisnienie": "ciśnienie"}
VARIABLES = {**POLLUTANTS, **WEATHER}  # zmienne macierzy korelacji
SEASONS = ["letni", "grzewczy"]
LIMIT_PM10 = config.PM10_CLASS_BINS[1]  # 50 µg/m3 – dobowa wartość dopuszczalna

# Kolory: stałe przypisanie do encji (paleta zweryfikowana pod kątem daltonizmu)
CITY_COLORS = dict(zip(config.CITIES, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]))
SEASON_COLORS = {"letni": "#2a78d6", "grzewczy": "#eb6834"}
TEXT, TEXT_MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
DIVERGING = LinearSegmentedColormap.from_list("rozbiezna", ["#1c5cab", "#86b6ef", "#f0efec", "#ef8f8e", "#b52f2f"])

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": TEXT_MUTED, "axes.titlecolor": TEXT,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "xtick.color": TEXT_MUTED, "ytick.color": TEXT_MUTED, "font.size": 10,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
})


def load(con: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame]:
    days = pd.read_sql("SELECT * FROM dzien_miasto", con, parse_dates=["data"])
    gus = pd.read_sql("SELECT * FROM gus_powiaty", con)
    return days, gus


# --- Eksploracja (EDA): statystyki opisowe i kształt rozkładów -------------------

# Zmienna: (etykieta, jednostka, szerokość przedziału histogramu, skala ilorazowa – czy wsp. zmienności ma sens).
# Temperatura w °C nie ma prawdziwego zera, więc współczynnik zmienności (std / średnia) nic dla niej nie znaczy.
EDA_VARIABLES = {
    "pm10": ("PM10", "µg/m³", 2, True), "pm25": ("PM2.5", "µg/m³", 2, True),
    "temp_sr": ("temperatura", "°C", 1, False), "wiatr_sr": ("prędkość wiatru", "m/s", 0.5, True),
    "opad": ("opad", "mm", 1, True), "wilgotnosc": ("wilgotność", "%", 2, True), "cisnienie": ("ciśnienie", "hPa", 2, True),
}


def load_eda(con: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    """Obserwacje zmiennych: pył – dni-miasto, pogoda – unikalne dni-stacja IMGW (Krosno i Jasło mają wspólną
    stację, więc ich pogoda nie jest liczona podwójnie). Kolumna 'grupa' = miasto / stacja."""
    pm = pd.read_sql("SELECT miasto AS grupa, pm10, pm25 FROM dzien_miasto", con)
    w = pd.read_sql("SELECT imgw_kod AS grupa, temp_sr, wiatr_sr, opad, wilgotnosc, cisnienie "
                    "FROM pogoda_dobowa JOIN dni_kompletne USING (data)", con)
    return {c: (pm if c in POLLUTANTS else w)[["grupa", c]].dropna() for c in EDA_VARIABLES}


def distribution_shape(skew: float, kurt: float) -> str:
    """Ocena kształtu ze skośności i kurtozy nadwyżkowej (rozkład normalny: 0 i 0)."""
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
    """Miary położenia (średnia, mediana, dominanta), rozproszenia (wariancja, odchylenie, wsp. zmienności)
    i kształtu (skośność, kurtoza) oraz histogramy. Dominanta danych ciągłych = przedział modalny histogramu."""
    rows, hist = [], []
    for col, (name, unit, width, ratio) in EDA_VARIABLES.items():
        s, groups = data[col][col], data[col]["grupa"]
        edges = np.round(np.arange(np.floor(s.min() / width) * width, s.max() + width, width), 6)
        counts, edges = np.histogram(s, bins=edges)
        m = counts.argmax()
        skew, kurt = s.skew(), s.kurt()
        rows.append({
            "zmienna": name, "jednostka": unit, "n": len(s), "srednia": s.mean(), "mediana": s.median(),
            "dominanta_od": edges[m], "dominanta_do": edges[m + 1], "odch_std": s.std(), "wariancja": s.var(),
            "wsp_zmiennosci_pct": 100 * s.std() / s.mean() if ratio else np.nan,
            "skosnosc": skew, "kurtoza": kurt, "ksztalt": distribution_shape(skew, kurt),
            # Po logarytmie (log(1 + x)) – sprawdzenie, czy transformacja usuwa skośność prawostronną
            "skosnosc_po_log": np.log1p(s).skew() if skew >= 1 and s.min() >= 0 else np.nan,
            "udzial_zer_pct": 100 * (s == 0).mean(),
            # Odchylenie w obrębie jednego miasta / stacji: ile rozproszenia to różnice między miejscami
            "odch_std_w_grupie": np.sqrt(s.groupby(groups).var().mean()),
            "q1": s.quantile(0.25), "q3": s.quantile(0.75), "min": s.min(), "max": s.max(),
        })
        hist += [{"zmienna": name, "od": edges[i], "do": edges[i + 1], "n": int(c)} for i, c in enumerate(counts)]
    return pd.DataFrame(rows), pd.DataFrame(hist)


def sigma_rule(data: dict[str, pd.DataFrame], stats: pd.DataFrame) -> pd.DataFrame:
    """Reguła 68–95–99,7: jaki odsetek obserwacji leży w ±1σ, ±2σ, ±3σ od średniej (rozkład normalny: 68,3 / 95,4 / 99,7).
    Dla zmiennych silnie skośnych także po przekształceniu log(1 + x)."""
    rows = []
    for col, (name, *_rest) in EDA_VARIABLES.items():
        s = data[col][col]
        warianty = [("bez zmian", s)]
        if not pd.isna(stats.loc[stats["zmienna"] == name, "skosnosc_po_log"].iloc[0]):
            warianty.append(("log(1 + x)", np.log1p(s)))
        for przeksztalcenie, v in warianty:
            odl = (v - v.mean()).abs() / v.std()
            rows.append({"zmienna": name, "przeksztalcenie": przeksztalcenie,
                         **{f"w_{k}_sigma_pct": 100 * (odl <= k).mean() for k in (1, 2, 3)},
                         "poza_3_sigma": int((odl > 3).sum()), "n": len(v)})
    return pd.DataFrame(rows)


# --- Testy istotności (bez scipy: rozkład normalny przez math.erfc, chi-kwadrat wzorem dla całkowitych df) ------

def pl(v: float, cyfry: int) -> str:
    """Liczba w zapisie polskim do opisów testów: przecinek dziesiętny i znak minus (−1,00)."""
    return f"{v:.{cyfry}f}".replace(".", ",").replace("-", "−")


def p_normal(z: float) -> float:
    """Dwustronna p-wartość dla statystyki z o rozkładzie N(0, 1)."""
    return math.erfc(abs(z) / math.sqrt(2))


def chi2_sf(x: float, df: int) -> float:
    """P(χ²(df) > x) dla całkowitego df – wzory zamknięte (parzyste df: szereg Poissona; nieparzyste: erfc + szereg)."""
    h = x / 2
    if df % 2 == 0:
        return math.exp(-h) * sum(h ** i / math.factorial(i) for i in range(df // 2))
    return math.erfc(math.sqrt(h)) + math.exp(-h) * sum(h ** (i - 0.5) / math.gamma(i + 0.5)
                                                       for i in range(1, (df - 1) // 2 + 1))


def spearman_p(r: float, n: int) -> float:
    """p-wartość dla H0: ρ = 0; t = r·√((n−2)/(1−r²)), przy n w setkach t(n−2) ≈ N(0, 1)."""
    return 0.0 if abs(r) >= 1 else p_normal(r * math.sqrt((n - 2) / (1 - r * r)))


def mann_whitney(a: pd.Series, b: pd.Series) -> dict:
    """Test U Manna–Whitneya (przybliżenie normalne z poprawką na rangi wiązane).
    Efekt: P(losowa obserwacja z a > z b) = U / (n_a·n_b); 0,5 = brak różnicy."""
    x = pd.concat([a, b], ignore_index=True)
    n1, n2, n = len(a), len(b), len(a) + len(b)
    u = x.rank().iloc[:n1].sum() - n1 * (n1 + 1) / 2
    t = x.value_counts().values
    sd = math.sqrt(n1 * n2 / 12 * ((n + 1) - (t ** 3 - t).sum() / (n * (n - 1))))
    z = (u - n1 * n2 / 2) / sd
    return {"z": z, "p": p_normal(z), "efekt": u / (n1 * n2)}


def permutation_test(x: np.ndarray, grupa: np.ndarray, n_perm: int = 20000) -> dict:
    """Test permutacyjny różnicy średnich dwóch grup – bez założeń o rozkładzie (p z poprawką +1)."""
    rng = np.random.default_rng(42)
    roznica = lambda g: x[g].mean() - x[~g].mean()  # noqa: E731
    obs = roznica(grupa)
    perm = np.array([roznica(rng.permutation(grupa)) for _ in range(n_perm)])
    return {"roznica": obs, "p": ((np.abs(perm) >= abs(obs)).sum() + 1) / (n_perm + 1)}


def friedman(tab: pd.DataFrame) -> dict:
    """Test Friedmana: bloki = wiersze (dni lub miesiące), grupy = kolumny (miasta); poprawka na rangi wiązane.
    Efekt: W Kendalla (0 – kolejność miast losowa, 1 – w każdym bloku taka sama)."""
    n, k = tab.shape
    rangi = tab.rank(axis=1)
    q = 12 / (n * k * (k + 1)) * (rangi.sum() ** 2).sum() - 3 * n * (k + 1)
    wiazania = sum(((c := wiersz.value_counts().values) ** 3 - c).sum() for _, wiersz in tab.iterrows())
    q /= 1 - wiazania / (n * k * (k * k - 1))
    return {"q": q, "df": k - 1, "p": chi2_sf(q, k - 1), "efekt": q / (n * (k - 1)),
            "srednie_rangi": rangi.mean().sort_values(ascending=False)}


def exact_spearman_p(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    """Korelacja Spearmana i dokładna p-wartość z wszystkich permutacji (dla kilku obserwacji)."""
    rx, ry = x.rank().values, y.rank().values
    r = np.corrcoef(rx, ry)[0, 1]
    wszystkie = [np.corrcoef(rx, p)[0, 1] for p in itertools.permutations(ry)]
    return r, float(np.mean([abs(v) >= abs(r) - 1e-9 for v in wszystkie]))


def significance_tests(days: pd.DataFrame, matrix: pd.DataFrame, ranking: pd.DataFrame) -> pd.DataFrame:
    """p-wartości dla pytań badawczych 1–3. Dni po sobie nie są niezależne (smog trwa kilka dni), więc testy
    z danych dobowych powtarzamy na średnich miesięcznych – mniej obserwacji, ale prawie niezależnych."""
    rows = []
    wiersz = lambda **k: rows.append(k)  # noqa: E731
    d = days.assign(miesiac_roku=days["data"].dt.strftime("%Y-%m"))
    for col, name in POLLUTANTS.items():
        # Pytanie 1: sezon grzewczy vs letni
        zima, lato = d.loc[d["sezon"] == "grzewczy", col], d.loc[d["sezon"] == "letni", col]
        t = mann_whitney(zima, lato)
        wiersz(pytanie=1, wskaznik=name, test="U Manna–Whitneya (dni)", h0="zimą i latem jest tyle samo pyłu",
               dane=f"{len(zima)} + {len(lato)} dni-miasto", statystyka=f"z = {pl(t['z'], 2)}", p=t["p"],
               efekt_nazwa="P(dzień zimowy > letni)", efekt=t["efekt"])
        mies = d.groupby(["miesiac_roku", "sezon"])[col].mean().reset_index()
        g = (mies["sezon"] == "grzewczy").values
        t = permutation_test(mies[col].values, g)
        wiersz(pytanie=1, wskaznik=name, test="permutacyjny (średnie miesięczne)", h0="średnia zimą = średnia latem",
               dane=f"{len(mies)} miesięcy ({g.sum()} grzewczych)", statystyka=f"różnica {pl(t['roznica'], 2)} µg/m³",
               p=t["p"], efekt_nazwa="różnica średnich [µg/m³]", efekt=t["roznica"])
        # Pytanie 3: czy miasta się różnią – te same dni w każdym mieście (bloki), więc test Friedmana
        for opis, tab in [("dni", d.pivot(index="data", columns="miasto", values=col)),
                          ("średnie miesięczne", d.groupby(["miesiac_roku", "miasto"])[col].mean().unstack())]:
            t = friedman(tab)
            wiersz(pytanie=3, wskaznik=name, test=f"Friedmana ({opis})", h0="wszystkie miasta mają takie same stężenia",
                   dane=f"{len(tab)} {'dni' if opis == 'dni' else 'miesięcy'} × {tab.shape[1]} miasta",
                   statystyka=f"Q = {pl(t['q'], 1)}, df = {t['df']}", p=t["p"], efekt_nazwa="W Kendalla", efekt=t["efekt"],
                   uwagi="kolejność: " + " > ".join(t["srednie_rangi"].index))
    # Pytanie 2: istotność współczynników korelacji (cała macierz) – przy dużym n istotne są już bardzo słabe związki
    for zakres, g in matrix.groupby("zakres", sort=False):
        pary = g[g["zmienna_a"] < g["zmienna_b"]]
        n = int(g["n"].iloc[0])
        nieistotne = pary[pary["p"] >= 0.05]
        wiersz(pytanie=2, wskaznik=None, test="korelacja Spearmana",
               h0="brak związku w parze zmiennych", dane=f"{zakres}: n = {n}",
               statystyka=f"|r| krytyczne = {pl(1.96 / math.sqrt(n - 2 + 1.96 ** 2), 3)}", p=None,
               efekt_nazwa="istotne pary", efekt=len(pary) - len(nieistotne),
               uwagi=("nieistotne: " + "; ".join(f"{a} – {b} (r = {pl(r, 3)})" for a, b, r in
                                                  nieistotne[["zmienna_a", "zmienna_b", "r"]].values))
               if len(nieistotne) else f"wszystkie pary istotne ({len(pary)})")
    # Pytanie 3: związek średniego PM10 z cechami powiatu – tylko kilka miast, więc test dokładny
    for cecha, opis in [("ludnosc", "z ludnością"), ("samochody_na_1000", "z liczbą samochodów na 1000 mieszk."),
                        ("gestosc_zaludnienia", "z gęstością zaludnienia")]:
        r, p = exact_spearman_p(ranking["pm10_srednia"], ranking[cecha])
        wiersz(pytanie=3, wskaznik=None, test=f"Spearman, test dokładny ({math.factorial(len(ranking))} ułożeń)",
               h0=f"brak związku PM10 {opis}", dane=f"{len(ranking)} miasta",
               statystyka=f"r = {pl(r, 2)}", p=p, efekt_nazwa="r Spearmana", efekt=r)
    out = pd.DataFrame(rows)
    out["wniosek"] = np.where(out["p"].isna(), "zob. uwagi",
                              np.where(out["p"] < 0.05, "odrzucamy H0", "brak podstaw do odrzucenia H0"))
    return out


def autocorrelation(days: pd.DataFrame) -> pd.DataFrame:
    """Autokorelacja dzień → następny dzień: miara tego, jak bardzo dni nie są niezależne."""
    rows = []
    for miasto, g in days.groupby("miasto"):
        g = g.set_index("data").asfreq("D")  # dni usunięte przy filtracji zostają brakami, nie skracają odstępów
        for col, name in POLLUTANTS.items():
            rows.append({"miasto": miasto, "wskaznik": name, "r_lag1": g[col].autocorr(1)})
    return pd.DataFrame(rows)


# --- Pytanie 1: sezon grzewczy vs letni ----------------------------------------

def seasonal_stats(days: pd.DataFrame) -> pd.DataFrame:
    """Statystyki opisowe PM10/PM2.5 w podziale na miasto i sezon (+ wiersz dla regionu)."""
    rows = []
    groups = [(c, g) for c, g in days.groupby("miasto")] + [("Wszystkie miasta", days)]
    for city, g in groups:
        for col, name in POLLUTANTS.items():
            for season in SEASONS:
                v = g.loc[g["sezon"] == season, col].dropna()
                if v.empty:
                    continue
                rows.append({"miasto": city, "wskaznik": name, "sezon": season, "n_dni": len(v),
                             "srednia": v.mean(), "mediana": v.median(), "odch_std": v.std(),
                             "p90": v.quantile(0.9),
                             "dni_powyzej_50": int((v > LIMIT_PM10).sum()) if col == "pm10" else None})
    df = pd.DataFrame(rows)
    # Iloraz średnich: ile razy wyższe stężenie w sezonie grzewczym
    ratio = df.pivot_table(index=["miasto", "wskaznik"], columns="sezon", values="srednia")
    df = df.merge((ratio["grzewczy"] / ratio["letni"]).rename("iloraz_grzewczy_letni").reset_index(),
                  on=["miasto", "wskaznik"])
    return df.round(2)


def monthly_means(days: pd.DataFrame) -> pd.DataFrame:
    """Średnie miesięczne dla miast oraz średnia ze wszystkich dni wszystkich miast ("Wszystkie miasta")."""
    df = days.assign(miesiac=days["data"].dt.month)
    miasta = df.groupby(["miasto", "miesiac"])[list(POLLUTANTS)].mean().reset_index()
    razem = df.groupby("miesiac")[list(POLLUTANTS)].mean().reset_index().assign(miasto="Wszystkie miasta")
    return pd.concat([miasta, razem[miasta.columns]], ignore_index=True).round(2)


# --- Pytanie 2: korelacje z pogodą ---------------------------------------------

def correlations(days: pd.DataFrame) -> pd.DataFrame:
    """Korelacje Pearsona i Spearmana PM ~ pogoda: cały rok i osobno w sezonach (dane łączne miast)."""
    rows = []
    scopes = {"cały rok": days, **{s: days[days["sezon"] == s] for s in SEASONS}}
    for scope, g in scopes.items():
        for method in ["pearson", "spearman"]:
            for col, name in POLLUTANTS.items():
                for wcol, wname in WEATHER.items():
                    pair = g[[col, wcol]].dropna()
                    rows.append({"zakres": scope, "metoda": method, "wskaznik": name, "zmienna": wname,
                                 "r": pair[col].corr(pair[wcol], method=method), "n": len(pair)})
    return pd.DataFrame(rows).round(3)


def correlation_matrix(days: pd.DataFrame) -> pd.DataFrame:
    """Macierz korelacji Spearmana wszystkich par zmiennych (pył i pogoda): cały rok i sezony, format długi."""
    rows = []
    scopes = {"cały rok": days, **{s: days[days["sezon"] == s] for s in SEASONS}}
    for scope, g in scopes.items():
        g = g[list(VARIABLES)].dropna().rename(columns=VARIABLES)  # te same dni dla każdej pary
        m = g.corr(method="spearman").rename_axis(index="zmienna_a", columns="zmienna_b")
        rows.append(m.stack().rename("r").reset_index().assign(zakres=scope, n=len(g)))
    # Bez zaokrąglania: strona i wykres zaokrąglają raz do 2 miejsc (np. −0,3047 → −0,30, a nie −0,305 → −0,31)
    out = pd.concat(rows, ignore_index=True)[["zakres", "zmienna_a", "zmienna_b", "r", "n"]]
    out["p"] = [spearman_p(r, n) for r, n in zip(out["r"], out["n"])]  # H0: ρ = 0
    return out


# --- Pytanie 3: ranking miast i dane GUS ---------------------------------------

def city_ranking(days: pd.DataFrame, gus: pd.DataFrame) -> pd.DataFrame:
    years = days["data"].dt.year.nunique()
    r = days.groupby("miasto").agg(
        pm10_srednia=("pm10", "mean"), pm10_mediana=("pm10", "median"), pm25_srednia=("pm25", "mean"),
        dni_pm10=("pm10", "count"),
        dni_powyzej_50=("pm10", lambda s: int((s > LIMIT_PM10).sum())),
        dni_zla_pct=("klasa", lambda s: 100 * (s == config.PM10_CLASS_LABELS[2]).sum() / s.notna().sum()),
    )
    r["dni_powyzej_50_rocznie"] = r["dni_powyzej_50"] / years
    # GUS: rok najnowszy (2025); dla samochodów rok 2024 ma anomalię w danych źródłowych
    latest = gus[gus["rok"] == gus["rok"].max()].set_index("miasto")
    r = r.join(latest[["jednostka", "ludnosc", "samochody_osobowe", "samochody_na_1000", "gestosc_zaludnienia"]])
    r = r.sort_values("pm10_srednia", ascending=False)
    r.insert(0, "pozycja", range(1, len(r) + 1))
    return r.reset_index().round(2)


def ranking_vs_gus(ranking: pd.DataFrame) -> pd.DataFrame:
    """Korelacja rang Spearmana między średnim PM10 a cechami powiatu (kilka miast – tylko orientacyjnie)."""
    rows = []
    for col in ["ludnosc", "samochody_na_1000", "gestosc_zaludnienia"]:
        rows.append({"cecha": col, "spearman_r": ranking["pm10_srednia"].corr(ranking[col], method="spearman"),
                     "n_miast": len(ranking)})
    return pd.DataFrame(rows).round(3)


# --- Wykresy ------------------------------------------------------------------

def _save(fig, name: str) -> None:
    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    fig.savefig(config.DATA_PROCESSED / name, dpi=150, bbox_inches="tight")
    plt.close(fig)


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
    _save(fig, "sezonowe.png")


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
        ax.annotate(city, (12, y), xytext=(12.25, y_label), textcoords="data",
                    va="center", color=TEXT, fontsize=9)
        prev = y_label
    ax.set_xticks(range(1, 13), ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"])
    ax.set_xlim(0.5, 13.3)
    ax.set_ylim(bottom=0)
    ax.set_ylabel("µg/m³")
    ax.set_title("Średnie miesięczne PM10 (2023–2025)")
    ax.legend(frameon=False, ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.1))
    _save(fig, "miesieczne_pm10.png")


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
        im = ax.imshow(np.ma.masked_where(diagonal, m.values), cmap=cmap, vmin=-1, vmax=1)
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
    fig.colorbar(im, ax=axes, shrink=0.75, pad=0.015, label="r Spearmana")
    fig.suptitle(f"Macierz korelacji Spearmana: pył i pogoda (dane dobowe, {len(config.CITIES)} miasta)",
                 x=0.01, y=0.98, ha="left", fontweight="bold", color=TEXT)
    _save(fig, "korelacje.png")


def plot_scatter(days: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    for season in SEASONS:
        g = days[days["sezon"] == season].dropna(subset=["pm10", "temp_sr"])
        ax.scatter(g["temp_sr"], g["pm10"], s=10, alpha=0.35, color=SEASON_COLORS[season],
                   label=f"sezon {season}", linewidths=0)
    ax.axhline(LIMIT_PM10, color=TEXT_MUTED, lw=1, ls="--")
    ax.text(ax.get_xlim()[1], LIMIT_PM10, " norma dobowa 50", va="bottom", ha="right", color=TEXT_MUTED, fontsize=8)
    ax.set_xlabel("średnia dobowa temperatura [°C]")
    ax.set_ylabel("PM10 [µg/m³]")
    ax.set_title(f"PM10 a temperatura (dni × {len(config.CITIES)} miasta)")
    ax.legend(frameon=False, markerscale=2)
    _save(fig, "rozrzut_pm10_temp.png")


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
    _save(fig, "ranking_pm10.png")


def plot_distributions(stats: pd.DataFrame, hist: pd.DataFrame) -> None:
    """Histogramy wszystkich zmiennych: średnia (linia ciągła), mediana (przerywana), przedział modalny (ciemniejszy)."""
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
    _save(fig, "rozklady.png")


def run() -> dict[str, pd.DataFrame]:
    with sqlite3.connect(config.DB_PATH) as con:
        days, gus = load(con)
        dane_eda = load_eda(con)
        opisowe, histogramy = descriptive_stats(dane_eda)
        results = {
            "wyniki_opisowe": opisowe,
            "wyniki_histogramy": histogramy,
            "wyniki_regula_3sigma": sigma_rule(dane_eda, opisowe),
            "wyniki_autokorelacja": autocorrelation(days),
            "wyniki_sezonowe": seasonal_stats(days),
            "wyniki_miesieczne": monthly_means(days),
            "wyniki_korelacje": correlations(days),
            "wyniki_macierz_korelacji": correlation_matrix(days),
            "wyniki_ranking": city_ranking(days, gus),
        }
        results["wyniki_ranking_gus"] = ranking_vs_gus(results["wyniki_ranking"])
        results["wyniki_testy"] = significance_tests(days, results["wyniki_macierz_korelacji"], results["wyniki_ranking"])
        for name, df in results.items():
            df.to_sql(name, con, if_exists="replace", index=False)
            print(f"  {name}: {len(df)} wierszy")
    con.close()
    plot_distributions(opisowe, histogramy)
    plot_seasonal(results["wyniki_sezonowe"])
    plot_monthly(results["wyniki_miesieczne"])
    plot_correlations(results["wyniki_macierz_korelacji"])
    plot_scatter(days)
    plot_ranking(results["wyniki_ranking"])
    print(f"  wykresy PNG: {config.DATA_PROCESSED.relative_to(config.ROOT)}")
    results["dni"] = days
    return results


if __name__ == "__main__":
    run()
