"""TK03 – pobieranie danych ze źródeł zdalnych (API GIOŚ, API GUS BDL, JSON).

Klient HTTP obsługuje timeouty, ponowienia z wykładniczym odstępem (błędy sieci,
HTTP 429 i 5xx, z uwzględnieniem nagłówka Retry-After) oraz minimalny odstęp
między zapytaniami do danego API (limity zapytań).

Surowe odpowiedzi JSON są zapisywane w data/raw/api/. Lista stacji i dane GUS są
pobierane tylko wtedy, gdy pliku jeszcze nie ma; bieżące pomiary GIOŚ trafiają
do nowego pliku ze znacznikiem czasu przy każdym uruchomieniu.
"""
import json
import os
import time
from datetime import datetime

import pandas as pd
import requests

from src import config


class ApiClient:
    """Prosty klient JSON z limitem tempa, timeoutem i ponowieniami."""

    def __init__(self, base_url: str, min_interval: float, headers: dict | None = None,
                 timeout: float = 30, retries: int = 4, backoff: float = 2.0):
        self.base_url = base_url
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", **(headers or {})})
        self._last_request = 0.0
        self.dziennik: list[dict] = []  # każda próba zapytania – do panelu „Akwizycja danych”

    def _wait_for_slot(self) -> None:
        delay = self.min_interval - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        self._last_request = time.monotonic()

    def get(self, path: str, params=None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(self.retries + 1):
            self._wait_for_slot()
            start = time.monotonic()
            wpis = {"czas": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "adres": url}
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                wpis.update(adres=resp.url, status=resp.status_code, ms=round((time.monotonic() - start) * 1000),
                            bajty=len(resp.content))
                self.dziennik.append(wpis)
            except (requests.ConnectionError, requests.Timeout) as exc:
                self.dziennik.append(wpis | {"status": type(exc).__name__, "ms": round((time.monotonic() - start) * 1000)})
                if attempt == self.retries:
                    raise
                wait = self.backoff ** attempt
                print(f"  {type(exc).__name__} dla {url} – ponowienie za {wait:.0f} s")
                time.sleep(wait)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.retries:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else self.backoff ** attempt * 5
                print(f"  HTTP {resp.status_code} dla {url} – ponowienie za {wait:.0f} s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("nieosiągalne")


def _save_json(data, name: str) -> None:
    config.API_RAW.mkdir(parents=True, exist_ok=True)
    (config.API_RAW / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _load_json(name: str):
    path = config.API_RAW / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# --- GIOŚ ---------------------------------------------------------------------

def gios_client() -> ApiClient:
    # Limit: 1500 żądań/min dla bieżących danych i listy stanowisk; dla listy stacji
    # dokumentacja podaje "2 i 1500" – ostrożnie 1 żądanie na sekundę.
    # API zwraca JSON-LD i odrzuca "Accept: application/json" (HTTP 406).
    return ApiClient(config.GIOS_API_URL, min_interval=1.0, headers={"Accept": "application/ld+json"})


def _gios_all_pages(client: ApiClient, path: str, list_key: str) -> list[dict]:
    """Pobiera wszystkie strony wyniku (stronicowanie wg pola totalPages)."""
    items, page, total = [], 0, 1
    while page < total:
        data = client.get(path, params={"page": page, "size": 500})
        items.extend(data[list_key])
        total = data.get("totalPages", 1)
        page += 1
    return items


def fetch_gios_stations(client: ApiClient) -> pd.DataFrame:
    """Lista stacji pomiarowych w województwie podkarpackim (z pamięcią podręczną)."""
    name = "gios_stations.json"
    stations = _load_json(name)
    if stations is None:
        stations = _gios_all_pages(client, "station/findAll", "Lista stacji pomiarowych")
        _save_json(stations, name)
    df = pd.DataFrame(stations)
    df = df[df["Województwo"] == config.GIOS_API_VOIVODESHIP].reset_index(drop=True)
    for col in ["WGS84 φ N", "WGS84 λ E"]:
        df[col] = pd.to_numeric(df[col])
    return df


def fetch_gios_current(client: ApiClient, stations: pd.DataFrame) -> pd.DataFrame:
    """Bieżące pomiary PM10/PM2.5 (do 3 dób wstecz, czas lokalny) dla stacji miast."""
    selected = stations[stations["Kod stacji"].isin(config.GIOS_STATIONS)]
    snapshot, rows = {}, []
    for _, st in selected.iterrows():
        sensors = _gios_all_pages(client, f"station/sensors/{st['Identyfikator stacji']}",
                                  "Lista stanowisk pomiarowych dla podanej stacji")
        snapshot[st["Kod stacji"]] = {"stanowiska": sensors, "dane": {}}
        for sensor in sensors:
            if sensor["Wskaźnik - kod"] not in config.GIOS_API_PM_CODES:
                continue
            try:
                values = _gios_all_pages(client, f"data/getData/{sensor['Identyfikator stanowiska']}",
                                         "Lista danych pomiarowych")
            except requests.HTTPError as exc:
                # Stanowiska manualne (24g) nie mają danych bieżących – API zwraca 400
                # z kodem API-ERR-100003; lista stanowisk nie podaje typu pomiaru.
                if exc.response.status_code == 400 and "API-ERR-100003" in exc.response.text:
                    print(f"  pominięto stanowisko manualne {sensor['Identyfikator stanowiska']} "
                          f"({st['Kod stacji']}, {sensor['Wskaźnik - kod']})")
                    continue
                raise
            snapshot[st["Kod stacji"]]["dane"][sensor["Wskaźnik - kod"]] = values
            for v in values:
                rows.append({"kod_stacji": st["Kod stacji"], "wskaznik": sensor["Wskaźnik - kod"],
                             "czas": v["Data"], "wartosc": v["Wartość"]})
    _save_json(snapshot, f"gios_current_{datetime.now():%Y%m%d_%H%M}.json")
    df = pd.DataFrame(rows)
    df["czas"] = pd.to_datetime(df["czas"])
    return df


# --- GUS BDL ------------------------------------------------------------------

def bdl_client() -> ApiClient:
    # Limity dla użytkownika anonimowego: 5/s, 100/15 min, 1000/12 h, 10 000/7 dni.
    # Klucz API (nagłówek X-ClientId) jest opcjonalny – czytany ze zmiennej środowiskowej.
    key = os.environ.get("BDL_API_KEY")
    return ApiClient(config.BDL_API_URL, min_interval=1.0, headers={"X-ClientId": key} if key else None)


def fetch_bdl(client: ApiClient) -> pd.DataFrame:
    """Wybrane zmienne BDL dla powiatów miast z konfiguracji (z pamięcią podręczną)."""
    name = "bdl_powiaty.json"
    raw = _load_json(name)
    if raw is None:
        raw = {}
        params = [("format", "json")] + [("var-id", v) for v in config.BDL_VARIABLES] \
            + [("year", y) for y in config.YEARS]
        for city, c in config.CITIES.items():
            raw[city] = client.get(f"data/by-unit/{c['bdl']}", params=params)
        _save_json(raw, name)
    rows = []
    for city, data in raw.items():
        if city not in config.CITIES:  # zapisany plik może zawierać powiaty spoza aktualnej listy miast
            continue
        for res in data["results"]:
            for v in res["values"]:
                rows.append({"miasto": city, "jednostka": data["unitName"], "unit_id": data["unitId"],
                             "zmienna": config.BDL_VARIABLES[res["id"]], "rok": int(v["year"]),
                             "wartosc": v["val"]})
    return pd.DataFrame(rows)


def zapisz_dziennik(klienci: dict[str, ApiClient], z_dysku: dict[str, bool]) -> None:
    """Dziennik akwizycji z API: wszystkie zapytania HTTP i pliki wczytane z pamięci podręcznej."""
    wpisy = [w | {"api": nazwa} for nazwa, k in klienci.items() for w in k.dziennik]
    dziennik = {
        "uruchomienie": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "zapytania": wpisy,
        "pamiec_podreczna": [{"plik": plik, "z_dysku": z,
                              "data_pliku": datetime.fromtimestamp((config.API_RAW / plik).stat().st_mtime)
                              .strftime("%Y-%m-%d %H:%M")} for plik, z in z_dysku.items()],
    }
    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    (config.DATA_PROCESSED / "akwizycja_api.json").write_text(
        json.dumps(dziennik, ensure_ascii=False, indent=1), encoding="utf-8")


def run() -> dict[str, pd.DataFrame]:
    # Lista stacji i dane GUS są pobierane raz – sprawdzamy, czy tym razem pochodzą z dysku
    z_dysku = {plik: (config.API_RAW / plik).exists() for plik in ["gios_stations.json", "bdl_powiaty.json"]}
    gios, bdl = gios_client(), bdl_client()
    stations = fetch_gios_stations(gios)
    wynik = {
        "api_stations": stations,
        "api_current": fetch_gios_current(gios, stations),
        "bdl": fetch_bdl(bdl),
    }
    zapisz_dziennik({"GIOŚ": gios, "GUS BDL": bdl}, z_dysku)
    return wynik


if __name__ == "__main__":
    for key, df in run().items():
        print(f"{key}: {df.shape}")
