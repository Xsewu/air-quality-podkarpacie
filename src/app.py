"""TK05 – strona z wynikami w osobnym oknie aplikacji.

Uruchomienie: python -m src.app  (po python -m src.main) albo plik .exe (python -m src.build_exe).
Startuje lokalny serwer HTTP dla katalogu web/ i otwiera stronę w oknie Edge/Chrome
w trybie aplikacji (bez paska adresu i kart). Zamknięcie okna kończy program.
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src import config

# W pliku .exe (PyInstaller) strona jest rozpakowana do katalogu tymczasowego sys._MEIPASS
WEB = Path(getattr(sys, "_MEIPASS", config.ROOT)) / "web"
BROWSERS = [  # Edge jest w każdym Windows 10/11; Chrome jako zapas
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
]


class QuietHandler(SimpleHTTPRequestHandler):
    """Serwuje pliki z web/ bez wypisywania każdego żądania w konsoli."""

    def log_message(self, *args) -> None:
        pass

    def end_headers(self) -> None:
        # Bez pamięci podręcznej: po ponownym python -m src.main okno zawsze wczytuje nowe app.js i dane
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def find_browser() -> str | None:
    """Ścieżka do Edge albo Chrome (None, jeśli żadnego nie ma)."""
    for path in BROWSERS:
        if path.exists():
            return str(path)
    return shutil.which("msedge") or shutil.which("chrome")


def lock_held(lock: Path) -> bool:
    """Czy przeglądarka trzyma plik blokady profilu? Pliku otwartego przez działający proces nie da się usunąć."""
    try:
        lock.unlink(missing_ok=True)
        return False
    except PermissionError:
        return True


def wait_until_closed(profile: str) -> bool:
    """Czeka, aż użytkownik zamknie okno. Zwraca False, jeśli przeglądarka w ogóle nie wystartowała.

    Nie czekamy na uruchomiony proces, bo Edge czasem startuje drugi raz w nowym procesie, a pierwszy
    kończy się od razu. Patrzymy na plik blokady profilu – przeglądarka trzyma go otwartego, dopóki działa.
    """
    lock = Path(profile) / "lockfile"
    deadline = time.monotonic() + 30
    while not lock.exists():                     # przeglądarka jeszcze startuje
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)
    while True:
        while lock_held(lock):
            time.sleep(1)
        time.sleep(3)  # przy ponownym starcie Edge nowy proces zakłada blokadę po chwili
        if not lock_held(lock):
            return True


def remove_profile(path: str) -> None:
    """Usuwa tymczasowy profil przeglądarki. Po zamknięciu okna procesy Edge kończą się jeszcze
    przez chwilę i trzymają pliki, więc próbujemy kilka razy przez ok. 10 s."""
    for _ in range(20):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.5)


def notify(text: str) -> None:
    """Komunikat, na który użytkownik musi odpowiedzieć: w konsoli Enter, w pliku .exe (bez konsoli) okienko Windows."""
    if sys.stdin is None and hasattr(ctypes, "windll"):
        ctypes.windll.user32.MessageBoxW(None, text, "Powietrze w Polsce", 0x40)  # 0x40 = ikona informacji
    else:
        input(text + " Enter kończy program.")


def main() -> None:
    if not (WEB / "data" / "meta.json").exists():
        notify("Brak danych strony (web/data). Uruchom najpierw: python -m src.main.")
        return
    # Port 0 = system wybiera wolny port
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(WEB)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Serwer: {url}")

    browser = find_browser()
    try:
        if browser is None:
            webbrowser.open(url)
            notify("Nie znaleziono Edge ani Chrome – strona otwarta w domyślnej przeglądarce.")
            return
        # Nowy, pusty profil przy każdym uruchomieniu, żeby okno nie trafiło do innego, już działającego
        # okna przeglądarki (wtedy program wyłączyłby serwer, a okno pokazałoby błąd połączenia).
        print("Okno aplikacji otwarte – zamknij je, aby zakończyć.")
        profile = tempfile.mkdtemp(prefix="powietrze-okno-")
        try:
            subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={profile}", "--start-maximized",
                              "--no-first-run", "--no-default-browser-check"])
            opened = wait_until_closed(profile)
        finally:
            remove_profile(profile)
        if not opened:
            webbrowser.open(url)
            notify("Nie udało się otworzyć okna aplikacji – strona otwarta w zwykłej przeglądarce.")
    finally:
        server.shutdown()
        print("Zamknięto.")


if __name__ == "__main__":
    main()
