import sqlite3

import numpy as np
import pandas as pd

from src import config, quality

# --- 1. GIOŚ: pomiary godzinowe ------------------------------------------------------------------

def gios_hourly(pm10: pd.DataFrame, pm25: pd.DataFrame) -> pd.DataFrame:
    """Godzinowe PM10 i PM2.5 w jednej tabeli (format długi)."""
    hourly = pd.concat([pm10, pm25], ignore_index=True)
    # Znaczniki w pliku 2025 mają szum rzędu milisekund (np. 03:00:00.005)
    hourly["czas"] = hourly["czas"].dt.round("h")
    return hourly


# --- 3. GIOŚ: średnie dobowe ---------------------------------------------------------------------

def gios_daily(hourly: pd.DataFrame) -> pd.DataFrame:
    """Godzinowe PM10/PM2.5 (po kontroli jakości) → średnie dobowe z kryterium kompletności."""
    hourly = hourly[hourly["wartosc"] >= 0].copy()  # bez braków (NaN), w tym wartości usuniętych przy kontroli
    hourly["data"] = (hourly["czas"] - pd.Timedelta(hours=1)).dt.strftime("%Y-%m-%d")
    stats = hourly.groupby(["data", "kod_stacji", "wskaznik"])["wartosc"].agg(["mean", "count"])
    stats.loc[stats["count"] < config.MIN_HOURS_PER_DAY, "mean"] = np.nan

    wide = stats.unstack("wskaznik")
    wide.columns = [f"{'pm' if stat == 'mean' else 'n_godz_pm'}{pollutant.replace('PM', '').lower()}"
                    for stat, pollutant in wide.columns]
    wide = wide.reset_index()

    # Pełna siatka dni × stacji: doby bez żadnego pomiaru też są widoczne jako braki
    days = pd.date_range(f"{config.YEARS[0]}-01-01", f"{config.YEARS[-1]}-12-31").strftime("%Y-%m-%d")
    grid = pd.MultiIndex.from_product([days, sorted(wide["kod_stacji"].unique())],
                                      names=["data", "kod_stacji"]).to_frame(index=False)
    out = grid.merge(wide, on=["data", "kod_stacji"], how="left")
    for col in ["pm25", "n_godz_pm25"]:
        if col not in out:
            out[col] = np.nan
    for col in ["n_godz_pm10", "n_godz_pm25"]:
        out[col] = out[col].fillna(0).astype(int)
    out[["pm10", "pm25"]] = out[["pm10", "pm25"]].round(2)
    return out[["data", "kod_stacji", "pm10", "pm25", "n_godz_pm10", "n_godz_pm25"]]


# --- 4. IMGW: pogoda dobowa ----------------------------------------------------------------------

def imgw_code(nsp: pd.Series) -> pd.Series:
    """Pełny kod stacji NSP (np. 350220580) → kod 3-cyfrowy z nazw plików (580)."""
    return nsp.astype(str).str[-3:].astype(int)


def mask_missing(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Status 8 = brak pomiaru → NaN (IMGW wpisuje wtedy czasem 0.0 zamiast pustego pola)."""
    for col in cols:
        df.loc[df["W" + col] == 8, col] = np.nan
    return df


def precipitation(h: pd.DataFrame) -> pd.DataFrame:
    """Opad za dobę 00–24 UTC: suma 4 terminów 6-godzinnych (06, 12, 18 dnia D i 00 dnia D+1)."""
    p = h[h["GG"].isin([0, 6, 12, 18])].copy()
    p.loc[p["WWO6G"] == 9, "WO6G"] = 0.0     # status 9 = brak zjawiska → 0 mm
    p.loc[p["WWO6G"] == 8, "WO6G"] = np.nan  # status 8 = brak pomiaru
    end = pd.to_datetime(dict(year=p["ROK"], month=p["MC"], day=p["DZ"], hour=p["GG"]))
    p["data"] = (end - pd.Timedelta(hours=6)).dt.strftime("%Y-%m-%d")  # termin = koniec okresu 6 h
    sums = p.groupby(["data", "imgw_kod"])["WO6G"].agg(["sum", "count"]).reset_index()
    sums["opad"] = sums["sum"].where(sums["count"] == 4)  # tylko pełne doby
    return sums[["data", "imgw_kod", "opad"]]


def imgw_daily(hourly: pd.DataFrame) -> pd.DataFrame:
    """Pogoda dobowa z terminów godzinowych: średnie za dobę CET i opad z sum 6-godzinnych."""
    h = mask_missing(hourly.copy(), ["TEMP", "FWR", "WLGW", "PPPS"])
    h["imgw_kod"] = imgw_code(h["NSP"])
    utc = pd.to_datetime(dict(year=h["ROK"], month=h["MC"], day=h["DZ"], hour=h["GG"]))
    h["data"] = (utc + pd.Timedelta(hours=1)).dt.strftime("%Y-%m-%d")  # UTC → CET
    daily = h.groupby(["data", "imgw_kod"]).agg(
        temp_sr=("TEMP", "mean"), wiatr_sr=("FWR", "mean"), wilgotnosc=("WLGW", "mean"),
        cisnienie=("PPPS", "mean"), n_godz=("FWR", "count")).reset_index()
    daily.loc[daily["n_godz"] < config.MIN_HOURS_PER_DAY, ["temp_sr", "wiatr_sr", "wilgotnosc", "cisnienie"]] = np.nan

    out = daily.merge(precipitation(h), on=["data", "imgw_kod"], how="left")
    # Doby spoza zakresu lat (np. 1 stycznia po przesunięciu na CET) są pomijane
    out = out[out["data"].between(f"{config.YEARS[0]}-01-01", f"{config.YEARS[-1]}-12-31")]
    out = out.round(2)
    return out[["data", "imgw_kod", "temp_sr", "opad", "wiatr_sr", "wilgotnosc", "cisnienie", "n_godz"]]


# --- 5. Stacje i najnowsze pomiary z API ---------------------------------------------------------

def stations_table(meta: pd.DataFrame, api_stations: pd.DataFrame) -> pd.DataFrame:
    """Stacja GIOŚ → miasto → stacja IMGW, plus nazwa, typ, współrzędne i identyfikator w API."""
    rows = [{"kod_stacji": c["gios"], "miasto": city, "imgw_kod": c["imgw"]} for city, c in config.CITIES.items()]
    df = pd.DataFrame(rows).merge(
        meta.rename(columns={"Kod stacji": "kod_stacji", "Nazwa stacji": "nazwa", "Typ stacji": "typ_stacji",
                             "Typ obszaru": "typ_obszaru", "WGS84 φ N": "lat", "WGS84 λ E": "lon"}),
        on="kod_stacji", how="left")
    df["nazwa"] = df["nazwa"].str.strip()  # w metadanych zdarza się tabulator na początku nazwy
    api_ids = api_stations.set_index("Kod stacji")["Identyfikator stacji"]
    df["api_id"] = df["kod_stacji"].map(api_ids).astype("Int64")
    return df[["kod_stacji", "miasto", "imgw_kod", "nazwa", "typ_stacji", "typ_obszaru", "lat", "lon", "api_id"]]


def current_table(current: pd.DataFrame) -> pd.DataFrame:
    """Najnowsze pomiary z API GIOŚ (bez pustych wartości)."""
    df = current.dropna(subset=["wartosc"]).copy()
    df["czas"] = df["czas"].dt.strftime("%Y-%m-%d %H:%M")
    return df[["czas", "kod_stacji", "wskaznik", "wartosc"]]


# --- 6. Baza SQLite ------------------------------------------------------------------------------

SCHEMA = """
DROP VIEW IF EXISTS dzien_miasto;
DROP VIEW IF EXISTS dzien_miasto_pelny;
DROP TABLE IF EXISTS dni_kompletne;
DROP TABLE IF EXISTS filtracja_braki;
DROP TABLE IF EXISTS kontrola_pomiarow;
DROP TABLE IF EXISTS kontrola_flagi;
DROP TABLE IF EXISTS przyklad_doby;
DROP TABLE IF EXISTS stacje;
DROP TABLE IF EXISTS pm_dobowe;
DROP TABLE IF EXISTS pogoda_dobowa;
DROP TABLE IF EXISTS pm_biezace;

CREATE TABLE stacje (
    kod_stacji TEXT PRIMARY KEY,
    miasto TEXT NOT NULL,
    imgw_kod INTEGER,
    nazwa TEXT, typ_stacji TEXT, typ_obszaru TEXT,
    lat REAL, lon REAL,
    api_id INTEGER                     -- identyfikator stacji w API GIOŚ
);
CREATE TABLE pm_dobowe (
    data TEXT NOT NULL,
    kod_stacji TEXT NOT NULL REFERENCES stacje(kod_stacji),
    pm10 REAL, pm25 REAL,              -- średnie dobowe [µg/m3]; NULL przy < MIN_HOURS_PER_DAY
    n_godz_pm10 INTEGER, n_godz_pm25 INTEGER,
    PRIMARY KEY (data, kod_stacji)
);
CREATE TABLE pogoda_dobowa (
    data TEXT NOT NULL,
    imgw_kod INTEGER NOT NULL,
    temp_sr REAL,                                 -- [°C]
    opad REAL,                                    -- suma za dobę 00–24 UTC [mm]
    wiatr_sr REAL,                                -- [m/s]
    wilgotnosc REAL,                              -- [%]
    cisnienie REAL,                               -- na poziomie stacji [hPa]
    n_godz INTEGER,                               -- liczba terminów z pomiarem wiatru
    PRIMARY KEY (data, imgw_kod)
);
CREATE TABLE pm_biezace (
    czas TEXT NOT NULL, kod_stacji TEXT NOT NULL, wskaznik TEXT NOT NULL, wartosc REAL,
    PRIMARY KEY (czas, kod_stacji, wskaznik)
);

-- Wszystkie dni: stacja miasta + pogoda z przypisanej stacji IMGW + sezon + klasa dnia (przed filtracją)
CREATE VIEW dzien_miasto_pelny AS
SELECT s.miasto, p.data, p.pm10, p.pm25,
       CASE WHEN CAST(substr(p.data, 6, 2) AS INTEGER) IN ({heating}) THEN 'grzewczy' ELSE 'letni' END AS sezon,
       CASE WHEN p.pm10 IS NULL THEN NULL
            WHEN p.pm10 <= {b0} THEN '{l0}'
            WHEN p.pm10 <= {b1} THEN '{l1}'
            ELSE '{l2}' END AS klasa,
       w.temp_sr, w.opad, w.wiatr_sr, w.wilgotnosc, w.cisnienie
FROM pm_dobowe p
JOIN stacje s ON s.kod_stacji = p.kod_stacji
LEFT JOIN pogoda_dobowa w ON w.data = p.data AND w.imgw_kod = s.imgw_kod;
""".format(heating=", ".join(map(str, config.HEATING_MONTHS)),
           b0=config.PM10_CLASS_BINS[0], b1=config.PM10_CLASS_BINS[1],
           l0=config.PM10_CLASS_LABELS[0], l1=config.PM10_CLASS_LABELS[1], l2=config.PM10_CLASS_LABELS[2])


# --- 7. Filtracja: tylko dni pełne we wszystkich miastach --------------------------------------

FILTER_COLUMNS = ["pm10", "pm25", "temp_sr", "opad", "wiatr_sr", "wilgotnosc", "cisnienie"]

FILTER_SQL = """
-- Dzień zostaje tylko wtedy, gdy każde miasto ma PM10, PM2.5 i wszystkie zmienne pogodowe
CREATE TABLE dni_kompletne AS
SELECT data FROM dzien_miasto_pelny
GROUP BY data
HAVING COUNT(*) = (SELECT COUNT(*) FROM stacje)
   AND SUM({missing}) = 0
ORDER BY data;

-- Dane do analizy (analiza, testy, klasteryzacja): wyłącznie dni pełne
CREATE VIEW dzien_miasto AS
SELECT d.* FROM dzien_miasto_pelny d JOIN dni_kompletne k USING (data);
""".format(missing=" OR ".join(f"{col} IS NULL" for col in FILTER_COLUMNS))


def filter_complete_days(con: sqlite3.Connection) -> None:
    """Usuwa dni, w których w którymkolwiek mieście brakuje pomiaru, i zapisuje powody (tabela filtracja_braki)."""
    con.executescript(FILTER_SQL)
    all_days = pd.read_sql("SELECT miasto, data, " + ", ".join(FILTER_COLUMNS) + " FROM dzien_miasto_pelny", con)
    long = all_days.melt(id_vars=["miasto", "data"], var_name="zmienna", value_name="wartosc")
    missing = (long[long["wartosc"].isna()].groupby(["miasto", "zmienna"])["data"].nunique()
               .rename("dni").reset_index())
    missing.to_sql("filtracja_braki", con, index=False)

    n_all = all_days["data"].nunique()
    n_complete = con.execute("SELECT COUNT(*) FROM dni_kompletne").fetchone()[0]
    print(f"  filtracja: {n_all} dni kalendarzowych -> {n_complete} kompletnych "
          f"(usunięto {n_all - n_complete} dni z brakiem pomiaru w którymkolwiek mieście)")


# --- Cały etap -----------------------------------------------------------------------------------

def run(local: dict[str, pd.DataFrame], api: dict[str, pd.DataFrame]) -> None:
    """Czyści dane z etapów TK02/TK03 i zapisuje je do db/air.sqlite."""
    raw = gios_hourly(local["gios_pm10"], local["gios_pm25"])               # 1
    checked = quality.check(raw)                                              # 2
    flags = checked["flags"]
    tables = {
        "stacje": stations_table(local["gios_stations"], api["api_stations"]),  # 5
        "kontrola_pomiarow": checked["summary"],
        "kontrola_flagi": flags.assign(czas=flags["czas"].dt.strftime("%Y-%m-%d %H:%M")),
        "przyklad_doby": checked["example_day"],
        "pm_dobowe": gios_daily(checked["hourly"]),                           # 3
        "pogoda_dobowa": imgw_daily(local["imgw_hourly"]),                    # 4
        "pm_biezace": current_table(api["api_current"]),
    }
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.DB_PATH) as con:                              # 6
        con.executescript(SCHEMA)
        for name, df in tables.items():
            df.to_sql(name, con, if_exists="append", index=False)
            print(f"  {name}: {len(df)} wierszy")
        filter_complete_days(con)                                             # 7
    con.close()
