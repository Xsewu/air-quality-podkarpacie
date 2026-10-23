"""TK04 – model klasyfikacji jakości dnia (drzewo decyzyjne, random forest).

Klasa dnia wynika z dobowego PM10 (progi w config.py), a cechami są wyłącznie
warunki pogodowe z tego samego dnia, sezon grzewczy i miasto – bez pomiarów pyłu.
Podział train/test jest czasowy (bez losowego mieszania dni): uczenie na latach
2023–2024, test na 2025. Przed uczeniem selekcja cech na podstawie modułu korelacji
(nadmiarowe cechy usuwane). Hiperparametry dobierane walidacją TimeSeriesSplit
wyłącznie na zbiorze uczącym.
"""
import json
import sqlite3
from itertools import combinations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression
from sklearn.metrics import (accuracy_score, balanced_accuracy_score, classification_report,
                             confusion_matrix, f1_score)
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sklearn.tree import DecisionTreeClassifier

from src import config
from src.analyze import SURFACE, TEXT, TEXT_MUTED, _save

WEATHER_FEATURES = ["temp_sr", "opad", "wiatr_sr", "wilgotnosc", "cisnienie"]
LABELS = config.PM10_CLASS_LABELS
TEST_YEAR = config.YEARS[-1]
RANDOM_STATE = 42
REDUNDANCY_THRESHOLD = 0.8  # |r| powyżej progu – cechy nadmiarowe (niosą prawie tę samą informację)


def load_dataset() -> pd.DataFrame:
    with sqlite3.connect(config.DB_PATH) as con:
        df = pd.read_sql("SELECT * FROM dzien_miasto", con, parse_dates=["data"])
    con.close()
    df = df.dropna(subset=["klasa", *WEATHER_FEATURES]).sort_values(["data", "miasto"]).reset_index(drop=True)
    df["sezon_grzewczy"] = (df["sezon"] == "grzewczy").astype(int)
    return df


def features(df: pd.DataFrame) -> pd.DataFrame:
    cities = pd.get_dummies(df["miasto"], prefix="miasto", dtype=int)
    return pd.concat([df[WEATHER_FEATURES + ["sezon_grzewczy"]], cities], axis=1)


def select_features(X_train: pd.DataFrame) -> tuple[list[str], dict]:
    """Selekcja cech przed uczeniem: moduł korelacji Spearmana |r| między cechami, tylko na zbiorze uczącym.

    Z każdej pary o |r| > progu usuwamy cechę silniej powiązaną (średnio) z pozostałymi.
    Kolumny miast pomijamy – to kodowanie one-hot jednej zmiennej, zależne z założenia.
    """
    cols = WEATHER_FEATURES + ["sezon_grzewczy"]
    corr = X_train[cols].corr(method="spearman").abs()
    removed = []
    for a, b in combinations(cols, 2):
        if a in removed or b in removed or corr.loc[a, b] <= REDUNDANCY_THRESHOLD:
            continue
        mean_r = corr.drop(index=removed, columns=removed).mean()
        removed.append(a if mean_r[a] >= mean_r[b] else b)
    a, b = max(combinations(cols, 2), key=lambda p: corr.loc[p])
    info = {"metoda": "moduł korelacji Spearmana, zbiór uczący", "prog": REDUNDANCY_THRESHOLD,
            "n": len(X_train), "cechy": cols, "etykiety": [feature_label(c) for c in cols],
            "modul_korelacji": corr.values.tolist(),
            "najsilniejsza_para": {"cechy": [feature_label(a), feature_label(b)], "r": corr.loc[a, b]},
            "usuniete": [feature_label(c) for c in removed],
            "vif": vif(X_train[cols]), "prog_vif": VIF_THRESHOLD}
    return [c for c in X_train.columns if c not in removed], info


VIF_THRESHOLD = 5  # VIF > 5 – cecha w dużej mierze wynika z pozostałych (wielokolinearność)


def vif(X: pd.DataFrame) -> list[float]:
    """Współczynnik inflacji wariancji: VIF_j = 1 / (1 − R²_j), gdzie R²_j z regresji cechy j na pozostałych.
    Para |r| widzi tylko związki dwóch cech; VIF wykrywa też cechę odtwarzaną przez kilka innych naraz."""
    out = []
    for c in X.columns:
        reszta = X.drop(columns=c)
        r2 = LinearRegression().fit(reszta, X[c]).score(reszta, X[c])
        out.append(1 / (1 - r2))
    return out


def evaluate(name: str, model, X_test, y_test) -> dict:
    pred = model.predict(X_test)
    report = classification_report(y_test, pred, labels=LABELS, output_dict=True, zero_division=0)
    return {
        "model": name,
        "accuracy": round(accuracy_score(y_test, pred), 3),
        "balanced_accuracy": round(balanced_accuracy_score(y_test, pred), 3),
        "f1_macro": round(f1_score(y_test, pred, labels=LABELS, average="macro", zero_division=0), 3),
        "per_class": {c: {k: round(report[c][k], 3) for k in ["precision", "recall", "f1-score", "support"]}
                      for c in LABELS},
        "confusion_matrix": confusion_matrix(y_test, pred, labels=LABELS).tolist(),
    }


def plot_confusion(results: list[dict]) -> None:
    fig, axes = plt.subplots(1, len(results), figsize=(4.2 * len(results), 3.8))
    for ax, res in zip(axes, results):
        cm = np.array(res["confusion_matrix"])
        share = cm / cm.sum(axis=1, keepdims=True)  # udział w klasie rzeczywistej
        ax.imshow(share, cmap="Blues", vmin=0, vmax=1)
        for (i, j), v in np.ndenumerate(cm):
            ax.text(j, i, f"{v}\n{share[i, j]:.0%}", ha="center", va="center", fontsize=9,
                    color="white" if share[i, j] > 0.55 else TEXT)
        ax.set_xticks(range(3), LABELS)
        ax.set_yticks(range(3), LABELS if ax is axes[0] else [""] * 3)
        ax.set_xlabel("przewidziana")
        ax.set_title(f"{res['model']}\nbalanced acc. {res['balanced_accuracy']:.2f}", fontsize=10)
        ax.grid(False)
    axes[0].set_ylabel("rzeczywista")
    _save(fig, "model_macierz_pomylek.png")


FEATURE_NAMES = {"temp_sr": "temperatura",
                 "opad": "opad", "wiatr_sr": "prędkość wiatru", "wilgotnosc": "wilgotność",
                 "cisnienie": "ciśnienie", "sezon_grzewczy": "sezon grzewczy"}


def feature_label(name: str) -> str:
    return FEATURE_NAMES.get(name, name.replace("miasto_", "miasto: "))


def plot_importance(imp: pd.DataFrame) -> None:
    imp = imp.sort_values("permutacyjna")
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.barh(imp["cecha"].map(feature_label), imp["permutacyjna"], xerr=imp["permutacyjna_std"], color="#2a78d6",
            height=0.6, edgecolor=SURFACE, linewidth=2, error_kw={"ecolor": TEXT_MUTED, "lw": 1})
    ax.axvline(0, color=TEXT_MUTED, lw=1)
    ax.set_xlabel("spadek balanced accuracy po permutacji cechy (zbiór testowy)")
    ax.set_title("Ważność cech – random forest")
    ax.grid(axis="y", visible=False)
    _save(fig, "model_waznosc_cech.png")


def run() -> dict:
    df = load_dataset()
    X, y = features(df), df["klasa"]
    train = df["data"].dt.year < TEST_YEAR
    X_train, X_test, y_train, y_test = X[train], X[~train], y[train], y[~train]
    print(f"  train: {train.sum()} dni ({df.loc[train, 'data'].min():%Y-%m-%d}..{df.loc[train, 'data'].max():%Y-%m-%d}), "
          f"test: {(~train).sum()} dni ({df.loc[~train, 'data'].min():%Y-%m-%d}..{df.loc[~train, 'data'].max():%Y-%m-%d})")

    keep, selection = select_features(X_train)
    X, X_train, X_test = X[keep], X_train[keep], X_test[keep]
    para = selection["najsilniejsza_para"]
    print(f"  selekcja cech: najsilniejsza para {' - '.join(para['cechy'])} |r| = {para['r']:.3f} "
          f"(próg {REDUNDANCY_THRESHOLD}), usunięte: {', '.join(selection['usuniete']) or 'brak'}")

    # Walidacja krzyżowa z zachowaniem kolejności w czasie (dane posortowane po dacie).
    # 3 foldy po ~6 miesięcy – każdy zawiera część sezonu grzewczego i wszystkie klasy
    # (przy 4 foldach walidacja maj–paź 2023 nie miała ani jednego dnia "zła").
    cv = TimeSeriesSplit(n_splits=3)
    tree = GridSearchCV(
        DecisionTreeClassifier(class_weight="balanced", random_state=RANDOM_STATE),
        {"max_depth": [3, 4, 5, 6, 8], "min_samples_leaf": [10, 25, 50]},
        cv=cv, scoring="balanced_accuracy").fit(X_train, y_train)
    forest = GridSearchCV(
        RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=RANDOM_STATE, n_jobs=-1),
        {"max_depth": [4, 6, 8, None], "min_samples_leaf": [5, 20, 50]},
        cv=cv, scoring="balanced_accuracy").fit(X_train, y_train)
    baseline = DummyClassifier(strategy="most_frequent").fit(X_train, y_train)

    results = [evaluate("klasa większościowa", baseline, X_test, y_test),
               evaluate("drzewo decyzyjne", tree.best_estimator_, X_test, y_test),
               evaluate("random forest", forest.best_estimator_, X_test, y_test)]
    results[1]["parametry"] = tree.best_params_
    results[2]["parametry"] = forest.best_params_
    results[1]["cv_balanced_accuracy"] = round(tree.best_score_, 3)
    results[2]["cv_balanced_accuracy"] = round(forest.best_score_, 3)

    rf = forest.best_estimator_
    perm = permutation_importance(rf, X_test, y_test, scoring="balanced_accuracy", n_repeats=20,
                                  random_state=RANDOM_STATE, n_jobs=-1)
    importance = pd.DataFrame({"cecha": X.columns, "gini": rf.feature_importances_,
                               "permutacyjna": perm.importances_mean, "permutacyjna_std": perm.importances_std}
                              ).round(4).sort_values("permutacyjna", ascending=False)
    tree_importance = pd.DataFrame({"cecha": X.columns, "gini": tree.best_estimator_.feature_importances_}
                                   ).round(4).sort_values("gini", ascending=False)

    report = {
        "klasy": {"progi_pm10": config.PM10_CLASS_BINS, "etykiety": LABELS},
        "podzial": {"train": f"{config.YEARS[0]}–{TEST_YEAR - 1}", "test": str(TEST_YEAR),
                    "n_train": int(train.sum()), "n_test": int((~train).sum()),
                    "rozklad_train": y_train.value_counts().reindex(LABELS).fillna(0).astype(int).to_dict(),
                    "rozklad_test": y_test.value_counts().reindex(LABELS).fillna(0).astype(int).to_dict()},
        "selekcja_cech": selection,
        "cechy": list(X.columns),
        "modele": results,
        "waznosc_cech_rf": importance.to_dict("records"),
        "waznosc_cech_drzewo": tree_importance.to_dict("records"),
    }
    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    (config.DATA_PROCESSED / "model_raport.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    metrics = pd.DataFrame([{k: r.get(k) for k in ["model", "accuracy", "balanced_accuracy", "f1_macro",
                                                    "cv_balanced_accuracy"]}
                            | {"parametry": json.dumps(r.get("parametry"))}
                            for r in results])
    with sqlite3.connect(config.DB_PATH) as con:
        metrics.to_sql("model_metryki", con, if_exists="replace", index=False)
        importance.to_sql("model_waznosc_cech", con, if_exists="replace", index=False)
    con.close()

    plot_confusion(results)
    plot_importance(importance)
    for r in results:
        print(f"  {r['model']}: accuracy {r['accuracy']}, balanced acc. {r['balanced_accuracy']}, F1 macro {r['f1_macro']}")
    return report


if __name__ == "__main__":
    run()
