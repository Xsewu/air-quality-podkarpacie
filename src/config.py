"""Konfiguracja: stacje, lata, ścieżki, progi klas jakości powietrza.

Wszystkie kody stacji i identyfikatory plików zostały odczytane z faktycznie
pobranych danych (strona archiwum GIOŚ, metadane GIOŚ, wykaz stacji IMGW).
"""
from pathlib import Path

# --- Ścieżki ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
DB_PATH = ROOT / "db" / "air.sqlite"
WEB_DATA = ROOT / "web" / "data"

GIOS_RAW = DATA_RAW / "gios"
IMGW_RAW = DATA_RAW / "imgw"

# --- Zakres czasowy: 3 ostatnie pełne lata w archiwum GIOŚ -----------------
YEARS = [2023, 2024, 2025]

# --- GIOŚ: archiwum plików (https://powietrze.gios.gov.pl/pjp/archives) ----
GIOS_DOWNLOAD_URL = "https://powietrze.gios.gov.pl/pjp/archives/downloadFile/{file_id}"
# Identyfikatory plików odczytane ze strony archiwum ("Wyniki pomiarów z RRRR roku")
GIOS_ARCHIVE_IDS = {2023: 564, 2024: 582, 2025: 644}
# "Metadane oraz kody stacji i stanowisk pomiarowych" (plik XLSX, nie ZIP)
GIOS_METADATA_ID = 643
# Pliki godzinowe (stacje automatyczne); w archiwum czas podany w CET (UTC+1)
GIOS_POLLUTANTS = ["PM10", "PM25"]
GIOS_HEADER_ROWS = 6  # Nr, Kod stacji, Wskaźnik, Czas uśredniania, Jednostka, Kod stanowiska

# --- IMGW: dane publiczne (https://danepubliczne.imgw.pl) ------------------
IMGW_BASE_URL = "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/dane_meteorologiczne"
IMGW_ENCODING = "cp1250"
# Stacje synoptyczne (kod 3-cyfrowy z nazw plików, pełny kod NSP z wykazu stacji)
IMGW_STATIONS = {
    580: "RZESZÓW-JASIONKA",
    670: "KROSNO",
    690: "LESKO",
}

# --- API (TK03) -------------------------------------------------------------
API_RAW = DATA_RAW / "api"
# API GIOŚ v1 (JSON-LD); wersję bez /v1 wycofano 30.06.2025
GIOS_API_URL = "https://api.gios.gov.pl/pjp-api/v1/rest"
GIOS_API_VOIVODESHIP = "PODKARPACKIE"
GIOS_API_PM_CODES = ["PM10", "PM2.5"]  # pole "Wskaźnik - kod" w liście stanowisk
# API GUS Bank Danych Lokalnych; klucz opcjonalny (zmienna środowiskowa BDL_API_KEY)
BDL_API_URL = "https://bdl.stat.gov.pl/api/v1"
BDL_VARIABLES = {
    60641: "ludnosc",            # P1336: ludność ogółem, miejsce zamieszkania, stan na 31 XII
    32561: "samochody_osobowe",  # P1733: pojazdy ogółem – samochody osobowe [szt.]
    60533: "samochody_na_1000",  # P2420: samochody osobowe na 1000 ludności
    60559: "gestosc_zaludnienia",  # P2425: ludność na 1 km2
}

# --- Miasta: stacja GIOŚ -> miasto -> stacja meteo IMGW -> powiat BDL --------
# Jedna stacja GIOŚ na miasto: stacja tła miejskiego z pełnymi latami 2023–2025.
# "bdl" – identyfikator powiatu w BDL (poziom 5).
CITIES = {
    # Rzeszów ma 5 stacji; PkRzeszStarz to stacja tła z kompletem PM10 i PM2.5 (PkRzeszPilsu jest komunikacyjna)
    "Rzeszów": {"gios": "PkRzeszStarz", "imgw": 580, "bdl": "061813563000"},     # Powiat m. Rzeszów
    "Krosno": {"gios": "PkKrosKletow", "imgw": 670, "bdl": "061813361000"},      # Powiat m. Krosno
    # brak danych synop dla Przemyśla (695) – najbliższa stacja: Lesko
    "Przemyśl": {"gios": "PkPrzemGrunw", "imgw": 690, "bdl": "061813462000"},    # Powiat m. Przemyśl
    # brak stacji synop w Jaśle – najbliższa stacja: Krosno; Jasło nie jest miastem na prawach powiatu
    "Jasło": {"gios": "PkJasloSikor", "imgw": 670, "bdl": "061813305000"},       # Powiat jasielski
}
GIOS_STATIONS = [c["gios"] for c in CITIES.values()]

# --- Czyszczenie i agregacja (TK04) ------------------------------------------
# Średnia dobowa liczona tylko przy co najmniej 75% ważnych godzin (18 z 24) –
# kryterium kompletności stosowane w ocenie jakości powietrza (dyrektywa 2008/50/WE).
MIN_HOURS_PER_DAY = 18
# Sezon grzewczy: październik–marzec; letni: kwiecień–wrzesień
HEATING_MONTHS = [10, 11, 12, 1, 2, 3]

# Klasy jakości dnia wg dobowego PM10 [µg/m3]:
#   dobra       PM10 <= 25        (do połowy dobowej wartości dopuszczalnej),
#   umiarkowana 25 < PM10 <= 50   (do dobowej wartości dopuszczalnej UE 50 µg/m3),
#   zła         PM10 > 50         (przekroczenie dobowej wartości dopuszczalnej).
PM10_CLASS_BINS = [25, 50]
PM10_CLASS_LABELS = ["dobra", "umiarkowana", "zła"]
