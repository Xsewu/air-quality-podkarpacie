"""TK05 – eksport wyników do web/data/*.json dla strony.

Funkcje są ułożone w kolejności paneli strony: A (akwizycja), P (przygotowanie), E (eksploracja),
1 (sezony), 2 (pogoda), 3 (ranking i mapa), 4 (klasy dni), 5 (typy dni) oraz meta (wspólne dane).
Dane pochodzą z bazy SQLite i z raportu JSON klasteryzacji. Klucze w plikach JSON są
po polsku, bo czyta je strona (web/app.js).
"""
import csv
import io
import json
import math
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

import openpyxl
import pandas as pd

from src import config
from src.plots import CITY_COLORS
from src.quality import ERROR_FACTOR, ERROR_THRESHOLD


# --- Pomocnicze ----------------------------------------------------------------------------------

def to_json_safe(obj):
    """Zamienia NaN/inf na null i typy numpy na zwykłe liczby (rekurencyjnie)."""
    if isinstance(obj, dict):
        return {str(k): to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json_safe(v) for v in obj]
    if hasattr(obj, "item"):  # skalary numpy
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def write(name: str, data) -> None:
    """Zapisuje web/data/<name>; allow_nan=False gwarantuje poprawny JSON."""
    config.WEB_DATA.mkdir(parents=True, exist_ok=True)
    text = json.dumps(to_json_safe(data), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    (config.WEB_DATA / name).write_text(text, encoding="utf-8")
    print(f"  web/data/{name}: {len(text) / 1024:.1f} kB")


def records(df: pd.DataFrame) -> list[dict]:
    """Tabela jako lista słowników, z None zamiast NaN."""
    return df.astype(object).where(df.notna(), None).to_dict("records")


def thousands(n: int) -> str:
    """Liczba z odstępem tysięcy, jak w polskim zapisie (281 741)."""
    return f"{n:,}".replace(",", " ")


def change(n: int) -> str:
    """Zmiana liczby wartości w lejku: „−15”, a przy braku zmian „0”."""
    return f"−{thousands(n)}" if n else "0"


def count(con, sql: str):
    """Pierwsza wartość wyniku zapytania SQL (np. COUNT(*))."""
    return con.execute(sql).fetchone()[0]


def file_info(path: Path) -> dict:
    """Nazwa, rozmiar i data pobrania pliku."""
    stat = path.stat()
    return {"nazwa": path.name, "kB": round(stat.st_size / 1024),
            "pobrano": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")}


def pm_hour_counts(con) -> dict:
    """Pomiary godzinowe PM: wczytane z plików, odrzucone przy kontroli jakości i pozostałe."""
    k = pd.read_sql("SELECT * FROM kontrola_pomiarow", con)
    left = int(k["godzin_po_kontroli"].sum())
    impossible = int(k["ujemne"].sum() + k["pm25_ponad_pm10"].sum())
    removed = int(k["usuniete"].sum())
    return {"wczytane": left + impossible + removed, "niemozliwe": impossible, "usuniete": removed,
            "po_kontroli": left, "oznaczone": int(k["odstaje_od_innych"].sum() + k["skok_w_czasie"].sum())}


# --- Panel A: akwizycja danych -------------------------------------------------------------------

def export_acquisition(con) -> None:
    """Źródła, pobrane pliki, próbki surowych danych i liczby."""
    gios, imgw, api = config.GIOS_RAW, config.IMGW_RAW, config.API_RAW
    last_year = config.YEARS[-1]

    # GIOŚ – pierwsze wiersze arkusza XLSX (4 kolumny), tak jak w pliku
    sheet = openpyxl.load_workbook(gios / "extracted" / f"{last_year}_PM10_1g.xlsx", read_only=True).worksheets[0]
    gios_sample = [[c.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if isinstance(c, datetime) else ("" if c is None else str(c))
                    for c in row[:4]] for _, row in zip(range(9), sheet.iter_rows(values_only=True))]
    # IMGW – wybrane kolumny z pliku nagłówka i pierwszy wiersz danych (cp1250)
    header = (imgw / "s_t_naglowek.csv").read_text(encoding=config.IMGW_ENCODING).strip().split(",")
    station = next(iter(config.IMGW_STATIONS))
    with zipfile.ZipFile(imgw / "terminowe" / f"{last_year}_{station}_s.zip") as zf:
        csv_file = io.TextIOWrapper(zf.open(f"s_t_{station}_{last_year}.csv"), encoding=config.IMGW_ENCODING)
        first_row = next(csv.reader(csv_file))
    shown = [i for i, name in enumerate(header)
             if name in ("NSP", "POST", "ROK", "MC", "DZ", "GG", "KRWR", "FWR", "TEMP", "WLGW", "PPPS", "WO6G")]
    # API GIOŚ – fragmenty zapisanych odpowiedzi JSON
    api_stations = json.loads((api / "gios_stations.json").read_text(encoding="utf-8"))
    snapshots = sorted(api.glob("gios_current_*.json"))
    last_snapshot = json.loads(snapshots[-1].read_text(encoding="utf-8"))
    first_station = config.GIOS_STATIONS[0]

    gios_files = [file_info(gios / "metadane.xlsx")] + [file_info(gios / f"{y}.zip") for y in config.YEARS]
    imgw_files = [file_info(imgw / "terminowe" / f"{y}_{k}_s.zip") for y in config.YEARS for k in config.IMGW_STATIONS]
    pm_hours = pm_hour_counts(con)["wczytane"]  # tak jak w plikach – przed kontrolą jakości
    weather_hours = count(con, "SELECT SUM(n_godz) FROM pogoda_dobowa")

    def pl(n: int, few: str, many: str) -> str:
        """Polska forma liczby mnogiej: 2–4 (bez 12–14) → few, pozostałe → many."""
        return few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many

    n_gios, n_imgw = len(config.GIOS_STATIONS), len(config.IMGW_STATIONS)
    # „znacznik” – krótki format na schemacie strony, „krotko” – jedna linia „co pobieramy” na schemacie
    sources = [
        {"id": "gios", "nazwa": "GIOŚ – archiwum pomiarów", "tk": "TK02", "dostep": "plik lokalny",
         "url": "https://powietrze.gios.gov.pl/pjp/archives", "znacznik": "XLSX",
         "format": "ZIP z plikami XLSX · czas CET, znacznik = koniec godziny",
         "co": f"godzinowe PM10 i PM2.5 z lat {config.YEARS[0]}–{last_year} dla {n_gios} stacji + metadane stacji",
         "krotko": f"PM10 i PM2.5 co godzinę · {n_gios} {pl(n_gios, 'stacje', 'stacji')}",
         "liczby": [[pl(len(gios_files), "pliki", "plików"), len(gios_files)],
                    ["MB", round(sum(f["kB"] for f in gios_files) / 1024)],
                    ["ważnych pomiarów godzinowych", pm_hours]],
         "pliki": gios_files,
         "probka": {"typ": "tabela", "opis": f"{last_year}_PM10_1g.xlsx – 6 wierszy nagłówka, potem godziny; 4 z ok. 185 kolumn",
                    "wiersze": gios_sample}},
        {"id": "imgw", "nazwa": "IMGW-PIB – dane synoptyczne", "tk": "TK02", "dostep": "plik lokalny",
         "url": "https://danepubliczne.imgw.pl", "znacznik": "CSV",
         "format": "ZIP z plikami CSV bez nagłówka · kodowanie cp1250 · czas UTC",
         "co": f"terminowe (godzinowe) obserwacje z {n_imgw} stacji: temperatura, wiatr, wilgotność, ciśnienie, opad 6-godzinny",
         "krotko": f"pogoda co godzinę · {n_imgw} {pl(n_imgw, 'stacje', 'stacji')} synoptyczne",
         "liczby": [[pl(len(imgw_files), "pliki", "plików"), len(imgw_files)],
                    ["MB", round(sum(f["kB"] for f in imgw_files) / 1024, 1)],
                    ["terminów z pomiarem wiatru", weather_hours]],
         "pliki": imgw_files,
         "probka": {"typ": "tabela", "opis": f"s_t_{station}_{last_year}.csv – 12 ze {len(header)} kolumn; nazwy z pliku s_t_nagłówek.csv",
                    "wiersze": [[header[i] for i in shown], [first_row[i].strip() for i in shown]]}},
        {"id": "gios_api", "nazwa": "GIOŚ – API jakości powietrza v1", "tk": "TK03", "dostep": "zapytania HTTP",
         "url": config.GIOS_API_URL, "znacznik": "JSON-LD",
         "format": "JSON-LD (nagłówek Accept: application/ld+json) · stronicowanie page/size",
         "co": "lista stacji, stanowiska pomiarowe i bieżące odczyty PM10/PM2.5 (do 3 dób wstecz) – w panelu 3 na tle historii",
         "krotko": "lista stacji i bieżące odczyty PM10/PM2.5",
         "liczby": [["stacji w Polsce", len(api_stations)],
                    ["bieżących odczytów", count(con, "SELECT COUNT(*) FROM pm_biezace")],
                    ["zapisanych migawek", len(snapshots)]],
         "pliki": [file_info(api / "gios_stations.json"), file_info(snapshots[-1])],
         "probka": {"typ": "json", "opis": "odpowiedź station/findAll (1 stacja) i data/getData (2 odczyty)",
                    "tekst": json.dumps({"stacja": next(s for s in api_stations if s["Kod stacji"] == first_station),
                                         "odczyty": last_snapshot[first_station]["dane"]["PM10"][:2]},
                                        ensure_ascii=False, indent=2)}},
    ]
    flow = [
        ["źródła danych", "3", "2 archiwa plików + API"],
        ["surowe pliki", f"{round((sum(f['kB'] for s in sources for f in s['pliki'])) / 1024)} MB", "data/raw/"],
        ["pomiary godzinowe", thousands(pm_hours + weather_hours), "PM + pogoda, wczytane z plików"],
        # Ostatni krok to odnośnik do panelu P, w którym dane są czyszczone
        ["przygotowanie danych", "dalej →", "kontrola jakości, agregacja do dób, filtracja", "#przygotowanie"],
    ]
    write("akwizycja.json", {"zrodla": sources, "przeplyw": flow})


# --- Panel P: przygotowanie danych ---------------------------------------------------------------

def export_preparation(con) -> None:
    """Lejek (ile danych zostaje po każdym kroku), tabela kroków, kontrola jakości, przykładowa doba,
    tabele bazy i wynik filtracji."""
    hours = pm_hour_counts(con)
    n_cities = len(config.CITIES)
    in_files = count(con, "SELECT COUNT(*) FROM pm_dobowe") * 24 * 2  # pełna siatka dni × stacji, 24 h, PM10 i PM2.5
    min_h = config.MIN_HOURS_PER_DAY
    daily = pd.read_sql(f"""SELECT SUM(pm10 IS NOT NULL) pm10, SUM(pm25 IS NOT NULL) pm25,
        SUM(n_godz_pm10 BETWEEN 1 AND {min_h - 1}) + SUM(n_godz_pm25 BETWEEN 1 AND {min_h - 1}) za_malo FROM pm_dobowe""",
                        con).iloc[0]
    n_days = count(con, "SELECT COUNT(DISTINCT data) FROM dzien_miasto_pelny")
    n_city_days = count(con, "SELECT COUNT(*) FROM dzien_miasto_pelny")
    n_complete = count(con, "SELECT COUNT(*) FROM dni_kompletne")
    n_analysis = count(con, "SELECT COUNT(*) FROM dzien_miasto")
    weather_days = count(con, "SELECT COUNT(*) FROM pogoda_dobowa")
    possible_weather_days = len(config.IMGW_STATIONS) * n_days
    # Tabele z etapu czyszczenia (wyniki analiz i klastrów powstają później)
    db_objects = pd.read_sql("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
                             "AND name NOT LIKE 'wyniki_%' AND name NOT LIKE 'klastry_%' "
                             "ORDER BY type, name", con)
    db_objects["wiersze"] = [count(con, f'SELECT COUNT(*) FROM "{n}"') for n in db_objects["name"]]

    funnel = [
        ["pomiary godzinowe PM", thousands(hours["wczytane"]), f"z plików (pustych miejsc: {thousands(in_files - hours['wczytane'])})"],
        ["wartości niemożliwe", change(hours["niemozliwe"]), "ujemne albo PM2.5 > PM10"],
        ["błędy czujników", change(hours["usuniete"]), f"usunięte ({thousands(hours['oznaczone'])} oznaczonych zostaje)"],
        ["średnie dobowe", thousands(daily["pm10"]), f"dni z PM10 na stacjach (PM2.5: {thousands(daily['pm25'])})"],
        ["z pogodą", thousands(n_city_days), f"{n_cities} miasta × {n_days} dni"],
        ["po filtracji", thousands(n_analysis), f"{n_cities} miasta × {n_complete} pełnych dni"],
    ]
    # Lejek dób: pełna siatka dni-miasto, z której kolejno odpadają dni (każdy dzień liczony raz, przy pierwszym powodzie)
    full = "pm10 IS NOT NULL AND pm25 IS NOT NULL"
    weather = " AND ".join(f"{c} IS NOT NULL" for c in ["temp_sr", "opad", "wiatr_sr", "wilgotnosc", "cisnienie"])
    no_pm = count(con, f"SELECT COUNT(*) FROM dzien_miasto_pelny WHERE NOT ({full})")
    no_weather = count(con, f"SELECT COUNT(*) FROM dzien_miasto_pelny WHERE {full} AND NOT ({weather})")
    day_funnel = {
        "siatka": [n_city_days, f"{n_cities} miasta × {n_days} dni"],
        "ubytki": [["brak PM10 lub PM2.5", no_pm, f"mniej niż {min_h} z 24 h albo brak danych"],
                   ["brak pogody", no_weather, "PM jest, ale brakuje zmiennej pogodowej"],
                   ["inne miasto niepełne", n_city_days - no_pm - no_weather - n_analysis,
                    f"dzień odpada we wszystkich {n_cities} miastach"]],
        "wynik": [n_analysis, f"{n_cities} miasta × {n_complete} pełnych dni"],
    }
    steps = [
        ["Czas", "UTC (IMGW) → CET (GIOŚ)", "wspólna doba"],
        ["Wartości niemożliwe", "< 0 albo PM2.5 > PM10 → brak",
         "0" if not hours["niemozliwe"] else f"{hours['niemozliwe']} zamienionych na brak"],
        ["Błędy czujników", f"> {ERROR_THRESHOLD} µg/m³ i {ERROR_FACTOR}× więcej niż inne stacje i sąsiednie godziny",
         f"{hours['usuniete']} usuniętych"],
        ["Braki w pogodzie", "kod 8 IMGW → brak", f"{thousands(weather_days)} z {thousands(possible_weather_days)} dni"],
        ["Średnie dobowe", f"min. {min_h} z 24 godzin", f"{thousands(daily['za_malo'])} dób odrzuconych"],
        ["Zapis", "SQLite db/air.sqlite",
         f"{(db_objects['type'] == 'table').sum()} tabel, {(db_objects['type'] == 'view').sum()} widoki"],
        ["Filtracja w bazie", f"widok dzien_miasto: wszystkie {n_cities} miasta pełne", f"{n_complete} z {n_days} dni"],
    ]

    # Kontrola jakości: wynik dla każdej stacji i dwa przykłady decyzji
    summary = pd.read_sql("SELECT * FROM kontrola_pomiarow ORDER BY miasto, wskaznik", con)
    flags = pd.read_sql("SELECT s.miasto, f.* FROM kontrola_flagi f JOIN stacje s USING (kod_stacji)", con)
    examples = pd.concat([flags[flags["decyzja"] == "usunięta"].nlargest(1, "wartosc"),    # najwyższa usunięta
                          flags[flags["decyzja"] != "usunięta"].nlargest(1, "wartosc")])   # najwyższa zostawiona
    day = pd.read_sql("SELECT d.*, s.miasto FROM przyklad_doby d JOIN stacje s USING (kod_stacji) ORDER BY godzina", con)
    missing = pd.read_sql("SELECT miasto, zmienna, dni FROM filtracja_braki ORDER BY miasto, zmienna", con)
    write("przygotowanie.json", {
        "lejek": funnel, "lejek_dni": day_funnel, "kroki": steps, "min_godzin": min_h,
        "tabele": records(db_objects.rename(columns={"name": "nazwa", "type": "typ"})),
        "przyklad_doby": records(day),
        "kontrola": {"prog": ERROR_THRESHOLD, "krotnosc": ERROR_FACTOR, "stacje": records(summary),
                     "przyklady": records(examples)},
        "filtracja": {"dni_kalendarzowe": n_days, "dni_kompletne": n_complete, "dni_miasto_po": n_analysis,
                      "braki": records(missing)},
    })


# --- Panel E: eksploracja i testy ----------------------------------------------------------------

def export_eda(con) -> None:
    """Statystyki opisowe, histogramy, reguła 3σ, testy istotności i autokorelacja."""
    stats = pd.read_sql("SELECT * FROM wyniki_opisowe", con)
    hist = pd.read_sql("SELECT * FROM wyniki_histogramy", con)
    days = pd.read_sql("SELECT pm10, klasa FROM dzien_miasto", con)
    bad = config.PM10_CLASS_LABELS[2]
    limit_3sigma = days["pm10"].mean() + 3 * days["pm10"].std()  # średnia + 3σ dla PM10
    write("eksploracja.json", {
        "statystyki": records(stats),
        "histogramy": {name: g[["od", "do", "n"]].values.tolist() for name, g in hist.groupby("zmienna", sort=False)},
        "regula_3sigma": records(pd.read_sql("SELECT * FROM wyniki_regula_3sigma", con)),
        # Ile dni ponad średnią + 3σ to dni złe – czy reguła 3σ nadaje się do usuwania „odstających” dni
        "pm10_ponad_3sigma": {"granica": limit_3sigma, "dni": int((days["pm10"] > limit_3sigma).sum()),
                              "w_tym_zle": int(((days["pm10"] > limit_3sigma) & (days["klasa"] == bad)).sum()),
                              "zle_wszystkie": int((days["klasa"] == bad).sum())},
        "testy": records(pd.read_sql("SELECT * FROM wyniki_testy", con)),
        "autokorelacja": records(pd.read_sql("SELECT * FROM wyniki_autokorelacja", con)),
    })


# --- Panel 1: sezony -----------------------------------------------------------------------------

def export_seasonal(con) -> None:
    seasonal = pd.read_sql("SELECT * FROM wyniki_sezonowe", con)
    monthly = pd.read_sql("SELECT * FROM wyniki_miesieczne", con)
    write("sezonowe.json", {
        "sezony": records(seasonal),
        "miesieczne": {city: {"pm10": g.sort_values("miesiac")["pm10"].round(1).tolist(),
                              "pm25": g.sort_values("miesiac")["pm25"].round(1).tolist()}
                       for city, g in monthly.groupby("miasto")},
    })


# --- Panel 2: pogoda -----------------------------------------------------------------------------

def export_correlations(con) -> None:
    matrix = pd.read_sql("SELECT * FROM wyniki_macierz_korelacji", con)
    days = pd.read_sql("SELECT miasto, data, sezon, pm10, temp_sr, wiatr_sr FROM dzien_miasto "
                       "WHERE pm10 IS NOT NULL AND temp_sr IS NOT NULL AND wiatr_sr IS NOT NULL", con)
    write("korelacje.json", {
        # Macierz korelacji Spearmana 7 × 7 (format długi: zakres, zmienna_a, zmienna_b, r, n, p)
        "macierz": records(matrix),
        # Punkty do wykresu rozrzutu: [temperatura, wiatr, PM10] w podziale na sezon
        "punkty": {season: g[["temp_sr", "wiatr_sr", "pm10"]].round(1).values.tolist()
                   for season, g in days.groupby("sezon")},
    })


# --- Panel 3: ranking i mapa ---------------------------------------------------------------------

def export_stations(con) -> None:
    """Stacje miast na mapie (średnie i najnowszy odczyt z API) oraz pozostałe stacje województwa."""
    stations = pd.read_sql("SELECT * FROM stacje", con)
    means = pd.read_sql("SELECT kod_stacji, AVG(pm10) pm10_srednia, AVG(pm25) pm25_srednia "
                        "FROM pm_dobowe GROUP BY kod_stacji", con)
    latest = pd.read_sql("""
        SELECT b.kod_stacji, b.wskaznik, b.czas, b.wartosc FROM pm_biezace b
        JOIN (SELECT kod_stacji, wskaznik, MAX(czas) czas FROM pm_biezace GROUP BY kod_stacji, wskaznik) m
          USING (kod_stacji, wskaznik, czas)""", con)
    # 2 miejsca po przecinku – do 1 miejsca zaokrągla dopiero strona (tak samo jak w rankingu)
    df = stations.merge(means, on="kod_stacji", how="left").round({"pm10_srednia": 2, "pm25_srednia": 2})
    out = []
    for rec in records(df):
        current = latest[latest["kod_stacji"] == rec["kod_stacji"]]
        rec["biezace"] = {r["wskaznik"]: {"czas": r["czas"], "wartosc": r["wartosc"]} for _, r in current.iterrows()}
        rec["kolor"] = CITY_COLORS[rec["miasto"]]
        out.append(rec)
    # Pozostałe stacje w Polsce z API GIOŚ (TK03) – na mapie jako tło
    api = json.loads((config.API_RAW / "gios_stations.json").read_text(encoding="utf-8"))
    known = set(stations["kod_stacji"])
    others = [{"kod_stacji": s["Kod stacji"], "nazwa": s["Nazwa stacji"], "miasto": s["Nazwa miasta"],
               "lat": float(s["WGS84 φ N"]), "lon": float(s["WGS84 λ E"])}
              for s in api if s["Kod stacji"] not in known]
    write("stacje.json", {"stacje": out, "inne_stacje": others, "teraz": export_now(con)})


def export_now(con) -> dict | None:
    """Ostatnie 24 h z API GIOŚ na tle dni tego samego miesiąca w latach 2023–2025 (kreski na pasku)."""
    now = pd.read_sql("SELECT * FROM wyniki_biezace", con)
    if now.empty:  # API niedostępne – karta na stronie się nie pokazuje
        return None
    month = int(now["miesiac"].iloc[0])
    days = pd.read_sql("SELECT miasto, pm10, pm25 FROM dzien_miasto "
                       "WHERE CAST(substr(data, 6, 2) AS INTEGER) = ?", con, params=[month])
    # Kreski służą tylko do rysowania, więc 1 miejsce po przecinku wystarcza
    history = {city: {col: g[col].dropna().round(1).tolist() for col in config.POLLUTANTS}
               for city, g in days.groupby("miasto")}
    return {"miesiac": month, "wiersze": records(now), "historia": history}


def export_ranking(con) -> None:
    ranking = pd.read_sql("SELECT * FROM wyniki_ranking ORDER BY pozycja", con)
    write("ranking.json", {"ranking": records(ranking.round(2))})


# --- Panel 4: klasy dni -------------------------------------------------------------------------

def export_classes(con) -> None:
    """Ile dni dobrych, umiarkowanych i złych: razem, w miastach, sezonach i miesiącach."""
    write("klasy.json", {"klasy": records(pd.read_sql("SELECT * FROM wyniki_klasy", con))})


# --- Panel 5: typy dni ---------------------------------------------------------------------------

def export_clusters() -> None:
    report = json.loads((config.DATA_PROCESSED / "klastry_raport.json").read_text(encoding="utf-8"))
    # Udział każdego typu we wszystkich dniach złych – do kafli na stronie
    total_bad = sum(v["zła"] for v in report["klaster_klasa"].values())
    for p in report["profil"]:
        p["udzial_w_dniach_zlych_pct"] = round(100 * report["klaster_klasa"][str(p["klaster"])]["zła"] / total_bad, 1)
    write("klastry.json", report)


# --- Wspólne dane strony -------------------------------------------------------------------------

def export_meta(con) -> None:
    """Zakres dat, miasta z kolorami, progi klas i źródła – używane przez wiele paneli."""
    # Zakres dat z pełnych danych (przed filtracją), liczba dni-miasto po filtracji
    period = pd.read_sql("SELECT MIN(data) od, MAX(data) do, (SELECT COUNT(*) FROM dzien_miasto) dni "
                         "FROM dzien_miasto_pelny", con).iloc[0]
    current = pd.read_sql("SELECT MIN(czas) od, MAX(czas) do FROM pm_biezace", con).iloc[0]
    write("meta.json", {
        "wygenerowano": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "zakres": {"od": period["od"], "do": period["do"], "dni_miasto": int(period["dni"])},
        "dane_biezace": {"od": current["od"], "do": current["do"]},
        "miasta": [{"miasto": c, "kolor": CITY_COLORS[c], "stacja": v["gios"],
                    "stacja_meteo": config.IMGW_STATIONS[v["imgw"]]} for c, v in config.CITIES.items()],
        "progi_pm10": {"granice": config.PM10_CLASS_BINS, "klasy": config.PM10_CLASS_LABELS},
        "miesiace_grzewcze": config.HEATING_MONTHS,
        "zrodla": [
            {"nazwa": "GIOŚ – archiwum pomiarów", "format": "XLSX (ZIP)", "url": "https://powietrze.gios.gov.pl/pjp/archives"},
            {"nazwa": "GIOŚ – API jakości powietrza v1", "format": "JSON-LD", "url": "https://api.gios.gov.pl/pjp-api/swagger-ui/"},
            {"nazwa": "IMGW – dane publiczne (synop)", "format": "CSV (ZIP)", "url": "https://danepubliczne.imgw.pl"},
        ],
    })


def run() -> None:
    with sqlite3.connect(config.DB_PATH) as con:
        export_acquisition(con)   # A
        export_preparation(con)   # P
        export_eda(con)           # E
        export_seasonal(con)      # 1
        export_correlations(con)  # 2
        export_stations(con)      # 3
        export_ranking(con)
        export_classes(con)       # 4
        export_meta(con)
    con.close()
    export_clusters()             # 5


if __name__ == "__main__":
    run()
