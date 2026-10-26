import matplotlib

matplotlib.use("Agg")  # rysowanie do plików, bez okna
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from sklearn.decomposition import PCA

from src import config

POLLUTANTS, SEASONS = config.POLLUTANTS, config.SEASONS
VARIABLES = {**config.POLLUTANTS, **config.WEATHER}
LIMIT = config.PM10_DAILY_LIMIT

# Kolory przypisane na stałe (paleta sprawdzona pod kątem daltonizmu)
CITY_COLORS = dict(zip(config.CITIES, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]))
SEASON_COLORS = {"letni": "#2a78d6", "grzewczy": "#eb6834"}
CLASS_COLORS = dict(zip(config.PM10_CLASS_LABELS, ["#0ca30c", "#fab219", "#d03b3b"]))
TEXT, TEXT_MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
# Skala rozbieżna do korelacji: niebieski (−1) – szary (0) – czerwony (+1)
DIVERGING = LinearSegmentedColormap.from_list("rozbiezna", ["#1c5cab", "#86b6ef", "#f0efec", "#ef8f8e", "#b52f2f"])

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": TEXT_MUTED, "axes.titlecolor": TEXT,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "xtick.color": TEXT_MUTED, "ytick.color": TEXT_MUTED, "font.size": 10,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
})


def save(fig, name: str) -> None:
    """Zapisuje wykres do data/processed/<name> i zamyka go."""
    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    fig.savefig(config.DATA_PROCESSED / name, dpi=150, bbox_inches="tight")
    plt.close(fig)


# --- Eksploracja danych (eda.py) ------------------------------------------------------------------

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


# --- Pytania 1–4 (analyze.py) ---------------------------------------------------------------------

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
    for ax, scope in zip(axes, ["cały rok", "grzewczy", "letni"]):
        sub = matrix[matrix["zakres"] == scope]
        m = sub.pivot(index="zmienna_a", columns="zmienna_b", values="r").reindex(index=names, columns=names)
        image = ax.imshow(np.ma.masked_where(diagonal, m.values), cmap=cmap, vmin=-1, vmax=1)
        for (i, j), v in np.ndenumerate(m.values):
            ax.text(j, i, "1" if i == j else f"{v:+.2f}", ha="center", va="center", fontsize=8.5,
                    color=TEXT_MUTED if i == j else "white" if abs(v) > 0.55 else TEXT)
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


# --- Typy dni pogodowych (cluster.py) -------------------------------------------------------------

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


def plot_profile(res: dict, feature_names: dict[str, str]) -> None:
    prof, z = res["profile"], res["z"]
    features = list(feature_names)
    labels = [f"{r.klaster}. {r.nazwa}" for r in prof.itertuples()]
    fig, ax = plt.subplots(figsize=(9, 0.7 * len(prof) + 1.6))
    lim = max(1.5, float(np.abs(z.values).max()))
    ax.imshow(z.values, cmap=DIVERGING, vmin=-lim, vmax=lim, aspect="auto")
    for (i, j), v in np.ndenumerate(z.values):
        phys = prof.iloc[i][features[j]]
        unit = {"temp_sr": "°C", "wiatr_sr": "m/s", "opad": "mm", "wilgotnosc": "%", "cisnienie": "hPa"}[features[j]]
        ax.text(j, i, f"{phys:+.1f} {unit}" if features[j] == "cisnienie" else f"{phys:.1f} {unit}",
                ha="center", va="center", fontsize=9, color="white" if abs(v) > 0.9 * lim * 0.6 else TEXT)
    ax.set_xticks(range(len(features)), list(feature_names.values()))
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


def plot_pca(res: dict, X: np.ndarray, random_state: int) -> None:
    prof, weather = res["profile"], res["weather"]
    pca = PCA(n_components=2, random_state=random_state)
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
