"""Uruchamia cały pipeline: python -m src.main"""
from src import analyze, clean, cluster, export, fetch_api, fetch_local, model


def main() -> None:
    print("[TK02] Dane lokalne: archiwa GIOŚ i IMGW")
    local = fetch_local.run()
    print("[TK03] Dane zdalne: API GIOŚ i GUS BDL")
    api = fetch_api.run()
    print("[TK04] Czyszczenie i zapis do SQLite")
    clean.run(local, api)
    print("[TK04] Analiza i wykresy")
    analyze.run()
    print("[TK04] Model klasyfikacji jakości dnia")
    model.run()
    print("[TK04] Klasteryzacja dni pogodowych")
    cluster.run()
    print("[TK05] Eksport danych dla strony WWW")
    export.run()


if __name__ == "__main__":
    main()
