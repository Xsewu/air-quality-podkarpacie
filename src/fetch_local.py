"""TK02 – wczytywanie danych ze źródeł lokalnych (archiwa GIOŚ XLSX, IMGW CSV).

Pliki archiwalne są pobierane jednorazowo do data/raw/ (istniejące pliki nie są
nadpisywane), a następnie wczytywane z dysku do DataFrame.
"""
import io
import zipfile
from pathlib import Path

import pandas as pd
import requests

from src import config

TIMEOUT = 600  # archiwa GIOŚ mają po ~50–70 MB


def _download(url: str, dest: Path) -> Path:
    """Pobiera plik, jeśli jeszcze go nie ma na dysku."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  pobieranie {url} -> {dest.relative_to(config.ROOT)}")
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(resp.content)
    tmp.replace(dest)
    return dest


# --- Pobieranie archiwów ------------------------------------------------------

def download_gios() -> None:
    """Archiwa roczne GIOŚ (ZIP z plikami XLSX) i metadane stacji (XLSX)."""
    _download(config.GIOS_DOWNLOAD_URL.format(file_id=config.GIOS_METADATA_ID),
              config.GIOS_RAW / "metadane.xlsx")
    extracted = config.GIOS_RAW / "extracted"
    for year, file_id in config.GIOS_ARCHIVE_IDS.items():
        archive = _download(config.GIOS_DOWNLOAD_URL.format(file_id=file_id),
                            config.GIOS_RAW / f"{year}.zip")
        with zipfile.ZipFile(archive) as zf:
            for pollutant in config.GIOS_POLLUTANTS:
                name = f"{year}_{pollutant}_1g.xlsx"
                if not (extracted / name).exists():
                    zf.extract(name, extracted)


def download_imgw() -> None:
    """Terminowe (godzinowe) dane synoptyczne IMGW i plik nagłówka kolumn."""
    term = f"{config.IMGW_BASE_URL}/terminowe/synop"
    # Nazwa pliku nagłówka zawiera "ł" i "ó" – w URL zakodowane jako UTF-8
    _download(f"{term}/s_t_nag%c5%82%c3%b3wek.csv", config.IMGW_RAW / "s_t_naglowek.csv")
    for year in config.YEARS:
        for code in config.IMGW_STATIONS:
            name = f"{year}_{code}_s.zip"
            _download(f"{term}/{year}/{name}", config.IMGW_RAW / "terminowe" / name)


# --- Wczytywanie GIOŚ ---------------------------------------------------------

def load_gios_stations() -> pd.DataFrame:
    """Metadane stacji (arkusz STACJE) dla stacji z konfiguracji."""
    df = pd.read_excel(config.GIOS_RAW / "metadane.xlsx", sheet_name="STACJE")
    df = df[df["Kod stacji"].isin(config.GIOS_STATIONS)]
    return df[["Kod stacji", "Nazwa stacji", "Typ stacji", "Typ obszaru", "Miejscowość",
               "Data uruchomienia", "Data zamknięcia", "WGS84 φ N", "WGS84 λ E"]].reset_index(drop=True)


def load_gios_hourly(pollutant: str) -> pd.DataFrame:
    """Pomiary godzinowe danego wskaźnika ze wszystkich lat w formacie długim.

    Plik XLSX ma 6 wierszy nagłówka (wiersz 1 = kod stacji), następnie w kolumnie 0
    znacznik czasu, a w kolejnych kolumnach wartości dla poszczególnych stacji.
    """
    frames = []
    for year in config.YEARS:
        path = config.GIOS_RAW / "extracted" / f"{year}_{pollutant}_1g.xlsx"
        raw = pd.read_excel(path, header=None)
        codes = raw.iloc[1, 1:].tolist()
        data = raw.iloc[config.GIOS_HEADER_ROWS:].copy()
        data.columns = ["czas"] + codes
        keep = [c for c in codes if c in config.GIOS_STATIONS]
        long = data[["czas"] + keep].melt(id_vars="czas", var_name="kod_stacji", value_name="wartosc")
        # W arkuszu wartości są mieszanką int/float – ujednolicenie do float
        long["wartosc"] = pd.to_numeric(long["wartosc"], errors="coerce")
        long["wskaznik"] = pollutant
        frames.append(long)
    return pd.concat(frames, ignore_index=True)


# --- Wczytywanie IMGW ---------------------------------------------------------

def load_imgw_hourly() -> pd.DataFrame:
    """Terminowe (godzinowe) dane synoptyczne s_t: CSV bez nagłówka, cp1250, nazwy kolumn z pliku nagłówka."""
    header = (config.IMGW_RAW / "s_t_naglowek.csv").read_text(encoding=config.IMGW_ENCODING).strip().split(",")
    frames = []
    for year in config.YEARS:
        for code in config.IMGW_STATIONS:
            with zipfile.ZipFile(config.IMGW_RAW / "terminowe" / f"{year}_{code}_s.zip") as zf:
                with zf.open(f"s_t_{code}_{year}.csv") as fh:
                    frames.append(pd.read_csv(io.TextIOWrapper(fh, encoding=config.IMGW_ENCODING),
                                              header=None, names=header, dtype={"NSP": str},
                                              low_memory=False))
    return pd.concat(frames, ignore_index=True)


def run() -> dict[str, pd.DataFrame]:
    """Pobiera brakujące archiwa i zwraca wszystkie surowe ramki danych."""
    download_gios()
    download_imgw()
    return {
        "gios_stations": load_gios_stations(),
        "gios_pm10": load_gios_hourly("PM10"),
        "gios_pm25": load_gios_hourly("PM25"),
        "imgw_hourly": load_imgw_hourly(),
    }


if __name__ == "__main__":
    for key, df in run().items():
        print(f"{key}: {df.shape}")
