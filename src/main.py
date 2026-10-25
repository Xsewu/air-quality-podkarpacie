"""Uruchamia cały projekt po kolei: python -m src.main

Każdy krok to osobny moduł z funkcją run(); można je też uruchamiać pojedynczo
(np. python -m src.analyze), jeśli baza db/air.sqlite już istnieje.
"""
from src import analyze, clean, cluster, eda, export, fetch_api, fetch_local, significance


def main() -> None:
    print("[TK02] Dane z plików: archiwa GIOŚ i IMGW")
    local = fetch_local.run()
    print("[TK03] Dane z internetu: API GIOŚ")
    api = fetch_api.run()
    print("[TK04] Przygotowanie danych i zapis do SQLite")
    clean.run(local, api)
    print("[TK04] Eksploracja danych (EDA)")
    eda.run()
    print("[TK04] Pytania 1–4: sezony, pogoda, ranking miast, klasy dni")
    analyze.run()
    print("[TK04] Testy istotności")
    significance.run()
    print("[TK04] Typy dni pogodowych (klasteryzacja)")
    cluster.run()
    print("[TK05] Eksport danych dla strony")
    export.run()


if __name__ == "__main__":
    main()
