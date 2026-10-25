"""Jedyny punkt wejścia projektu: liczenie wyników, okno ze stroną i plik .exe.

python -m src.main             – otwiera stronę w oknie; jeśli wyników jeszcze nie ma, najpierw je liczy
python -m src.main --przelicz  – liczy wszystko od nowa (np. po zmianie miast), potem otwiera okno
python -m src.main --bez-okna  – tylko liczy (razem z --przelicz albo przy pierwszym uruchomieniu)
python -m src.main --exe       – buduje dist/Powietrze w Polsce.exe (wymaga: pip install pyinstaller)

Plik .exe zawiera okno i gotową stronę z danymi, więc niczego nie liczy – działa bez Pythona.
Każdy etap liczenia to osobny moduł z funkcją run(); można je uruchamiać pojedynczo
(np. python -m src.analyze), jeśli baza db/air.sqlite już istnieje.
"""
import argparse
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

NAME = "Powietrze w Polsce"
# W pliku .exe (PyInstaller) strona jest rozpakowana do katalogu tymczasowego sys._MEIPASS
WEB = Path(getattr(sys, "_MEIPASS", config.ROOT)) / "web"
BROWSERS = [  # Edge jest w każdym Windows 10/11; Chrome jako zapas
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
]
# Biblioteki potrzebne tylko do liczenia i budowania – plik .exe ich nie potrzebuje, więc są pomijane
NOT_IN_EXE = ["pandas", "numpy", "matplotlib", "sklearn", "scipy", "openpyxl", "requests",
              "PyInstaller", "setuptools", "PIL", "tkinter"]


# --- 1. Liczenie: wszystkie etapy po kolei ----------------------------------------------------------

def compute() -> None:
    # Import wewnątrz funkcji: samo okno (i plik .exe) nie potrzebuje bibliotek do liczenia
    from src import analyze, clean, cluster, eda, export, fetch_api, fetch_local, significance
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


# --- 2. Okno: lokalny serwer + przeglądarka w trybie aplikacji -------------------------------------

class QuietHandler(SimpleHTTPRequestHandler):
    """Serwuje pliki z web/ bez wypisywania każdego żądania w konsoli."""

    def log_message(self, *args) -> None:
        pass

    def end_headers(self) -> None:
        # Bez pamięci podręcznej: po przeliczeniu okno zawsze wczytuje nowe app.js i dane
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
        ctypes.windll.user32.MessageBoxW(None, text, NAME, 0x40)  # 0x40 = ikona informacji
    else:
        input(text + " Enter kończy program.")


def show_window() -> None:
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


# --- 3. Plik .exe: okno + strona z danymi w jednym pliku -------------------------------------------

def build_exe() -> None:
    from PyInstaller.__main__ import run as pyinstaller
    web = config.ROOT / "web"
    pyinstaller([
        str(Path(__file__)),
        "--name", NAME,
        "--onefile",                              # jeden plik .exe
        "--windowed",                             # bez czarnego okna konsoli
        "--icon", str(web / "ikona.ico"),
        "--add-data", f"{web}{os.pathsep}web",    # strona z danymi w środku pliku
        "--paths", str(config.ROOT),              # żeby działał import src.config
        *[arg for module in NOT_IN_EXE for arg in ("--exclude-module", module)],
        "--distpath", str(config.ROOT / "dist"),
        "--workpath", str(config.ROOT / "build"),
        "--specpath", str(config.ROOT / "build"),
        "--noconfirm", "--clean", "--log-level", "WARN",
    ])
    exe = config.ROOT / "dist" / f"{NAME}.exe"
    print(f"Gotowe: {exe.relative_to(config.ROOT)} ({exe.stat().st_size / 1024 / 1024:.1f} MB)")


# --- Start ----------------------------------------------------------------------------------------

def main() -> None:
    have_results = (WEB / "data" / "meta.json").exists()
    if getattr(sys, "frozen", False):  # plik .exe: dane są w środku, tylko okno
        if have_results:
            show_window()
        else:
            notify("W pliku brakuje danych strony – zbuduj go ponownie: python -m src.main --exe.")
        return

    parser = argparse.ArgumentParser(description="Jakość powietrza: liczenie wyników, okno ze stroną i plik .exe.")
    parser.add_argument("--przelicz", action="store_true", help="policz wszystko od nowa")
    parser.add_argument("--bez-okna", action="store_true", help="nie otwieraj okna po liczeniu")
    parser.add_argument("--exe", action="store_true", help="zbuduj plik .exe (dane muszą już być policzone)")
    args = parser.parse_args()

    if args.przelicz or not have_results:
        compute()
    if args.exe:
        build_exe()
    elif not args.bez_okna:
        show_window()


if __name__ == "__main__":
    main()
