"""TK04 – klasteryzacja: podział dni na typy pogody i sprawdzenie, w którym typie jest najwięcej pyłu.

Kroki (funkcja run na dole pliku):
1. Dane: jeden wiersz = dzień na jednej stacji IMGW (każde miasto ma swoją).
   Ciśnienie jako różnica od średniej stacji.
2. Cechy: tylko pogoda, opad zlogarytmowany, wszystko sprowadzone do jednej skali.
3. Wybór liczby typów k (2–8) według wskaźnika silhouette.
4. KMeans i opis typów: średnia pogoda, pył (nie brał udziału w podziale), udział dni złych.
"""
import json
import sqlite3

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler

from src import config
from src.plots import DIVERGING, GRID, SURFACE, TEXT, TEXT_MUTED, save

FEATURES = ["temp_sr", "wiatr_sr", "opad", "wilgotnosc", "cisnienie"]
FEATURE_NAMES = {"temp_sr": "temperatura", "wiatr_sr": "prędkość wiatru", "opad": "opad",
                 "wilgotnosc": "wilgotność", "cisnienie": "anomalia ciśnienia"}
K_RANGE = range(2, 9)
RANDOM_STATE = 42
# Paleta kategoryczna w stałej kolejności (klaster 1, 2, ... – posortowane malejąco wg PM10)
CLUSTER_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
# Słowa do nazw klastrów: (cecha, znak z-score) -> przymiotnik
DESCRIPTORS = {("temp_sr", 1): "ciepłe", ("temp_sr", -1): "zimne",
               ("wiatr_sr", 1): "wietrzne", ("wiatr_sr", -1): "bezwietrzne",
               ("opad", 1): "deszczowe", ("opad", -1): "bez opadu",
               ("wilgotnosc", 1): "wilgotne", ("wilgotnosc", -1): "suche",
               ("cisnienie", 1): "wyżowe", ("cisnienie", -1): "niżowe"}


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    """1. Pogoda dni pełnych (dzień × stacja IMGW) i pył dni-miasto do opisu typów."""
    with sqlite3.connect(config.DB_PATH) as con:
        # Tylko dni kompletne (te same co w zbiorze analitycznym dzien_miasto)
        weather = pd.read_sql("SELECT w.data, w.imgw_kod, " + ", ".join(f"w.{f}" for f in FEATURES)
                              + " FROM pogoda_dobowa w JOIN dni_kompletne USING (data)", con)
        days = pd.read_sql("SELECT miasto, data, pm10, pm25, sezon, klasa FROM dzien_miasto", con)
    con.close()
    weather = weather.dropna(subset=FEATURES).sort_values(["data", "imgw_kod"]).reset_index(drop=True)
    # Ciśnienie na poziomie stacji zależy od wysokości n.p.m. (Katowice leżą ok. 270 m wyżej niż Gdańsk-Świbno),
    # więc porównujemy anomalię względem średniej danej stacji – inaczej klastry odzwierciedlałyby stacje.
    weather["cisnienie"] = weather["cisnienie"] - weather.groupby("imgw_kod")["cisnienie"].transform("mean")
    days["imgw_kod"] = days["miasto"].map({c: v["imgw"] for c, v in config.CITIES.items()})
    return weather, days


def feature_matrix(weather: pd.DataFrame) -> np.ndarray:
    """2. Cechy do KMeans: logarytm opadu i jedna skala dla wszystkich (StandardScaler)."""
    X = weather[FEATURES].copy()
    X["opad"] = np.log1p(X["opad"])  # opad silnie prawoskośny (większość dni = 0 mm)
    return StandardScaler().fit_transform(X)


def choose_k(X: np.ndarray) -> tuple[pd.DataFrame, int]:
    """3. Silhouette i inercja dla k = 2..8; wybieramy k z najwyższym silhouette."""
    rows = []
    for k in K_RANGE:
        km = KMeans(n_clusters=k, n_init=20, random_state=RANDOM_STATE).fit(X)
        rows.append({"k": k, "silhouette": silhouette_score(X, km.labels_), "inercja": km.inertia_,
                     "min_rozmiar": int(np.bincount(km.labels_).min())})
    scores = pd.DataFrame(rows)
    scores["spadek_inercji"] = -scores["inercja"].diff()
    return scores.round(3), int(scores.loc[scores["silhouette"].idxmax(), "k"])


def cluster_name(z: pd.Series) -> str:
    """Nazwa z dwóch cech o największym |z-score| średniej klastra."""
    top = z.abs().sort_values(ascending=False).index[:2]
    return ", ".join(DESCRIPTORS[(f, 1 if z[f] > 0 else -1)] for f in top)


def profile(weather: pd.DataFrame, days: pd.DataFrame, X: np.ndarray, k: int) -> dict:
    """4. KMeans dla wybranego k i opis każdego typu (pogoda, pył, klasy dni, miesiące, stacje)."""
    km = KMeans(n_clusters=k, n_init=20, random_state=RANDOM_STATE).fit(X)
    weather = weather.assign(klaster_km=km.labels_)
    merged = days.merge(weather[["data", "imgw_kod", "klaster_km"]], on=["data", "imgw_kod"], how="inner")

    # Numeracja klastrów od najwyższego średniego PM10 (stabilne kolory i czytelna kolejność)
    order = merged.groupby("klaster_km")["pm10"].mean().sort_values(ascending=False).index
    relabel = {old: new for new, old in enumerate(order, start=1)}
    weather["klaster"] = weather["klaster_km"].map(relabel)
    merged["klaster"] = merged["klaster_km"].map(relabel)

    # Profil pogody (jednostki fizyczne) i z-score średnich do nazw i heatmapy
    phys = weather.groupby("klaster")[FEATURES].mean()
    z = pd.DataFrame(X, columns=FEATURES).assign(klaster=weather["klaster"].values).groupby("klaster").mean()
    weather["miesiac"] = pd.to_datetime(weather["data"]).dt.month
    heating = weather["miesiac"].isin(config.HEATING_MONTHS)

    limit = config.PM10_DAILY_LIMIT
    rows = []
    for c in sorted(weather["klaster"].unique()):
        w = weather[weather["klaster"] == c]
        m = merged[merged["klaster"] == c]
        pm10 = m["pm10"].dropna()
        rows.append({
            "klaster": int(c), "nazwa": cluster_name(z.loc[c]), "kolor": CLUSTER_COLORS[c - 1],
            "n_dni_pogodowych": len(w), "n_dni_miasto": len(m),
            # 4 miejsca: strona zaokrągla do 1 miejsca, więc 2 miejsca dawałyby podwójne zaokrąglenie (10,3496 → 10,35 → 10,4)
            **{f: round(phys.loc[c, f], 4) for f in FEATURES},
            "udzial_grzewczy_pct": round(100 * heating[weather["klaster"] == c].mean(), 1),
            "pm10_srednia": round(pm10.mean(), 4), "pm10_mediana": round(pm10.median(), 4),
            "pm25_srednia": round(m["pm25"].mean(), 4), "pm25_mediana": round(m["pm25"].median(), 4),
            "dni_pm10": len(pm10), "dni_powyzej_50": int((pm10 > limit).sum()),
            "dni_zla_pct": round(100 * (m["klasa"] == config.PM10_CLASS_LABELS[2]).sum() / m["klasa"].notna().sum(), 2),
        })
    prof = pd.DataFrame(rows)

    # Rozkład miesięcy: udział klastrów w dniach pogodowych danego miesiąca
    months = (pd.crosstab(weather["miesiac"], weather["klaster"], normalize="index") * 100).round(1)
    # Klaster × klasa jakości (dni miasto) i zgodność podziałów (ARI)
    with_class = merged.dropna(subset=["klasa"])
    cross = pd.crosstab(with_class["klaster"], with_class["klasa"]).reindex(columns=config.PM10_CLASS_LABELS, fill_value=0)
    ari = adjusted_rand_score(with_class["klasa"], with_class["klaster"])
    # Udział stacji w klastrach – kontrola, czy klastry nie odpowiadają stacjom
    stations = (pd.crosstab(weather["klaster"], weather["imgw_kod"], normalize="columns") * 100).round(1)
    # Kwartyle PM w klastrach – do wykresu pudełkowego na stronie
    quart = {}
    for col in ["pm10", "pm25"]:
        q = merged.groupby("klaster")[col].quantile([0.1, 0.25, 0.5, 0.75, 0.9]).unstack().round(1)
        quart[col] = {int(c): q.loc[c].tolist() for c in q.index}

    return {"weather": weather, "merged": merged, "profile": prof, "z": z, "months": months,
            "cross": cross, "ari": round(ari, 3), "stations": stations, "quartiles": quart}


# --- Wykresy ------------------------------------------------------------------

def plot_k(scores: pd.DataFrame, k: int) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    for ax, col, title in [(axes[0], "silhouette", "Silhouette (wyżej = lepiej)"),
                           (axes[1], "inercja", "Inercja – metoda łokcia")]:
        ax.plot(scores["k"], scores[col], color="#2a78d6", lw=2, marker="o", ms=6)
        best = scores[scores["k"] == k].iloc[0]
        ax.plot(best["k"], best[col], marker="o", ms=12, mfc="none", mec=TEXT, mew=2)
        ax.annotate(f"k = {k}", (best["k"], best[col]), xytext=(12, -16), textcoords="offset points", color=TEXT)
        ax.set_xticks(scores["k"])
        ax.set_xlabel("liczba klastrów k")
        ax.set_title(title)
    save(fig, "klastry_wybor_k.png")


def plot_profile(res: dict) -> None:
    prof, z = res["profile"], res["z"]
    labels = [f"{r.klaster}. {r.nazwa}" for r in prof.itertuples()]
    fig, ax = plt.subplots(figsize=(9, 0.7 * len(prof) + 1.6))
    lim = max(1.5, float(np.abs(z.values).max()))
    ax.imshow(z.values, cmap=DIVERGING, vmin=-lim, vmax=lim, aspect="auto")
    for (i, j), v in np.ndenumerate(z.values):
        phys = prof.iloc[i][FEATURES[j]]
        unit = {"temp_sr": "°C", "wiatr_sr": "m/s", "opad": "mm", "wilgotnosc": "%", "cisnienie": "hPa"}[FEATURES[j]]
        ax.text(j, i, f"{phys:+.1f} {unit}" if FEATURES[j] == "cisnienie" else f"{phys:.1f} {unit}",
                ha="center", va="center", fontsize=9, color="white" if abs(v) > 0.9 * lim * 0.6 else TEXT)
    ax.set_xticks(range(len(FEATURES)), [FEATURE_NAMES[f] for f in FEATURES])
    ax.set_yticks(range(len(prof)), labels)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Profil klastrów: średnie cech (kolor = z-score względem wszystkich dni)")
    save(fig, "klastry_profil.png")


def plot_pm(res: dict) -> None:
    prof, merged = res["profile"], res["merged"]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    data = [merged.loc[merged["klaster"] == c, "pm10"].dropna().values for c in prof["klaster"]]
    bp = ax.boxplot(data, positions=range(len(data)), widths=0.55, showfliers=False, patch_artist=True,
                    medianprops={"color": TEXT, "lw": 1.5}, whiskerprops={"color": TEXT_MUTED},
                    capprops={"color": TEXT_MUTED})
    for patch, color in zip(bp["boxes"], prof["kolor"]):
        patch.set_facecolor(color)
        patch.set_edgecolor(SURFACE)
    ax.scatter(range(len(data)), prof["pm10_srednia"], color=TEXT, marker="D", s=22, zorder=3, label="średnia")
    ax.axhline(config.PM10_CLASS_BINS[1], color=TEXT_MUTED, lw=1, ls="--")
    ax.text(len(data) - 0.5, config.PM10_CLASS_BINS[1], "norma dobowa 50 ", ha="right", va="bottom",
            color=TEXT_MUTED, fontsize=8)
    ax.set_xticks(range(len(data)), [f"{r.klaster}. {r.nazwa}\n" + f"{r.dni_zla_pct:.1f}".replace(".", ",") + "% dni złych"
                                     for r in prof.itertuples()], fontsize=9)
    ax.set_ylabel("PM10 [µg/m³]")
    ax.set_ylim(bottom=0)
    ax.set_title("PM10 w typach dni pogodowych (pudełko: kwartyle, wąsy: 1,5 IQR)")
    ax.legend(frameon=False, loc="upper right")
    ax.grid(axis="x", visible=False)
    save(fig, "klastry_pm10.png")


def plot_pca(res: dict, X: np.ndarray) -> None:
    prof, weather = res["profile"], res["weather"]
    pca = PCA(n_components=2, random_state=RANDOM_STATE)
    P = pca.fit_transform(X)
    fig, ax = plt.subplots(figsize=(8, 6))
    for r in prof.itertuples():
        mask = (weather["klaster"] == r.klaster).values
        ax.scatter(P[mask, 0], P[mask, 1], s=7, alpha=0.35, color=r.kolor, linewidths=0, label=f"{r.klaster}. {r.nazwa}")
    # Przy k > 3 kolory w rozrzucie nie wystarczają – centroidy z etykietami
    for i, r in enumerate(prof.itertuples()):
        mask = (weather["klaster"] == r.klaster).values
        cx, cy = P[mask, 0].mean(), P[mask, 1].mean()
        ax.scatter(cx, cy, s=160, color=r.kolor, edgecolors=SURFACE, linewidths=2.5, zorder=3)
        # Etykiety na przemian nad punktem (w prawo) i pod nim (w lewo), żeby bliskie centroidy się nie nakładały
        above = i % 2 == 0
        ax.annotate(f"{r.klaster}. {r.nazwa}", (cx, cy), xytext=(10, 10) if above else (-10, -14),
                    textcoords="offset points", ha="left" if above else "right", va="bottom" if above else "top",
                    fontsize=10, fontweight="bold", color=TEXT, zorder=4,
                    arrowprops={"arrowstyle": "-", "color": TEXT_MUTED, "lw": 1, "shrinkB": 7},
                    bbox={"boxstyle": "round,pad=0.25", "fc": SURFACE, "ec": GRID, "alpha": 0.9})
    ev = pca.explained_variance_ratio_ * 100
    ax.set_xlabel(f"PC1 ({ev[0]:.0f}% wariancji) – pozostałe {100 - ev.sum():.0f}% (m.in. opad vs wiatr) poza rzutem")
    ax.set_ylabel(f"PC2 ({ev[1]:.0f}% wariancji)")
    ax.set_title(f"Dni pogodowe w rzucie PCA ({len(config.IMGW_STATIONS)} stacje IMGW × 2023–2025)")
    ax.legend(frameon=False, markerscale=3, loc="best", fontsize=9)
    save(fig, "klastry_pca.png")


def run() -> dict:
    weather, days = load()
    X = feature_matrix(weather)
    scores, k = choose_k(X)
    res = profile(weather, days, X, k)
    best, second = scores.sort_values("silhouette", ascending=False).iloc[:2].itertuples()
    rationale = (f"k = {k}: najwyższe silhouette ({best.silhouette:.3f}; kolejne k = {second.k}: {second.silhouette:.3f}), "
                 f"a spadek inercji po k = {k} wyraźnie maleje (łokieć).")
    print(f"  dni pogodowe: {len(weather)} (data × stacja IMGW), wybrane k = {k}")
    print("  " + ", ".join(f"k={r.k}: {r.silhouette:.3f}" for r in scores.itertuples()))

    prof = res["profile"]
    report = {
        "metoda": {"obiekt": "dzień pogodowy (data × stacja IMGW)", "n": len(weather),
                   "cechy": [FEATURE_NAMES[f] for f in FEATURES],
                   "przeksztalcenia": "opad: log1p; ciśnienie: anomalia względem średniej stacji; StandardScaler",
                   "algorytm": "KMeans (n_init=20, random_state=42)"},
        "wybor_k": scores.to_dict("records"), "k": k, "uzasadnienie": rationale,
        "profil": prof.to_dict("records"),
        "z_score": {int(c): res["z"].loc[c].round(3).to_dict() for c in res["z"].index},
        "miesiace": {int(m): {int(c): v for c, v in row.items()} for m, row in res["months"].iterrows()},
        "klaster_klasa": {int(c): row.to_dict() for c, row in res["cross"].iterrows()},
        "ari": res["ari"],
        "udzial_stacji_pct": {int(c): {int(s): v for s, v in row.items()} for c, row in res["stations"].iterrows()},
        "kwartyle_pm": res["quartiles"],
    }
    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    (config.DATA_PROCESSED / "klastry_raport.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with sqlite3.connect(config.DB_PATH) as con:
        res["weather"][["data", "imgw_kod", "klaster"]].to_sql("klastry_dni", con, if_exists="replace", index=False)
        prof.to_sql("klastry_profil", con, if_exists="replace", index=False)
    con.close()

    plot_k(scores, k)
    plot_profile(res)
    plot_pm(res)
    plot_pca(res, X)
    for r in prof.itertuples():
        print(f"  {r.klaster}. {r.nazwa}: {r.n_dni_pogodowych} dni, PM10 {r.pm10_srednia}, dni złe {r.dni_zla_pct}%")
    print(f"  ARI (klaster vs klasa jakości): {res['ari']}")
    return report


if __name__ == "__main__":
    run()
