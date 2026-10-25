"""TK04 – kontrola jakości pomiarów godzinowych PM10 i PM2.5 (przed liczeniem średnich dobowych).

Dwa kroki:
1. Wartości niemożliwe – stężenie < 0 albo PM2.5 > PM10 w tej samej godzinie na tej samej
   stacji (PM2.5 jest częścią PM10). Takie wartości zamieniamy na brak.
2. Błędy czujników – wysoka wartość może być smogiem albo awarią. Powyżej ERROR_THRESHOLD
   sprawdzamy dwa warunki:
   a) przestrzenny: wartość > ERROR_FACTOR × mediana pozostałych stacji w tej samej godzinie,
   b) czasowy:      wartość > ERROR_FACTOR × średnia z godziny przed i po.
   Oba naraz = izolowany skok na jednej stacji, czyli błąd – usuwamy.
   Tylko jeden = oznaczenie, wartość zostaje (lokalne źródło albo narastający smog).

Funkcja check() wykonuje oba kroki i zwraca oczyszczone dane oraz tabele z wynikami.
"""
import numpy as np
import pandas as pd

from src import config

ERROR_THRESHOLD = 30  # µg/m3 – niżej to zwykły szum przy czystym powietrzu (6 przy medianie 2 to też „3 razy więcej”)
ERROR_FACTOR = 3      # „ponad 3 razy więcej niż odniesienie”


def validate_range(hourly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Krok 1: wartości niemożliwe fizycznie → brak. Zwraca dane i liczbę takich wartości na stację."""
    h = hourly.copy()
    negative = h["wartosc"] < 0
    h.loc[negative, "wartosc"] = np.nan

    # PM2.5 > PM10 w tej samej godzinie: nie wiadomo, który pomiar jest zły, więc brakiem stają się oba
    both = h.pivot_table(index=["czas", "kod_stacji"], columns="wskaznik", values="wartosc").dropna()
    impossible = both.index[both["PM25"] > both["PM10"]]
    hour_and_station = pd.MultiIndex.from_frame(h[["czas", "kod_stacji"]])
    h.loc[hour_and_station.isin(impossible), "wartosc"] = np.nan

    summary = (h.assign(ujemne=negative)
               .groupby(["kod_stacji", "wskaznik"])["ujemne"].sum().astype(int).reset_index())
    impossible_per_station = pd.Series(impossible.get_level_values("kod_stacji")).value_counts()
    summary["pm25_ponad_pm10"] = summary["kod_stacji"].map(impossible_per_station).fillna(0).astype(int)
    return h, summary


def detect_sensor_errors(hourly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Krok 2: błędy czujników. Zwraca dane bez błędów i tabelę wszystkich oznaczonych godzin."""
    parts = []
    for pollutant, g in hourly.groupby("wskaznik"):
        wide = g.pivot(index="czas", columns="kod_stacji", values="wartosc")
        wide = wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq="h"))  # ciągła oś godzin
        for station in wide.columns:
            value, others = wide[station], wide.drop(columns=station)
            # Każde miasto ma jedną stację, więc odniesieniem są stacje w pozostałych miastach (co najmniej 2)
            others_median = others.median(axis=1).where(others.notna().sum(axis=1) >= 2)
            before, after = value.shift(1), value.shift(-1)
            high = value > ERROR_THRESHOLD
            spatial = high & (value > ERROR_FACTOR * others_median)
            temporal = high & (value > ERROR_FACTOR * (before + after) / 2)
            flagged = spatial | temporal
            parts.append(pd.DataFrame({
                "czas": value.index[flagged], "kod_stacji": station, "wskaznik": pollutant,
                "wartosc": value[flagged].values, "mediana_innych": others_median[flagged].values,
                "przed": before[flagged].values, "po": after[flagged].values,
                "przestrzenne": spatial[flagged].values, "czasowe": temporal[flagged].values,
            }))
    flags = pd.concat(parts, ignore_index=True)
    flags["decyzja"] = np.select([flags["przestrzenne"] & flags["czasowe"], flags["przestrzenne"]],
                                 ["usunięta", "odstaje od innych stacji"], default="skok w czasie")

    removed = pd.MultiIndex.from_frame(flags.loc[flags["decyzja"] == "usunięta", ["czas", "kod_stacji", "wskaznik"]])
    h = hourly.copy()
    h.loc[pd.MultiIndex.from_frame(h[["czas", "kod_stacji", "wskaznik"]]).isin(removed), "wartosc"] = np.nan
    return h, flags


def summarize(hourly: pd.DataFrame, range_check: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    """Wynik kontroli dla każdej stacji i wskaźnika (tabela kontrola_pomiarow)."""
    decisions = flags.pivot_table(index=["kod_stacji", "wskaznik"], columns="decyzja", values="wartosc",
                                  aggfunc="size", fill_value=0)
    decisions = decisions.reindex(columns=["usunięta", "odstaje od innych stacji", "skok w czasie"], fill_value=0)
    decisions.columns = ["usuniete", "odstaje_od_innych", "skok_w_czasie"]
    hours_left = (hourly.dropna(subset=["wartosc"]).groupby(["kod_stacji", "wskaznik"]).size()
                  .rename("godzin_po_kontroli"))
    out = (range_check.set_index(["kod_stacji", "wskaznik"]).join(hours_left).join(decisions)
           .fillna(0).astype(int).reset_index())
    out.insert(0, "miasto", out["kod_stacji"].map({c["gios"]: city for city, c in config.CITIES.items()}))
    return out


def example_day(raw: pd.DataFrame, cleaned: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    """24 godziny doby z najwyższą usuniętą wartością, przed i po kontroli (wykres na stronie)."""
    removed = flags[flags["decyzja"] == "usunięta"]
    if removed.empty:
        return pd.DataFrame(columns=["data", "kod_stacji", "wskaznik", "godzina", "przed", "po"])
    worst = removed.loc[removed["wartosc"].idxmax()]
    day = (worst["czas"] - pd.Timedelta(hours=1)).normalize()
    hours = pd.date_range(day + pd.Timedelta(hours=1), periods=24, freq="h")  # 01:00 D … 00:00 D+1

    def values_of(h: pd.DataFrame) -> np.ndarray:
        same_series = (h["kod_stacji"] == worst["kod_stacji"]) & (h["wskaznik"] == worst["wskaznik"])
        return h[same_series].set_index("czas")["wartosc"].reindex(hours).values

    return pd.DataFrame({"data": day.strftime("%Y-%m-%d"), "kod_stacji": worst["kod_stacji"],
                         "wskaznik": worst["wskaznik"], "godzina": range(1, 25),
                         "przed": values_of(raw), "po": values_of(cleaned)})


def check(raw: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Cała kontrola jakości: oba kroki, podsumowanie i przykładowa doba."""
    hourly, range_check = validate_range(raw)
    hourly, flags = detect_sensor_errors(hourly)
    summary = summarize(hourly, range_check, flags)

    impossible_hours = summary.loc[summary["wskaznik"] == "PM25", "pm25_ponad_pm10"].sum()
    print(f"  kontrola jakości: ujemne {summary['ujemne'].sum()}, godzin z PM2.5 > PM10 {impossible_hours}, "
          f"usunięte błędy {summary['usuniete'].sum()}, oznaczone i zostawione "
          f"{summary['odstaje_od_innych'].sum() + summary['skok_w_czasie'].sum()}")
    return {"hourly": hourly, "summary": summary, "flags": flags, "example_day": example_day(raw, hourly, flags)}
