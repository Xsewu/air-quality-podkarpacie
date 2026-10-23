"""TK05 – strona z wynikami w osobnym oknie aplikacji.

Uruchomienie: python -m src.app  (po python -m src.main)
Startuje lokalny serwer HTTP dla katalogu web/ i otwiera stronę w oknie Edge/Chrome
w trybie aplikacji (bez paska adresu i kart). Zamknięcie okna kończy program.
"""
import shutil
import subprocess
import tempfile
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src import config

WEB = config.ROOT / "web"
PRZEGLADARKI = [  # Edge jest w każdym Windows 10/11; Chrome jako zapas
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
]


class CichyHandler(SimpleHTTPRequestHandler):
    """Serwuje pliki z web/ bez wypisywania każdego żądania w konsoli."""

    def log_message(self, *args) -> None:
        pass

    def end_headers(self) -> None:
        # Bez pamięci podręcznej: po ponownym python -m src.main okno zawsze wczytuje nowe app.js i dane
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def znajdz_przegladarke() -> str | None:
    for sciezka in PRZEGLADARKI:
        if sciezka.exists():
            return str(sciezka)
    return shutil.which("msedge") or shutil.which("chrome")


def main() -> None:
    if not (WEB / "data" / "meta.json").exists():
        print("Brak danych strony (web/data). Uruchom najpierw: python -m src.main")
        return
    # Port 0 = system wybiera wolny port
    serwer = ThreadingHTTPServer(("127.0.0.1", 0), partial(CichyHandler, directory=str(WEB)))
    threading.Thread(target=serwer.serve_forever, daemon=True).start()
    adres = f"http://127.0.0.1:{serwer.server_address[1]}/"
    print(f"Serwer: {adres}")

    przegladarka = znajdz_przegladarke()
    try:
        if przegladarka is None:
            webbrowser.open(adres)
            input("Nie znaleziono Edge ani Chrome – strona otwarta w domyślnej przeglądarce. Enter kończy program.")
            return
        # Nowy, pusty profil przy każdym uruchomieniu: proces przeglądarki żyje dokładnie tak długo jak okno.
        # Ze stałym profilem, gdy działało jeszcze poprzednie okno, Edge przekazywał adres staremu procesowi
        # i od razu kończył polecenie (kod 21) – program wyłączał wtedy serwer, a okno pokazywało błąd połączenia.
        print("Okno aplikacji otwarte – zamknij je, aby zakończyć.")
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="powietrze-okno-", ignore_cleanup_errors=True) as profil:
            wynik = subprocess.run([przegladarka, f"--app={adres}", f"--user-data-dir={profil}", "--start-maximized",
                                    "--no-first-run", "--no-default-browser-check"], check=False)
        if time.monotonic() - start < 3:
            # Przeglądarka zakończyła się od razu (oddała okno innemu procesowi albo nie wystartowała) – serwer zostaje
            input(f"Przeglądarka zakończyła się od razu (kod {wynik.returncode}). Strona działa pod adresem {adres}"
                  " – Enter kończy program.")
    finally:
        serwer.shutdown()
        print("Zamknięto.")


if __name__ == "__main__":
    main()
