import io
import zipfile

import pandas as pd

from src import config


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
    """Wczytuje z data/raw/ wszystkie surowe ramki danych."""
    return {
        "gios_stations": load_gios_stations(),
        "gios_pm10": load_gios_hourly("PM10"),
        "gios_pm25": load_gios_hourly("PM25"),
        "imgw_hourly": load_imgw_hourly(),
    }


if __name__ == "__main__":
    for key, df in run().items():
        print(f"{key}: {df.shape}")
