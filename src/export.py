"""TK05 – eksport wyników do web/data/*.json dla strony WWW.

Dane są czytane z SQLite (tabele wyniki_*, stacje, pm_biezace, dzien_miasto)
oraz z raportu modelu (data/processed/model_raport.json). Wartości NaN są
zamieniane na null, a zapis z allow_nan=False gwarantuje poprawny JSON.
"""
import csv
import io
import json
import math
import sqlite3
import zipfile
from datetime import datetime

import openpyxl
import pandas as pd

from src import config
from src.analyze import CITY_COLORS
from src.clean import KROTNOSC_BLEDU, PROG_BLEDU
from src.model import feature_label


def _clean(obj):
    """Rekurencyjnie zamienia NaN/inf i typy numpy na wartości zgodne z JSON."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if hasattr(obj, "item"):  # skalary numpy
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _write(name: str, data) -> None:
    config.WEB_DATA.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_clean(data), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    (config.WEB_DATA / name).write_text(text, encoding="utf-8")
    print(f"  web/data/{name}: {len(text) / 1024:.1f} kB")


def _records(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(df.notna(), None).to_dict("records")


def export_seasonal(con) -> None:
    seasonal = pd.read_sql("SELECT * FROM wyniki_sezonowe", con)
    monthly = pd.read_sql("SELECT * FROM wyniki_miesieczne", con)
    _write("sezonowe.json", {
        "sezony": _records(seasonal),
        "miesieczne": {city: {"pm10": g.sort_values("miesiac")["pm10"].round(1).tolist(),
                              "pm25": g.sort_values("miesiac")["pm25"].round(1).tolist()}
                       for city, g in monthly.groupby("miasto")},
    })


def export_eda(con) -> None:
    """Panel „Eksploracja danych”: statystyki opisowe i histogramy (przedziały: od, do, liczba obserwacji)."""
    stats = pd.read_sql("SELECT * FROM wyniki_opisowe", con)
    hist = pd.read_sql("SELECT * FROM wyniki_histogramy", con)
    days = pd.read_sql("SELECT pm10, klasa FROM dzien_miasto", con)
    granica = days["pm10"].mean() + 3 * days["pm10"].std()  # średnia + 3σ dla PM10
    _write("eksploracja.json", {
        "statystyki": _records(stats),
        "histogramy": {z: g[["od", "do", "n"]].values.tolist() for z, g in hist.groupby("zmienna", sort=False)},
        "regula_3sigma": _records(pd.read_sql("SELECT * FROM wyniki_regula_3sigma", con)),
        # Ile dni ponad średnią + 3σ to dni złe – czy reguła 3σ nadaje się do odrzucania „odstających” dni
        "pm10_ponad_3sigma": {"granica": granica, "dni": int((days["pm10"] > granica).sum()),
                              "w_tym_zle": int(((days["pm10"] > granica) & (days["klasa"] == config.PM10_CLASS_LABELS[2])).sum()),
                              "zle_wszystkie": int((days["klasa"] == config.PM10_CLASS_LABELS[2]).sum())},
        "testy": _records(pd.read_sql("SELECT * FROM wyniki_testy", con)),
        "autokorelacja": _records(pd.read_sql("SELECT * FROM wyniki_autokorelacja", con)),
    })


def export_correlations(con) -> None:
    matrix = pd.read_sql("SELECT * FROM wyniki_macierz_korelacji", con)
    days = pd.read_sql("SELECT miasto, data, sezon, pm10, temp_sr, wiatr_sr FROM dzien_miasto "
                       "WHERE pm10 IS NOT NULL AND temp_sr IS NOT NULL AND wiatr_sr IS NOT NULL", con)
    _write("korelacje.json", {
        # Macierz korelacji Spearmana 7 × 7 (format długi: zakres, zmienna_a, zmienna_b, r, n)
        "macierz": _records(matrix),
        # Punkty do wykresu rozrzutu: [temperatura, wiatr, PM10] w podziale na sezon
        "punkty": {s: g[["temp_sr", "wiatr_sr", "pm10"]].round(1).values.tolist() for s, g in days.groupby("sezon")},
    })


def export_stations(con) -> None:
    stations = pd.read_sql("SELECT * FROM stacje", con)
    means = pd.read_sql("SELECT kod_stacji, AVG(pm10) pm10_srednia, AVG(pm25) pm25_srednia "
                        "FROM pm_dobowe GROUP BY kod_stacji", con)
    latest = pd.read_sql("""
        SELECT b.kod_stacji, b.wskaznik, b.czas, b.wartosc FROM pm_biezace b
        JOIN (SELECT kod_stacji, wskaznik, MAX(czas) czas FROM pm_biezace GROUP BY kod_stacji, wskaznik) m
          USING (kod_stacji, wskaznik, czas)""", con)
    # 2 miejsca po przecinku – do 1 miejsca zaokrągla dopiero widok (spójnie z rankingiem)
    df = stations.merge(means, on="kod_stacji", how="left").round({"pm10_srednia": 2, "pm25_srednia": 2})
    out = []
    for rec in _records(df):
        cur = latest[latest["kod_stacji"] == rec["kod_stacji"]]
        rec["biezace"] = {r["wskaznik"]: {"czas": r["czas"], "wartosc": r["wartosc"]} for _, r in cur.iterrows()}
        rec["kolor"] = CITY_COLORS[rec["miasto"]]
        out.append(rec)
    # Pozostałe stacje województwa z API GIOŚ (TK03) – na mapie jako kontekst
    api = json.loads((config.API_RAW / "gios_stations.json").read_text(encoding="utf-8"))
    known = set(stations["kod_stacji"])
    others = [{"kod_stacji": s["Kod stacji"], "nazwa": s["Nazwa stacji"], "miasto": s["Nazwa miasta"],
               "lat": float(s["WGS84 φ N"]), "lon": float(s["WGS84 λ E"])}
              for s in api if s["Województwo"] == config.GIOS_API_VOIVODESHIP and s["Kod stacji"] not in known]
    _write("stacje.json", {"stacje": out, "inne_stacje": others})


def export_ranking(con) -> None:
    ranking = pd.read_sql("SELECT * FROM wyniki_ranking ORDER BY pozycja", con)
    gus = pd.read_sql("SELECT * FROM gus_powiaty ORDER BY miasto, rok", con)
    _write("ranking.json", {
        "ranking": _records(ranking.round(2)),
        "korelacje_gus": _records(pd.read_sql("SELECT * FROM wyniki_ranking_gus", con)),
        "gus": _records(gus),
    })


def export_model() -> None:
    report = json.loads((config.DATA_PROCESSED / "model_raport.json").read_text(encoding="utf-8"))
    for key in ["waznosc_cech_rf", "waznosc_cech_drzewo"]:
        for rec in report[key]:
            rec["etykieta"] = feature_label(rec["cecha"])
    _write("model.json", report)


def export_clusters() -> None:
    report = json.loads((config.DATA_PROCESSED / "klastry_raport.json").read_text(encoding="utf-8"))
    # Ten sam udział dni złych liczony względem wszystkich dni złych – do kafli na stronie
    total_bad = sum(v["zła"] for v in report["klaster_klasa"].values())
    for p in report["profil"]:
        p["udzial_w_dniach_zlych_pct"] = round(100 * report["klaster_klasa"][str(p["klaster"])]["zła"] / total_bad, 1)
    _write("klastry.json", report)


def _plik(sciezka) -> dict:
    s = sciezka.stat()
    return {"nazwa": sciezka.name, "kB": round(s.st_size / 1024),
            "pobrano": datetime.fromtimestamp(s.st_mtime).strftime("%Y-%m-%d %H:%M")}


def export_acquisition(con) -> None:
    """Panel „Akwizycja danych”: źródła, pobrane pliki, próbki surowych danych, liczby i dziennik zapytań HTTP."""
    gios, imgw = config.GIOS_RAW, config.IMGW_RAW
    licz = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731

    # GIOŚ – pierwsze wiersze arkusza XLSX (4 kolumny), tak jak w pliku
    ark = openpyxl.load_workbook(gios / "extracted" / f"{config.YEARS[-1]}_PM10_1g.xlsx", read_only=True).worksheets[0]
    probka_gios = [[c.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if isinstance(c, datetime) else ("" if c is None else str(c))
                    for c in w[:4]] for _, w in zip(range(9), ark.iter_rows(values_only=True))]
    # IMGW – nazwy pierwszych kolumn z pliku nagłówka i pierwszy wiersz danych (cp1250)
    naglowek = (imgw / "s_t_naglowek.csv").read_text(encoding=config.IMGW_ENCODING).strip().split(",")
    kod = next(iter(config.IMGW_STATIONS))
    with zipfile.ZipFile(imgw / "terminowe" / f"{config.YEARS[-1]}_{kod}_s.zip") as zf:
        wiersz = next(csv.reader(io.TextIOWrapper(zf.open(f"s_t_{kod}_{config.YEARS[-1]}.csv"), encoding=config.IMGW_ENCODING)))
    kolumny = [i for i, n in enumerate(naglowek) if n in ("NSP", "POST", "ROK", "MC", "DZ", "GG", "KRWR", "FWR", "TEMP", "WLGW", "PPPS", "WO6G")]
    # API GIOŚ i BDL – fragmenty zapisanych odpowiedzi JSON
    stacje_api = json.loads((config.API_RAW / "gios_stations.json").read_text(encoding="utf-8"))
    migawki = sorted(config.API_RAW.glob("gios_current_*.json"))
    ostatnia = json.loads(migawki[-1].read_text(encoding="utf-8"))
    kod_gios = config.GIOS_STATIONS[0]
    bdl = json.loads((config.API_RAW / "bdl_powiaty.json").read_text(encoding="utf-8"))
    miasto0 = next(iter(config.CITIES))
    dziennik_plik = config.DATA_PROCESSED / "akwizycja_api.json"
    dziennik = json.loads(dziennik_plik.read_text(encoding="utf-8")) if dziennik_plik.exists() else None

    pliki_gios = [_plik(gios / "metadane.xlsx")] + [_plik(gios / f"{r}.zip") for r in config.YEARS]
    pliki_imgw = [_plik(imgw / "terminowe" / f"{r}_{k}_s.zip") for r in config.YEARS for k in config.IMGW_STATIONS]
    godz_pm = _godziny_pm(con)["wczytane"]  # tak jak w plikach – przed kontrolą jakości
    terminy = licz("SELECT SUM(n_godz) FROM pogoda_dobowa")
    zrodla = [
        {"id": "gios", "nazwa": "GIOŚ – archiwum pomiarów", "tk": "TK02", "dostep": "plik lokalny",
         "url": "https://powietrze.gios.gov.pl/pjp/archives",
         "format": "ZIP z plikami XLSX · czas CET, znacznik = koniec godziny",
         "co": f"godzinowe PM10 i PM2.5 z lat {config.YEARS[0]}–{config.YEARS[-1]} dla {len(config.GIOS_STATIONS)} stacji + metadane stacji",
         "liczby": [["plików", len(pliki_gios)], ["MB", round(sum(p["kB"] for p in pliki_gios) / 1024)],
                    ["ważnych pomiarów godzinowych", godz_pm]],
         "pliki": pliki_gios,
         "probka": {"typ": "tabela", "opis": f"{config.YEARS[-1]}_PM10_1g.xlsx – 6 wierszy nagłówka, potem godziny; 4 z ok. 185 kolumn",
                    "wiersze": probka_gios}},
        {"id": "imgw", "nazwa": "IMGW-PIB – dane synoptyczne", "tk": "TK02", "dostep": "plik lokalny",
         "url": "https://danepubliczne.imgw.pl",
         "format": "ZIP z plikami CSV bez nagłówka · kodowanie cp1250 · czas UTC",
         "co": f"terminowe (godzinowe) obserwacje z {len(config.IMGW_STATIONS)} stacji: temperatura, wiatr, wilgotność, ciśnienie, opad 6-godzinny",
         "liczby": [["plików", len(pliki_imgw)], ["MB", round(sum(p["kB"] for p in pliki_imgw) / 1024, 1)],
                    ["terminów z pomiarem wiatru", terminy]],
         "pliki": pliki_imgw,
         "probka": {"typ": "tabela", "opis": f"s_t_{kod}_{config.YEARS[-1]}.csv – 12 ze {len(naglowek)} kolumn; nazwy z pliku s_t_nagłówek.csv",
                    "wiersze": [[naglowek[i] for i in kolumny], [wiersz[i].strip() for i in kolumny]]}},
        {"id": "gios_api", "nazwa": "GIOŚ – API jakości powietrza v1", "tk": "TK03", "dostep": "zapytania HTTP",
         "url": config.GIOS_API_URL,
         "format": "JSON-LD (nagłówek Accept: application/ld+json) · stronicowanie page/size",
         "co": "lista stacji, stanowiska pomiarowe i bieżące odczyty PM10/PM2.5 (do 3 dób wstecz)",
         "liczby": [["stacji w Polsce", len(stacje_api)],
                    ["w woj. podkarpackim", sum(s["Województwo"] == config.GIOS_API_VOIVODESHIP for s in stacje_api)],
                    ["bieżących odczytów", licz("SELECT COUNT(*) FROM pm_biezace")], ["zapisanych migawek", len(migawki)]],
         "pliki": [_plik(config.API_RAW / "gios_stations.json"), _plik(migawki[-1])],
         "probka": {"typ": "json", "opis": "odpowiedź station/findAll (1 stacja) i data/getData (2 odczyty)",
                    "tekst": json.dumps({"stacja": next(s for s in stacje_api if s["Kod stacji"] == kod_gios),
                                         "odczyty": ostatnia[kod_gios]["dane"]["PM10"][:2]}, ensure_ascii=False, indent=2)}},
        {"id": "bdl", "nazwa": "GUS – Bank Danych Lokalnych", "tk": "TK03", "dostep": "zapytania HTTP",
         "url": config.BDL_API_URL,
         "format": "JSON · bez klucza (limit 5 zapytań/s, 100/15 min)",
         "co": f"ludność, samochody osobowe, samochody na 1000 mieszk. i gęstość zaludnienia – {len(config.CITIES)} powiatów × {len(config.YEARS)} lata",
         "liczby": [["powiatów", len(config.CITIES)], ["zmiennych", len(config.BDL_VARIABLES)],
                    ["wartości", licz("SELECT COUNT(*) FROM gus_powiaty") * len(config.BDL_VARIABLES)]],
         "pliki": [_plik(config.API_RAW / "bdl_powiaty.json")],
         "probka": {"typ": "json", "opis": f"odpowiedź data/by-unit/{config.CITIES[miasto0]['bdl']} (1 zmienna)",
                    "tekst": json.dumps({"unitName": bdl[miasto0]["unitName"],
                                         "results": [{k: r[k] for k in ("id", "values")} for r in bdl[miasto0]["results"][:1]]},
                                        ensure_ascii=False, indent=2)}},
    ]
    przeplyw = [
        ["źródła danych", "4", "2 archiwa plików + 2 API"],
        ["surowe pliki", f"{round((sum(p['kB'] for z in zrodla for p in z['pliki'])) / 1024)} MB", "data/raw/"],
        ["pomiary godzinowe", _tys(godz_pm + terminy), "PM + pogoda, wczytane z plików"],
        # Ostatni krok to odnośnik do panelu, w którym dane są czyszczone
        ["przygotowanie danych", "dalej →", "kontrola jakości, agregacja do dób, filtracja", "#przygotowanie"],
    ]
    _write("akwizycja.json", {"zrodla": zrodla, "przeplyw": przeplyw, "dziennik": dziennik})


def _tys(n: int) -> str:
    """Liczba z odstępem tysięcy, jak w polskim zapisie (281 741)."""
    return f"{n:,}".replace(",", " ")


def _minus(n: int) -> str:
    """Zmiana liczby wartości w lejku: „−15”, a przy braku zmian „0”."""
    return f"−{_tys(n)}" if n else "0"


def _godziny_pm(con) -> dict:
    """Pomiary godzinowe PM: wczytane z plików, odrzucone przy kontroli jakości i pozostałe."""
    k = pd.read_sql("SELECT * FROM kontrola_pomiarow", con)
    po = int(k["godzin_po_kontroli"].sum())
    niemozliwe = int(k["ujemne"].sum() + k["pm25_ponad_pm10"].sum())
    usuniete = int(k["usuniete"].sum())
    return {"wczytane": po + niemozliwe + usuniete, "niemozliwe": niemozliwe, "usuniete": usuniete, "po_kontroli": po,
            "oznaczone": int(k["odstaje_od_innych"].sum() + k["skok_w_czasie"].sum())}


def export_preparation(con) -> None:
    """Panel „Przygotowanie danych”: lejek od pomiarów godzinowych do zbioru analitycznego, kroki TK04,
    przykład jednej doby przed i po kontroli, kontrola jakości i filtracja."""
    licz = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    g = _godziny_pm(con)
    miasta = len(config.CITIES)
    w_plikach = licz("SELECT COUNT(*) FROM pm_dobowe") * 24 * 2  # pełna siatka dni × stacji, 24 h, PM10 i PM2.5
    min_h = config.MIN_HOURS_PER_DAY
    doby = pd.read_sql(f"""SELECT SUM(pm10 IS NOT NULL) pm10, SUM(pm25 IS NOT NULL) pm25,
        SUM(n_godz_pm10 BETWEEN 1 AND {min_h - 1}) + SUM(n_godz_pm25 BETWEEN 1 AND {min_h - 1}) za_malo FROM pm_dobowe""",
                       con).iloc[0]
    dni_kal = licz("SELECT COUNT(DISTINCT data) FROM dzien_miasto_pelny")
    dni_pelne = licz("SELECT COUNT(*) FROM dzien_miasto_pelny")
    kompletne = licz("SELECT COUNT(*) FROM dni_kompletne")
    dni_miasto = licz("SELECT COUNT(*) FROM dzien_miasto")
    pogoda = pd.read_sql("""SELECT COUNT(*) dni, SUM(temp_sr IS NULL) temperatura, SUM(opad IS NULL) opad,
        SUM(wiatr_sr IS NULL) wiatr, SUM(wilgotnosc IS NULL) "wilgotność", SUM(cisnienie IS NULL) "ciśnienie"
        FROM pogoda_dobowa""", con).iloc[0]
    mozliwe_pogoda = len(config.IMGW_STATIONS) * dni_kal
    braki_pogody = ", ".join(f"{k} {int(v)}" for k, v in pogoda.drop("dni").items() if v)
    # Tabele z etapu czyszczenia (wyniki analizy, modelu i klastrów powstają później)
    obiekty = pd.read_sql("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
                          "AND name NOT LIKE 'wyniki_%' AND name NOT LIKE 'model_%' AND name NOT LIKE 'klastry_%' "
                          "ORDER BY type, name", con)
    obiekty["wiersze"] = [licz(f'SELECT COUNT(*) FROM "{n}"') for n in obiekty["name"]]

    lejek = [
        ["pomiary godzinowe PM", _tys(g["wczytane"]), f"z plików (pustych miejsc: {_tys(w_plikach - g['wczytane'])})"],
        ["wartości niemożliwe", _minus(g['niemozliwe']), "ujemne albo PM2.5 > PM10"],
        ["błędy czujników", _minus(g['usuniete']), f"usunięte ({_tys(g['oznaczone'])} oznaczonych zostaje)"],
        ["średnie dobowe", _tys(doby["pm10"]), f"dni z PM10 na stacjach (PM2.5: {_tys(doby['pm25'])})"],
        ["z pogodą", _tys(dni_pelne), f"{miasta} miasta × {dni_kal} dni"],
        ["po filtracji", _tys(dni_miasto), f"{miasta} miasta × {kompletne} pełnych dni"],
    ]
    kroki = [
        ["Ujednolicenie czasu",
         "GIOŚ podaje czas zimowy (CET), a IMGW czas UTC – przeliczamy wszystko na CET, żeby doba była ta sama.",
         "wspólna doba dla pyłu i pogody"],
        ["Wartości niemożliwe", "Stężenie poniżej 0 albo PM2.5 większe niż PM10 zamieniamy na brak.",
         "nie było takich wartości" if not g["niemozliwe"] else f"{g['niemozliwe']} wartości zamienionych na brak"],
        ["Błędy czujników",
         f"Wartość powyżej {PROG_BLEDU} µg/m³, która jest {KROTNOSC_BLEDU} razy wyższa niż na innych stacjach "
         "i niż godzinę przed i po, to błąd – usuwamy ją.",
         f"{g['usuniete']} usuniętych, {_tys(g['oznaczone'])} oznaczonych"],
        ["Braki w pogodzie", "IMGW oznacza brak pomiaru kodem 8 – zamieniamy go na brak. Braków nie zgadujemy.",
         f"{_tys(pogoda['dni'])} z {_tys(mozliwe_pogoda)} dni; brakujące: {braki_pogody or 'żadne'}"],
        ["Średnie dobowe", f"Średnią z doby liczymy tylko, gdy jest co najmniej {min_h} z 24 godzin.",
         f"{_tys(doby['za_malo'])} dób odrzuconych (za mało godzin)"],
        ["Zapis do bazy", "Wszystko trafia do bazy SQLite db/air.sqlite.",
         f"{(obiekty['type'] == 'table').sum()} tabel i {(obiekty['type'] == 'view').sum()} widoki"],
        ["Filtracja dni", f"Dzień zostaje tylko, gdy wszystkie {miasta} miasta mają pełne pomiary.",
         f"{kompletne} z {dni_kal} dni ({round(100 * kompletne / dni_kal)}%)"],
    ]

    # Kontrola jakości: podsumowanie stacji i dwa przykłady decyzji
    kontrola = pd.read_sql("SELECT * FROM kontrola_pomiarow ORDER BY miasto, wskaznik", con)
    flagi = pd.read_sql("SELECT s.miasto, f.* FROM kontrola_flagi f JOIN stacje s USING (kod_stacji)", con)
    przyklady = pd.concat([flagi[flagi["decyzja"] == "usunięta"].nlargest(1, "wartosc"),     # najwyższa usunięta
                           flagi[flagi["decyzja"] != "usunięta"].nlargest(1, "wartosc")])    # najwyższa zostawiona
    doba = pd.read_sql("SELECT d.*, s.miasto FROM przyklad_doby d JOIN stacje s USING (kod_stacji) ORDER BY godzina", con)
    braki = pd.read_sql("SELECT miasto, zmienna, dni FROM filtracja_braki ORDER BY miasto, zmienna", con)
    _write("przygotowanie.json", {
        "lejek": lejek, "kroki": kroki, "min_godzin": min_h,
        "tabele": _records(obiekty.rename(columns={"name": "nazwa", "type": "typ"})),
        "przyklad_doby": _records(doba),
        "kontrola": {"prog": PROG_BLEDU, "krotnosc": KROTNOSC_BLEDU, "stacje": _records(kontrola),
                     "przyklady": _records(przyklady)},
        "filtracja": {"dni_kalendarzowe": dni_kal, "dni_kompletne": kompletne, "dni_miasto_po": dni_miasto,
                      "braki": _records(braki)},
    })


def export_meta(con) -> None:
    # Zakres dat z pełnych danych (przed filtracją), liczba dni-miasto po filtracji
    rng = pd.read_sql("SELECT MIN(data) od, MAX(data) do, (SELECT COUNT(*) FROM dzien_miasto) dni FROM dzien_miasto_pelny", con).iloc[0]
    current = pd.read_sql("SELECT MIN(czas) od, MAX(czas) do FROM pm_biezace", con).iloc[0]
    _write("meta.json", {
        "wygenerowano": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "zakres": {"od": rng["od"], "do": rng["do"], "dni_miasto": int(rng["dni"])},
        "dane_biezace": {"od": current["od"], "do": current["do"]},
        "miasta": [{"miasto": c, "kolor": CITY_COLORS[c], "stacja": v["gios"],
                    "stacja_meteo": config.IMGW_STATIONS[v["imgw"]]} for c, v in config.CITIES.items()],
        "progi_pm10": {"granice": config.PM10_CLASS_BINS, "klasy": config.PM10_CLASS_LABELS},
        "miesiace_grzewcze": config.HEATING_MONTHS,
        "zrodla": [
            {"nazwa": "GIOŚ – archiwum pomiarów", "format": "XLSX (ZIP)", "url": "https://powietrze.gios.gov.pl/pjp/archives"},
            {"nazwa": "GIOŚ – API jakości powietrza v1", "format": "JSON-LD", "url": "https://api.gios.gov.pl/pjp-api/swagger-ui/"},
            {"nazwa": "IMGW – dane publiczne (synop)", "format": "CSV (ZIP)", "url": "https://danepubliczne.imgw.pl"},
            {"nazwa": "GUS – Bank Danych Lokalnych API", "format": "JSON", "url": "https://bdl.stat.gov.pl/api/v1"},
        ],
    })


def run() -> None:
    with sqlite3.connect(config.DB_PATH) as con:
        export_eda(con)
        export_seasonal(con)
        export_correlations(con)
        export_stations(con)
        export_ranking(con)
        export_meta(con)
        export_acquisition(con)
        export_preparation(con)
    con.close()
    export_model()
    export_clusters()


if __name__ == "__main__":
    run()
