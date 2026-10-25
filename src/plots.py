"""Wspólny wygląd wykresów PNG: kolory, styl matplotlib i zapis do data/processed/.

Każdy moduł rysujący (eda, analyze, cluster) importuje kolory i funkcję save()
stąd, dzięki czemu wszystkie wykresy wyglądają tak samo.
"""
import matplotlib

matplotlib.use("Agg")  # rysowanie do plików, bez okna
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

from src import config

# Kolory przypisane na stałe (paleta sprawdzona pod kątem daltonizmu)
CITY_COLORS = dict(zip(config.CITIES, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]))
SEASON_COLORS = {"letni": "#2a78d6", "grzewczy": "#eb6834"}
CLASS_COLORS = dict(zip(config.PM10_CLASS_LABELS, ["#0ca30c", "#fab219", "#d03b3b"]))  # dobra, umiarkowana, zła
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
