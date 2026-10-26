import json
import sqlite3

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler

from src import config
from src.plots import plot_k, plot_pca, plot_pm, plot_profile

FEATURES = ["temp_sr", "wiatr_sr", "opad", "wilgotnosc", "cisnienie"]
FEATURE_NAMES = {"temp_sr": "temperatura", "wiatr_sr": "prędkość wiatru", "opad": "opad",
                 "wilgotnosc": "wilgotność", "cisnienie": "anomalia ciśnienia"}
K_RANGE = range(2, 9)
RANDOM_STATE = 42
# Paleta kategoryczna w stałej kolejności (klaster 1, 2, ... – posortowane malejąco wg PM10)
CLUSTER_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
DESCRIPTORS = {("temp_sr", 1): "ciepłe", ("temp_sr", -1): "zimne",
               ("wiatr_sr", 1): "wietrzne", ("wiatr_sr", -1): "bezwietrzne",
               ("opad", 1): "deszczowe", ("opad", -1): "bez opadu",
               ("wilgotnosc", 1): "wilgotne", ("wilgotnosc", -1): "suche",
               ("cisnienie", 1): "wyżowe", ("cisnienie", -1): "niżowe"}


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    """1. Pogoda dni pełnych (dzień × stacja IMGW) i pył dni-miasto do opisu typów."""
    with sqlite3.connect(config.DB_PATH) as con:
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

    months = (pd.crosstab(weather["miesiac"], weather["klaster"], normalize="index") * 100).round(1)
    with_class = merged.dropna(subset=["klasa"])
    cross = pd.crosstab(with_class["klaster"], with_class["klasa"]).reindex(columns=config.PM10_CLASS_LABELS, fill_value=0)
    ari = adjusted_rand_score(with_class["klasa"], with_class["klaster"])
    # Udział stacji w klastrach – kontrola, czy klastry nie odpowiadają stacjom
    stations = (pd.crosstab(weather["klaster"], weather["imgw_kod"], normalize="columns") * 100).round(1)
    quart = {}
    for col in ["pm10", "pm25"]:
        q = merged.groupby("klaster")[col].quantile([0.1, 0.25, 0.5, 0.75, 0.9]).unstack().round(1)
        quart[col] = {int(c): q.loc[c].tolist() for c in q.index}

    return {"weather": weather, "merged": merged, "profile": prof, "z": z, "months": months,
            "cross": cross, "ari": round(ari, 3), "stations": stations, "quartiles": quart}


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
    pca = PCA(n_components=2, random_state=RANDOM_STATE)
    P = pca.fit_transform(X)
    ev = pca.explained_variance_ratio_ * 100
    klaster_arr = res["weather"]["klaster"].values
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
        "pca": {
            "wariancja": [round(float(ev[0]), 1), round(float(ev[1]), 1)],
            "punkty": {int(c): P[klaster_arr == c].round(2).tolist() for c in sorted(set(klaster_arr))},
        },
    }
    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    (config.DATA_PROCESSED / "klastry_raport.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    plot_k(scores, k)
    plot_profile(res, FEATURE_NAMES)
    plot_pm(res)
    plot_pca(res, X, RANDOM_STATE)
    for r in prof.itertuples():
        print(f"  {r.klaster}. {r.nazwa}: {r.n_dni_pogodowych} dni, PM10 {r.pm10_srednia}, dni złe {r.dni_zla_pct}%")
    print(f"  ARI (klaster vs klasa jakości): {res['ari']}")
    return report
