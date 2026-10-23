"""TK04 – czyszczenie, łączenie, agregacja godzinowa→dobowa, zapis do SQLite.

Konwencje czasu:
- GIOŚ (archiwum): czas CET (UTC+1, bez czasu letniego), znacznik oznacza koniec
  godziny uśredniania – wartość z 01:00 dotyczy okresu 00:00–01:00. Doba D to
  godziny 01:00 dnia D … 00:00 dnia D+1, więc data = (czas − 1 h).
- IMGW (terminy synoptyczne): czas UTC; przeliczany na CET (+1 h), żeby doby
  meteorologiczne pokrywały się z dobami pomiarów pyłu. Opad pochodzi z sum
  6-godzinnych (WO6G) za dobę 00–24 UTC – dobowy opad IMGW (SMDB) obejmuje
  06 UTC D → 06 UTC D+1, czyli w 1/4 następny dzień, więc nie jest używany.
- GIOŚ API (dane bieżące): czas lokalny (zapisywany bez zmian).
"""
import sqlite3

import numpy as np
import pandas as pd

from src import config

SCHEMA = """
DROP VIEW IF EXISTS dzien_miasto;
DROP VIEW IF EXISTS dzien_miasto_pelny;
DROP TABLE IF EXISTS dni_kompletne;
DROP TABLE IF EXISTS filtracja_braki;
DROP TABLE IF EXISTS kontrola_pomiarow;
DROP TABLE IF EXISTS kontrola_flagi;
DROP TABLE IF EXISTS przyklad_doby;
DROP TABLE IF EXISTS stacje;
DROP TABLE IF EXISTS stacje_meteo;
DROP TABLE IF EXISTS pm_dobowe;
DROP TABLE IF EXISTS pogoda_dobowa;
DROP TABLE IF EXISTS gus_powiaty;
DROP TABLE IF EXISTS pm_biezace;

CREATE TABLE stacje_meteo (
    imgw_kod INTEGER PRIMARY KEY,
    nsp TEXT,
    nazwa TEXT
);
CREATE TABLE stacje (
    kod_stacji TEXT PRIMARY KEY,
    miasto TEXT NOT NULL,
    imgw_kod INTEGER REFERENCES stacje_meteo(imgw_kod),
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
    imgw_kod INTEGER NOT NULL REFERENCES stacje_meteo(imgw_kod),
    temp_sr REAL,                                 -- [°C]
    opad REAL,                                    -- suma za dobę 00–24 UTC [mm]
    wiatr_sr REAL,                                -- [m/s]
    wilgotnosc REAL,                              -- [%]
    cisnienie REAL,                               -- na poziomie stacji [hPa]
    n_godz INTEGER,                               -- liczba terminów z pomiarem wiatru
    PRIMARY KEY (data, imgw_kod)
);
CREATE TABLE gus_powiaty (
    miasto TEXT NOT NULL, jednostka TEXT, unit_id TEXT, rok INTEGER NOT NULL,
    ludnosc REAL, samochody_osobowe REAL, samochody_na_1000 REAL, gestosc_zaludnienia REAL,
    PRIMARY KEY (miasto, rok)
);
CREATE TABLE pm_biezace (
    czas TEXT NOT NULL, kod_stacji TEXT NOT NULL, wskaznik TEXT NOT NULL, wartosc REAL,
    PRIMARY KEY (czas, kod_stacji, wskaznik)
);

-- Wszystkie dni: stacja miasta + pogoda z przypisanej stacji IMGW (przed filtracją)
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


# --- GIOŚ ---------------------------------------------------------------------

def gios_hourly(pm10: pd.DataFrame, pm25: pd.DataFrame) -> pd.DataFrame:
    """Godzinowe PM10 i PM2.5 w jednej tabeli (format długi)."""
    hourly = pd.concat([pm10, pm25], ignore_index=True)
    # Znaczniki w pliku 2025 mają szum rzędu milisekund (np. 03:00:00.005)
    hourly["czas"] = hourly["czas"].dt.round("h")
    return hourly


# --- Kontrola jakości pomiarów godzinowych (przed agregacją do dób) -------------

PROG_BLEDU = 30       # µg/m3 – poniżej kryteria nie działają (6 przy medianie 2 to szum przy czystym powietrzu)
KROTNOSC_BLEDU = 3    # „ponad 3 × odniesienie”


def waliduj_zakres(hourly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Krok 1 kontroli – wartości niemożliwe fizycznie zamieniane na brak.

    Stężenie < 0 oraz PM2.5 > PM10 na tej samej stacji w tej samej godzinie (PM2.5 jest częścią PM10;
    nie wiadomo, który pomiar jest błędny, więc brakiem stają się oba).
    """
    h = hourly.copy()
    ujemne = h["wartosc"] < 0
    h.loc[ujemne, "wartosc"] = np.nan
    oba = h.pivot_table(index=["czas", "kod_stacji"], columns="wskaznik", values="wartosc").dropna()
    niemozliwe = oba.index[oba["PM25"] > oba["PM10"]]
    h.loc[pd.MultiIndex.from_frame(h[["czas", "kod_stacji"]]).isin(niemozliwe), "wartosc"] = np.nan
    wynik = (h.assign(ujemne=ujemne).groupby(["kod_stacji", "wskaznik"])["ujemne"].sum().astype(int).reset_index()
             .assign(pm25_ponad_pm10=lambda d: d["kod_stacji"].map(
                 pd.Series(niemozliwe.get_level_values("kod_stacji")).value_counts()).fillna(0).astype(int)))
    return h, wynik


def wykryj_bledy(hourly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Krok 2 kontroli – błędy pomiarowe: odróżnienie awarii czujnika od prawdziwego smogu.

    Dla każdej godziny i stacji z wartością > PROG_BLEDU sprawdzamy dwa niezależne kryteria:
    a) przestrzenne – wartość > 3 × mediana pozostałych stacji projektu w tej samej godzinie
       (co najmniej 2 stacje odniesienia; projekt ma jedną stację na miasto, więc odniesieniem są inne miasta),
    b) czasowe – wartość > 3 × średnia z godziny przed i po na tej samej stacji.
    Oba naraz = izolowany skok na jednej stacji, czyli błąd: wartość usuwana.
    Tylko jedno = flaga, wartość zostaje (może to być lokalne źródło albo narastający epizod smogowy).
    """
    czesci = []
    for wsk, g in hourly.groupby("wskaznik"):
        szer = g.pivot(index="czas", columns="kod_stacji", values="wartosc")
        szer = szer.reindex(pd.date_range(szer.index.min(), szer.index.max(), freq="h"))  # ciągła oś godzin
        for st in szer.columns:
            v, inne = szer[st], szer.drop(columns=st)
            mediana = inne.median(axis=1).where(inne.notna().sum(axis=1) >= 2)
            przed, po = v.shift(1), v.shift(-1)
            a = (v > PROG_BLEDU) & (v > KROTNOSC_BLEDU * mediana)
            b = (v > PROG_BLEDU) & (v > KROTNOSC_BLEDU * (przed + po) / 2)
            m = a | b
            czesci.append(pd.DataFrame({"czas": v.index[m], "kod_stacji": st, "wskaznik": wsk, "wartosc": v[m].values,
                                        "mediana_innych": mediana[m].values, "przed": przed[m].values, "po": po[m].values,
                                        "przestrzenne": a[m].values, "czasowe": b[m].values}))
    flagi = pd.concat(czesci, ignore_index=True)
    flagi["decyzja"] = np.select([flagi["przestrzenne"] & flagi["czasowe"], flagi["przestrzenne"]],
                                 ["usunięta", "odstaje od innych stacji"], default="skok w czasie")
    usun = pd.MultiIndex.from_frame(flagi.loc[flagi["decyzja"] == "usunięta", ["czas", "kod_stacji", "wskaznik"]])
    h = hourly.copy()
    h.loc[pd.MultiIndex.from_frame(h[["czas", "kod_stacji", "wskaznik"]]).isin(usun), "wartosc"] = np.nan
    return h, flagi


def kontrola_pomiarow(hourly: pd.DataFrame, walidacja: pd.DataFrame, flagi: pd.DataFrame) -> pd.DataFrame:
    """Podsumowanie kontroli jakości: stacja × wskaźnik (do bazy, strony i README)."""
    decyzje = flagi.pivot_table(index=["kod_stacji", "wskaznik"], columns="decyzja", values="wartosc",
                                aggfunc="size", fill_value=0)
    decyzje = decyzje.reindex(columns=["usunięta", "odstaje od innych stacji", "skok w czasie"], fill_value=0)
    decyzje.columns = ["usuniete", "odstaje_od_innych", "skok_w_czasie"]
    godziny = hourly.dropna(subset=["wartosc"]).groupby(["kod_stacji", "wskaznik"]).size().rename("godzin_po_kontroli")
    out = walidacja.set_index(["kod_stacji", "wskaznik"]).join(godziny).join(decyzje).fillna(0).astype(int).reset_index()
    out.insert(0, "miasto", out["kod_stacji"].map({c["gios"]: m for m, c in config.CITIES.items()}))
    return out


def przyklad_doby(surowe: pd.DataFrame, po_kontroli: pd.DataFrame, flagi: pd.DataFrame) -> pd.DataFrame:
    """24 godziny jednej doby przed i po kontroli – doba z najwyższą usuniętą wartością (do panelu na stronie)."""
    usuniete = flagi[flagi["decyzja"] == "usunięta"]
    if usuniete.empty:
        return pd.DataFrame(columns=["data", "kod_stacji", "wskaznik", "godzina", "przed", "po"])
    wzor = usuniete.loc[usuniete["wartosc"].idxmax()]
    doba = (wzor["czas"] - pd.Timedelta(hours=1)).normalize()
    godziny = pd.date_range(doba + pd.Timedelta(hours=1), periods=24, freq="h")  # 01:00 D … 00:00 D+1
    wybierz = lambda h: (h[(h["kod_stacji"] == wzor["kod_stacji"]) & (h["wskaznik"] == wzor["wskaznik"])]  # noqa: E731
                         .set_index("czas")["wartosc"].reindex(godziny))
    return pd.DataFrame({"data": doba.strftime("%Y-%m-%d"), "kod_stacji": wzor["kod_stacji"], "wskaznik": wzor["wskaznik"],
                         "godzina": range(1, 25), "przed": wybierz(surowe).values, "po": wybierz(po_kontroli).values})


def gios_daily(hourly: pd.DataFrame) -> pd.DataFrame:
    """Godzinowe PM10/PM2.5 (po kontroli jakości) → średnie dobowe z kryterium kompletności."""
    hourly = hourly[hourly["wartosc"] >= 0].copy()  # odrzuca braki (NaN), w tym usunięte przy kontroli
    hourly["data"] = (hourly["czas"] - pd.Timedelta(hours=1)).dt.strftime("%Y-%m-%d")
    agg = hourly.groupby(["data", "kod_stacji", "wskaznik"])["wartosc"].agg(["mean", "count"])
    agg.loc[agg["count"] < config.MIN_HOURS_PER_DAY, "mean"] = np.nan
    wide = agg.unstack("wskaznik")
    wide.columns = [f"{'pm' if s == 'mean' else 'n_godz_pm'}{w.replace('PM', '').lower()}"
                    for s, w in wide.columns]
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


def stations_table(meta: pd.DataFrame, api_stations: pd.DataFrame) -> pd.DataFrame:
    """Mapowanie stacja GIOŚ → miasto → stacja meteo IMGW (+ współrzędne, id w API)."""
    rows = [{"kod_stacji": c["gios"], "miasto": city, "imgw_kod": c["imgw"]} for city, c in config.CITIES.items()]
    df = pd.DataFrame(rows).merge(
        meta.rename(columns={"Kod stacji": "kod_stacji", "Nazwa stacji": "nazwa", "Typ stacji": "typ_stacji",
                             "Typ obszaru": "typ_obszaru", "WGS84 φ N": "lat", "WGS84 λ E": "lon"}),
        on="kod_stacji", how="left")
    df["nazwa"] = df["nazwa"].str.strip()  # w metadanych zdarza się wiodący tabulator
    api_ids = api_stations.set_index("Kod stacji")["Identyfikator stacji"]
    df["api_id"] = df["kod_stacji"].map(api_ids).astype("Int64")
    return df[["kod_stacji", "miasto", "imgw_kod", "nazwa", "typ_stacji", "typ_obszaru",
               "lat", "lon", "api_id"]]


# --- IMGW ---------------------------------------------------------------------

def _imgw_code(nsp: pd.Series) -> pd.Series:
    """Pełny kod NSP (np. 350220580) → kod 3-cyfrowy z nazw plików (580)."""
    return nsp.astype(str).str[-3:].astype(int)


def _mask_missing(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
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
    agg = p.groupby(["data", "imgw_kod"])["WO6G"].agg(["sum", "count"]).reset_index()
    agg["opad"] = agg["sum"].where(agg["count"] == 4)  # tylko pełne doby
    return agg[["data", "imgw_kod", "opad"]]


def imgw_daily(hourly: pd.DataFrame) -> pd.DataFrame:
    """Pogoda dobowa z terminów godzinowych: średnie za dobę CET i opad z sum 6-godzinnych."""
    h = _mask_missing(hourly.copy(), ["TEMP", "FWR", "WLGW", "PPPS"])
    h["imgw_kod"] = _imgw_code(h["NSP"])
    utc = pd.to_datetime(dict(year=h["ROK"], month=h["MC"], day=h["DZ"], hour=h["GG"]))
    h["data"] = (utc + pd.Timedelta(hours=1)).dt.strftime("%Y-%m-%d")  # UTC → CET
    hd = h.groupby(["data", "imgw_kod"]).agg(
        temp_sr=("TEMP", "mean"), wiatr_sr=("FWR", "mean"), wilgotnosc=("WLGW", "mean"),
        cisnienie=("PPPS", "mean"), n_godz=("FWR", "count")).reset_index()
    # Doby z niekompletnymi terminami: średnie tylko przy >= 18 pomiarach
    hd.loc[hd["n_godz"] < config.MIN_HOURS_PER_DAY, ["temp_sr", "wiatr_sr", "wilgotnosc", "cisnienie"]] = np.nan

    out = hd.merge(precipitation(h), on=["data", "imgw_kod"], how="left")
    # Doby spoza zakresu lat (np. 1 stycznia po przesunięciu na CET) są pomijane
    out = out[out["data"].between(f"{config.YEARS[0]}-01-01", f"{config.YEARS[-1]}-12-31")]
    out = out.round(2)
    return out[["data", "imgw_kod", "temp_sr", "opad", "wiatr_sr", "wilgotnosc", "cisnienie", "n_godz"]]


def meteo_stations(hourly: pd.DataFrame) -> pd.DataFrame:
    df = hourly[["NSP"]].drop_duplicates().assign(imgw_kod=lambda x: _imgw_code(x["NSP"]))
    df["nazwa"] = df["imgw_kod"].map(config.IMGW_STATIONS)
    return df.rename(columns={"NSP": "nsp"})[["imgw_kod", "nsp", "nazwa"]]


# --- Filtracja: tylko dni kompletne we wszystkich miastach ----------------------

ZMIENNE_FILTRA = ["pm10", "pm25", "temp_sr", "opad", "wiatr_sr", "wilgotnosc", "cisnienie"]

FILTRACJA = """
-- Dzień zostaje tylko wtedy, gdy każde miasto ma PM10, PM2.5 i wszystkie zmienne pogodowe
CREATE TABLE dni_kompletne AS
SELECT data FROM dzien_miasto_pelny
GROUP BY data
HAVING COUNT(*) = (SELECT COUNT(*) FROM stacje)
   AND SUM({braki}) = 0
ORDER BY data;

-- Zbiór analityczny (analiza, model, klasteryzacja): wyłącznie dni kompletne
CREATE VIEW dzien_miasto AS
SELECT d.* FROM dzien_miasto_pelny d JOIN dni_kompletne k USING (data);
""".format(braki=" OR ".join(f"{z} IS NULL" for z in ZMIENNE_FILTRA))


def filtruj(con: sqlite3.Connection) -> None:
    """Usuwa dni, w których w którymkolwiek mieście brakuje pomiaru, i zapisuje przyczyny braków."""
    con.executescript(FILTRACJA)
    pelny = pd.read_sql("SELECT miasto, data, " + ", ".join(ZMIENNE_FILTRA) + " FROM dzien_miasto_pelny", con)
    dlugi = pelny.melt(id_vars=["miasto", "data"], var_name="zmienna", value_name="wartosc")
    braki = (dlugi[dlugi["wartosc"].isna()].groupby(["miasto", "zmienna"])["data"].nunique()
             .rename("dni").reset_index())
    braki.to_sql("filtracja_braki", con, index=False)
    wszystkie = pelny["data"].nunique()
    kompletne = con.execute("SELECT COUNT(*) FROM dni_kompletne").fetchone()[0]
    print(f"  filtracja: {wszystkie} dni kalendarzowych -> {kompletne} kompletnych "
          f"(usunięto {wszystkie - kompletne} dni z brakiem pomiaru w którymkolwiek mieście)")


# --- GUS i API ----------------------------------------------------------------

def gus_table(bdl: pd.DataFrame) -> pd.DataFrame:
    wide = bdl.pivot_table(index=["miasto", "jednostka", "unit_id", "rok"], columns="zmienna",
                           values="wartosc").reset_index()
    wide.columns.name = None
    return wide


def current_table(current: pd.DataFrame) -> pd.DataFrame:
    df = current.dropna(subset=["wartosc"]).copy()
    df["czas"] = df["czas"].dt.strftime("%Y-%m-%d %H:%M")
    return df[["czas", "kod_stacji", "wskaznik", "wartosc"]]


# --- Zapis --------------------------------------------------------------------

def run(local: dict[str, pd.DataFrame], api: dict[str, pd.DataFrame]) -> None:
    """Czyści dane z etapów TK02/TK03 i zapisuje je do db/air.sqlite."""
    surowe = gios_hourly(local["gios_pm10"], local["gios_pm25"])
    godzinowe, walidacja = waliduj_zakres(surowe)
    godzinowe, flagi = wykryj_bledy(godzinowe)
    kontrola = kontrola_pomiarow(godzinowe, walidacja, flagi)
    godziny_pm25_ponad = kontrola.loc[kontrola["wskaznik"] == "PM25", "pm25_ponad_pm10"].sum()
    print(f"  kontrola jakości: ujemne {kontrola['ujemne'].sum()}, godzin z PM2.5 > PM10 {godziny_pm25_ponad}, "
          f"usunięte błędy {kontrola['usuniete'].sum()}, oznaczone i zostawione "
          f"{kontrola['odstaje_od_innych'].sum() + kontrola['skok_w_czasie'].sum()}")
    tables = {
        "stacje_meteo": meteo_stations(local["imgw_hourly"]),
        "stacje": stations_table(local["gios_stations"], api["api_stations"]),
        "kontrola_pomiarow": kontrola,
        "kontrola_flagi": flagi.assign(czas=flagi["czas"].dt.strftime("%Y-%m-%d %H:%M")),
        "przyklad_doby": przyklad_doby(surowe, godzinowe, flagi),
        "pm_dobowe": gios_daily(godzinowe),
        "pogoda_dobowa": imgw_daily(local["imgw_hourly"]),
        "gus_powiaty": gus_table(api["bdl"]),
        "pm_biezace": current_table(api["api_current"]),
    }
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.DB_PATH) as con:
        con.executescript(SCHEMA)
        for name, df in tables.items():
            df.to_sql(name, con, if_exists="append", index=False)
            print(f"  {name}: {len(df)} wierszy")
        filtruj(con)
    con.close()
