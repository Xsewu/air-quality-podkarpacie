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
# Pliki godzinowe (stacje automatyczne); w archiwum czas podany w CET (UTC+1)
GIOS_POLLUTANTS = ["PM10", "PM25"]
GIOS_HEADER_ROWS = 6  # Nr, Kod stacji, Wskaźnik, Czas uśredniania, Jednostka, Kod stanowiska
IMGW_ENCODING = "cp1250"
# Stacje synoptyczne (kod 3-cyfrowy z nazw plików, pełny kod NSP z wykazu stacji)
IMGW_STATIONS = {
    375: "WARSZAWA",
    560: "KATOWICE-MUCHOWIEC",
    424: "WROCŁAW-STRACHOWICE",
    155: "GDAŃSK-ŚWIBNO",
}

# --- API -------------------------------------------------------------------
API_RAW = DATA_RAW / "api"
# API GIOŚ v1 (JSON-LD); wersję bez /v1 wycofano 30.06.2025
GIOS_API_URL = "https://api.gios.gov.pl/pjp-api/v1/rest"
GIOS_API_PM_CODES = ["PM10", "PM2.5"]  # pole "Wskaźnik - kod" w liście stanowisk

# --- Miasta: stacja GIOŚ -> miasto -> stacja meteo IMGW ----------------------
# Miasta, w których jest i stacja synoptyczna IMGW z danymi za 2023–2025, i stacja tła miejskiego GIOŚ
# z PM10 i PM2.5. Z kilku stacji w mieście bierzemy tę z najpełniejszymi danymi.
CITIES = {
    "Warszawa": {"gios": "MzWarBajkowa", "imgw": 375},
    "Katowice": {"gios": "SlKatoKossut", "imgw": 560},
    "Wrocław": {"gios": "DsWrocWybCon", "imgw": 424},
    "Gdańsk": {"gios": "PmGdaLeczkow", "imgw": 155},
}
GIOS_STATIONS = [c["gios"] for c in CITIES.values()]

# --- Czyszczenie i agregacja ------------------------------------------------
# Średnia dobowa liczona tylko przy co najmniej 75% ważnych godzin (18 z 24) –
# kryterium kompletności stosowane w ocenie jakości powietrza (dyrektywa 2008/50/WE).
MIN_HOURS_PER_DAY = 18
HEATING_MONTHS = [10, 11, 12, 1, 2, 3]

# Klasy jakości dnia wg dobowego PM10 [µg/m3]:
#   dobra       PM10 <= 25        (do połowy dobowej wartości dopuszczalnej),
#   umiarkowana 25 < PM10 <= 50   (do dobowej wartości dopuszczalnej UE 50 µg/m3),
#   zła         PM10 > 50         (przekroczenie dobowej wartości dopuszczalnej).
PM10_CLASS_BINS = [25, 50]
PM10_CLASS_LABELS = ["dobra", "umiarkowana", "zła"]
PM10_DAILY_LIMIT = PM10_CLASS_BINS[1]  # 50 µg/m3 – dobowa norma UE

# --- Zmienne w danych dobowych: kolumna w bazie → nazwa na wykresach i stronie ---
POLLUTANTS = {"pm10": "PM10", "pm25": "PM2.5"}
WEATHER = {"temp_sr": "temperatura", "wiatr_sr": "prędkość wiatru", "opad": "opad",
           "wilgotnosc": "wilgotność", "cisnienie": "ciśnienie"}
SEASONS = ["letni", "grzewczy"]
