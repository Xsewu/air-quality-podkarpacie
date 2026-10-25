"""TK05 – buduje plik .exe z oknem aplikacji: python -m src.build_exe

Do pliku trafia program okna (src/app.py) i cała strona z danymi (web/), więc .exe działa
na każdym komputerze z Windows i przeglądarką Edge albo Chrome – bez Pythona i bez projektu.
Dane w środku pliku to stan z chwili budowania: po python -m src.main trzeba go zbudować od nowa.

Wymaga PyInstaller (pip install pyinstaller). Wynik: dist/Powietrze w Polsce.exe
"""
import os

from PyInstaller.__main__ import run as pyinstaller

from src import config

NAME = "Powietrze w Polsce"


def main() -> None:
    web = config.ROOT / "web"
    if not (web / "data" / "meta.json").exists():
        print("Brak danych strony (web/data). Uruchom najpierw: python -m src.main")
        return
    pyinstaller([
        str(config.ROOT / "src" / "app.py"),
        "--name", NAME,
        "--onefile",                              # jeden plik .exe
        "--windowed",                             # bez czarnego okna konsoli
        "--icon", str(web / "ikona.ico"),
        "--add-data", f"{web}{os.pathsep}web",    # strona z danymi w środku pliku
        "--paths", str(config.ROOT),              # żeby działał import src.config
        "--distpath", str(config.ROOT / "dist"),
        "--workpath", str(config.ROOT / "build"),
        "--specpath", str(config.ROOT / "build"),
        "--noconfirm", "--clean", "--log-level", "WARN",
    ])
    exe = config.ROOT / "dist" / f"{NAME}.exe"
    print(f"Gotowe: {exe.relative_to(config.ROOT)} ({exe.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
