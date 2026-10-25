# Jakość powietrza w dużych miastach Polski: analiza PM10 i PM2.5 w zależności od pogody i pory roku

Projekt zaliczeniowy z przedmiotu **Techniki analizy danych** (Informatyka, sem. 5, specjalność AI, Politechnika Rzeszowska).

Badamy pył zawieszony PM10 i PM2.5 w czterech miastach z różnych części Polski: **Warszawie, Katowicach,
Wrocławiu i Gdańsku**, w latach **2023–2025**. Porównujemy go z pogodą (IMGW). Wybraliśmy miasta, w których
**i pył, i pogoda są mierzone w tym samym mieście**, a stacja pyłu ma pełne PM10 i PM2.5 z 3 lat.

## Treści programowe

| Kod | Wymaganie | Gdzie w projekcie |
|-----|-----------|-------------------|
| TK01 | Źródła i formaty danych | sekcja [Źródła i formaty danych](#tk01--źródła-i-formaty-danych) |
| TK02 | Dane z plików | [`src/fetch_local.py`](src/fetch_local.py) – archiwa GIOŚ (XLSX), dane IMGW (CSV) |
| TK03 | Dane z internetu | [`src/fetch_api.py`](src/fetch_api.py) – API GIOŚ (JSON-LD) |
| TK04 | Analiza, czyszczenie, zapis | [`src/quality.py`](src/quality.py), [`src/clean.py`](src/clean.py), [`src/eda.py`](src/eda.py), [`src/analyze.py`](src/analyze.py), [`src/significance.py`](src/significance.py), [`src/cluster.py`](src/cluster.py) |
| TK05 | Prezentacja wyników | [`src/export.py`](src/export.py), strona [`web/`](web/index.html), okno aplikacji [`src/app.py`](src/app.py) |

## Uruchomienie

Potrzebny jest Python 3.11+ i internet przy pierwszym uruchomieniu (archiwa GIOŚ mają ok. 170 MB).

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows; Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

python -m src.main                # wszystko: pobranie → czyszczenie → EDA → analiza → testy → klastry → eksport
python -m src.app                 # strona w osobnym oknie
python -m http.server -d web      # albo strona w przeglądarce: http://localhost:8000
```

- Pierwsze uruchomienie pobiera dane do `data/raw/` (kilka minut). Kolejne korzystają z plików na dysku.
  Pobrane pliki nigdy nie są nadpisywane.
- Każde uruchomienie pobiera też najnowsze pomiary z API GIOŚ – w panelu 3 widać je na mapie i na tle lat 2023–2025.
- Strony nie da się otworzyć prosto z dysku (`file://`), bo przeglądarka zablokuje pliki z danymi.
- `python -m src.app` otwiera stronę w oknie Edge (lub Chrome) bez paska adresu. Zamknięcie okna kończy program.
- **Plik .exe** (Windows): `pip install pyinstaller`, a potem `python -m src.build_exe`. Powstaje
  `dist/Powietrze w Polsce.exe` (ok. 8 MB) z oknem aplikacji i całą stroną z danymi w środku – działa też
  na komputerze bez Pythona (potrzebny Edge albo Chrome), więc można go komuś wysłać. Dane w pliku to stan
  z chwili budowania: po `python -m src.main` trzeba go zbudować od nowa. Windows może ostrzec przed programem
  od nieznanego wydawcy – wtedy „Więcej informacji” → „Uruchom mimo to”.
- Etapy można uruchamiać osobno, np. `python -m src.analyze` (analiza, testy, klastry i eksport potrzebują bazy
  `db/air.sqlite`).

**Panele strony:**

- **A – Akwizycja danych:** skąd są dane, pobrane pliki i fragmenty surowych danych.
- **P – Przygotowanie danych:** jak z surowych pomiarów powstają czyste dane – kroki, kontrola jakości, filtracja.
- **E – Eksploracja (EDA):** typowe wartości, rozrzut i rozkład każdej zmiennej, reguła 3σ, testy istotności.
- **1–5:** odpowiedzi na pytania badawcze i typy dni pogodowych.

## TK01 – Źródła i formaty danych

| Źródło | Co pobieramy | Format | Dostęp |
|--------|--------------|--------|--------|
| **GIOŚ – archiwum** ([powietrze.gios.gov.pl/pjp/archives](https://powietrze.gios.gov.pl/pjp/archives)) | pomiary godzinowe PM10 i PM2.5 z lat 2023–2025 (cała Polska) | ZIP z plikami **XLSX** | plik (TK02) |
| **GIOŚ – metadane stacji** | kody, nazwy, typ i położenie stacji | **XLSX** | plik (TK02) |
| **IMGW** ([danepubliczne.imgw.pl](https://danepubliczne.imgw.pl)) | pogoda co godzinę ze stacji 375, 560, 424, 155 | ZIP z plikami **CSV**, kodowanie **cp1250** | plik (TK02) |
| **GIOŚ – API** ([api.gios.gov.pl](https://api.gios.gov.pl/pjp-api/swagger-ui/index.html)) | lista stacji i najnowsze pomiary | **JSON-LD** | internet (TK03) |

Co trzeba wiedzieć o formatach (sprawdzone na pobranych plikach):

- **GIOŚ XLSX:** 6 wierszy nagłówka, potem godziny i po jednej kolumnie na stację. Czas jest zimowy (CET),
  a godzina oznacza **koniec** pomiaru (01:00 to pomiar z 00:00–01:00).
- **IMGW CSV:** plik nie ma nagłówka – nazwy kolumn są w osobnym pliku. Kod `8` oznacza brak pomiaru (czasem
  z wpisanym 0.0), kod `9` – brak zjawiska (np. brak deszczu). Czas jest w UTC. Opad podany jest jako sumy
  z 6 godzin.
- **Dlaczego nie bierzemy gotowych danych dobowych IMGW:** dobowy opad IMGW liczony jest od 6:00 UTC do 6:00 UTC
  następnego dnia, więc nie pasuje do doby pyłu. Dlatego wszystko liczymy z danych godzinowych.
- **API GIOŚ:** wymaga nagłówka `Accept: application/ld+json`. Stanowiska ręczne zwracają błąd 400 – najnowsze dane
  mają tylko stanowiska automatyczne.

## Przygotowanie danych (TK04)

**Wybór miast.** IMGW publikuje dane godzinowe za 2023–2025 z 59 stacji synoptycznych. Z miast, które mają taką
stację, wzięliśmy duże miasta z różnych części kraju, w których jest stacja **tła miejskiego** GIOŚ (nie przy
ruchliwej ulicy) z pełnym PM10 i PM2.5 we wszystkich 3 latach. Kraków odpadł – żadna jego stacja tła miejskiego
nie ma kompletu PM10 i PM2.5 we wszystkich 3 latach. W mieście z kilkoma stacjami bierzemy tę z najpełniejszymi danymi.

| Miasto | Stacja GIOŚ | Stacja pogodowa IMGW |
|--------|-------------|----------------------|
| Warszawa | MzWarBajkowa | 375 Warszawa (Okęcie) |
| Katowice | SlKatoKossut | 560 Katowice-Muchowiec |
| Wrocław | DsWrocWybCon | 424 Wrocław-Strachowice |
| Gdańsk | PmGdaLeczkow | 155 Gdańsk-Świbno |

**Kroki** ([`src/clean.py`](src/clean.py), kontrola jakości w [`src/quality.py`](src/quality.py)):

1. **Czas.** Wszystko przeliczamy na czas zimowy (CET), żeby doba pyłu i pogody była ta sama.
2. **Wartości niemożliwe.** Stężenie poniżej 0 albo PM2.5 większe niż PM10 (PM2.5 to część PM10) zamieniamy
   na brak. Ujemnych nie było. PM2.5 > PM10 wystąpiło w **15 godzinach we Wrocławiu** – nie wiadomo, który
   pomiar jest zły, więc brakiem stają się oba (30 wartości).
3. **Błędy czujników.** Wysoka wartość może być smogiem albo awarią. Dla wartości powyżej **30 µg/m³**
   sprawdzamy dwie rzeczy:

   | Warunek | Co oznacza |
   |---|---|
   | a) ponad 3 razy więcej niż na innych stacjach w tej samej godzinie | stacja widzi coś, czego nie widzą inne |
   | b) ponad 3 razy więcej niż godzinę przed i po | nagły skok, który zaraz znika |

   **Oba warunki naraz = błąd – usuwamy.** Jeden warunek = tylko oznaczenie, wartość zostaje. Każde miasto ma
   jedną stację, więc porównujemy z pozostałymi miastami.

   Przykłady:
   - **Warszawa, PM10, 17.10.2023, godz. 8:** 201,1 µg/m³, inne stacje 32,7, godzinę przed 27,0, po 29,3
     → oba warunki → **usunięta**.
   - **Wrocław, PM10, 5.03.2025, godz. 20:** 228,4 µg/m³, inne stacje 21,3, godzinę przed 171,4, po 152,6
     → tylko a), bo smog trwał kilka godzin → **zostaje**. Gdybyśmy usuwali „wszystko powyżej 150”,
     stracilibyśmy prawdziwy epizod smogowy.

   | Stacja | Wskaźnik | Usunięte | Tylko a) – zostaje | Tylko b) – zostaje |
   |---|---|---|---|---|
   | Warszawa | PM10 | 12 | 238 | 0 |
   | Warszawa | PM2.5 | 1 | 191 | 0 |
   | Katowice | PM10 | 3 | 610 | 0 |
   | Katowice | PM2.5 | 0 | 357 | 0 |
   | Wrocław | PM10 | 6 | 331 | 7 |
   | Wrocław | PM2.5 | 1 | 106 | 1 |
   | Gdańsk | PM10 | 7 | 232 | 1 |
   | Gdańsk | PM2.5 | 0 | 162 | 0 |

   Usunięto **30 z 207 969** pomiarów (0,01%), a 2236 tylko oznaczono. Najczęściej od innych odstają
   **Katowice** (970 godzin, 83% zimą) – to typowy smog śląski, którego nie ma w pozostałych miastach.
4. **Braki w pogodzie.** Kod 8 zamieniamy na brak, opad z kodem 9 na 0 mm. Braków nie zgadujemy.
5. **Średnie dobowe.** Średnią z doby liczymy tylko, gdy jest co najmniej **18 z 24 godzin** (tak każe dyrektywa
   UE 2008/50/WE). Opad liczymy tylko przy wszystkich 4 sumach 6-godzinnych.
6. **Zapis do bazy** SQLite `db/air.sqlite`:

| Tabela | Co zawiera | Wiersze |
|---|---|---|
| `stacje`, `stacje_meteo` | stacje pyłu i pogody | 4, 4 |
| `kontrola_pomiarow`, `kontrola_flagi` | wynik kontroli jakości i każda oznaczona godzina | 8, 2 266 |
| `przyklad_doby` | doba z przykładu powyżej, przed i po kontroli | 24 |
| `pm_dobowe` | średnie dobowe PM10 i PM2.5 | 4 384 |
| `pogoda_dobowa` | temperatura, opad, wiatr, wilgotność, ciśnienie | 4 383 |
| `pm_biezace` | najnowsze pomiary z API GIOŚ | kilkaset |
| `dzien_miasto_pelny` (widok) | wszystkie dni: pył + pogoda + sezon + klasa dnia | 4 384 |
| `dni_kompletne`, `filtracja_braki` | pełne dni i powody odrzucenia pozostałych | 1036, 16 |
| `dzien_miasto` (widok) | **dane do analizy** – tylko pełne dni | 4 144 |
| `wyniki_*`, `klastry_*` | wyniki analiz | – |

7. **Filtracja.** Dzień zostaje tylko wtedy, gdy **wszystkie 4 miasta** mają PM10, PM2.5 i pełną pogodę.
   Dzięki temu miasta i sezony porównujemy na tych samych dniach. Wszystkie analizy korzystają z tych danych.

**Ile danych brakowało** (z 1096 dni):

| Miasto | Dni z PM10 i PM2.5 | Brak |
|---|---|---|
| Warszawa | 1088 | 0,7% |
| Katowice | 1065 | 2,8% |
| Wrocław | 1086 | 0,9% |
| Gdańsk | 1088 | 0,7% |

**Wynik filtracji:** zostaje **1036 z 1096 dni (95%)**, czyli 4144 dni-miasto (4 × 1036). Powody odrzucenia:

| Dni z brakiem | PM10 | PM2.5 | opad | reszta pogody |
|---|---|---|---|---|
| Warszawa | 8 | 8 | 1 | – |
| Katowice | **31** | **31** | 2 | – |
| Wrocław | 10 | 10 | 1 | – |
| Gdańsk | 8 | 8 | 4 | po 3 |

Jednego dnia może brakować kilku rzeczy, więc liczby się nie sumują. Najwięcej dni zabrała dziura w Katowicach
w maju 2024 (20 dni).

## Klasy jakości dnia

Klasę dnia wyznacza **średnie dobowe PM10** (progi w [`src/config.py`](src/config.py)):

| Klasa | PM10 | Dlaczego |
|---|---|---|
| dobra | do 25 µg/m³ | do połowy normy |
| umiarkowana | 25–50 µg/m³ | do normy UE |
| zła | ponad 50 µg/m³ | norma przekroczona |

W danych do analizy: 2931 dni dobrych, 1062 umiarkowane, 151 złych (dni-miasto).

## Eksploracja danych (EDA)

Zanim wybierzemy metody, sprawdzamy, jak wyglądają dane (wykres: `data/processed/rozklady.png`, panel E na stronie).
Pył i pogodę liczymy na 4144 dniach-miasto (każde miasto ma swoją stację pogodową).

| Zmienna | n | Średnia | Mediana | Dominanta | Odch. std. | Wariancja | Wsp. zmienności | Skośność | Kurtoza | Kształt |
|---|---|---|---|---|---|---|---|---|---|---|
| PM10 [µg/m³] | 4144 | 22,2 | 19,2 | 14–16 | 12,9 | 167,2 | 58% | 2,38 | 10,72 | mocno skośny w prawo (po logarytmie: 0,21) |
| PM2.5 [µg/m³] | 4144 | 16,0 | 12,9 | 8–10 | 10,2 | 104,4 | 64% | 2,56 | 13,50 | mocno skośny w prawo (po logarytmie: 0,47) |
| temperatura [°C] | 4144 | 10,3 | 9,6 | 18–19 | 7,7 | 58,8 | – | 0,02 | −0,99 | symetryczny, dwa szczyty: zima i lato |
| wiatr [m/s] | 4144 | 3,1 | 2,8 | 2,0–2,5 | 1,4 | 2,0 | 45% | 0,99 | 1,13 | lekko skośny w prawo |
| opad [mm] | 4144 | 1,7 | 0,0 | 0–1 | 5,1 | 26,4 | 311% | 11,66 | 235,05 | mocno skośny; 57% dni bez opadu |
| wilgotność [%] | 4144 | 76,2 | 77,7 | 78–80 | 12,4 | 154,0 | 16% | −0,46 | −0,37 | bliski normalnemu |
| ciśnienie [hPa] | 4144 | 1000,4 | 1001,5 | 1000–1002 | 14,1 | 199,1 | 1% | −0,17 | −0,33 | bliski normalnemu |

Jak czytać tabelę:

- **Dominanta** to najczęstszy przedział wartości (przy pomiarach ciągłych pojedyncze liczby prawie się nie powtarzają).
- **Wariancja** jest w jednostkach do kwadratu, dlatego łatwiej czytać odchylenie standardowe.
- **Współczynnika zmienności** nie liczymy dla temperatury – skala °C nie ma prawdziwego zera.
- **Rozkład normalny** ma skośność 0 i kurtozę 0. Skośność powyżej 1 oznacza wyraźnie niesymetryczny rozkład.
- **Testu normalności nie liczymy:** wymagałby biblioteki `scipy`, a przy tysiącach danych i tak wykazuje
  „nienormalność” przy drobnych odchyleniach. Oceniamy kształt ze skośności, kurtozy i histogramu.

**Co z tego wynika:**

- **Pył jest mocno skośny:** większość dni jest czysta, a kilka bardzo brudnych. Dlatego średnia (22,2) jest
  wyższa od mediany (19,2), a korelacje liczymy metodą Spearmana, która nie wymaga rozkładu normalnego.
- **Regresja liniowa** wymaga rozkładu normalnego – dla pyłu trzeba by go najpierw zlogarytmować.
- **Opad:** ponad połowa dni bez deszczu, kilka ulew (np. 140,5 mm w Gdańsku 28.07.2025, 118,1 mm w Katowicach
  14.09.2024 – powódź), więc w klasteryzacji używamy jego logarytmu.
- **Różne skale zmiennych** (wariancja PM10 167,2, wiatru 2,0) – przed klasteryzacją sprowadzamy je do jednej skali.
- **Ciśnienie:** odchylenie 14,1 hPa, ale na jednej stacji tylko 8–10 – resztę daje różna wysokość stacji
  (Gdańsk-Świbno średnio 1014 hPa, Katowice 983 hPa). Dlatego w klasteryzacji liczy się tylko różnica
  od średniej danej stacji.
- **Temperatura** ma dwa szczyty (zima i lato), więc wyniki liczymy też osobno dla sezonów.

### Reguła 68–95–99,7 (3σ)

W rozkładzie normalnym 68% danych leży w odległości 1 odchylenia od średniej, 95% – 2 odchyleń, 99,7% – 3 odchyleń.

| Zmienna | Przekształcenie | w ±1σ | w ±2σ | w ±3σ | poza ±3σ |
|---|---|---|---|---|---|
| *rozkład normalny* | – | *68,3%* | *95,4%* | *99,7%* | *0,3%* |
| PM10 | bez zmian | **80,3%** | 95,8% | **98,2%** | **73 (1,8%)** |
| PM10 | logarytm | 68,8% | 95,1% | 99,6% | 17 (0,4%) |
| PM2.5 | bez zmian | **82,9%** | 95,6% | **98,5%** | **64 (1,5%)** |
| PM2.5 | logarytm | 67,3% | 96,2% | 99,7% | 13 (0,3%) |
| temperatura | bez zmian | **59,6%** | **98,7%** | 100,0% | 1 (0,0%) |
| wiatr | bez zmian | 70,5% | 95,9% | 98,9% | **45 (1,1%)** |
| opad | bez zmian | **93,1%** | 97,1% | **98,5%** | **62 (1,5%)** |
| opad | logarytm | **83,6%** | 93,4% | 98,3% | **69 (1,7%)** |
| wilgotność | bez zmian | 65,7% | 96,1% | 99,9% | 3 (0,1%) |
| ciśnienie | bez zmian | 65,9% | 96,1% | 99,9% | 3 (0,1%) |

- **Pył nie pasuje do reguły.** Powyżej średnia + 3σ (61,0 µg/m³) jest 73 dni PM10 i **wszystkie to dni ze smogiem**
  (73 ze 151). Usunięcie ich jako „odstających” wycięłoby połowę smogu. Dlatego błędy czujników szukamy innym
  sposobem (krok 3 przygotowania).
- **Po logarytmie** pył pasuje do reguły prawie idealnie. Wilgotność i ciśnienie pasują bez zmian.

## Testy istotności (p-wartości)

Testy sprawdzają, czy wyniki pytań 1–3 mogą być przypadkiem. Wynik jest istotny, gdy p < 0,05.
Liczymy je bez `scipy` – wzorami i przez losowanie (`numpy`).

| Pyt. | Test | Co sprawdza | Dane | Wynik | p | Wielkość efektu |
|---|---|---|---|---|---|---|
| 1 | U Manna–Whitneya | czy zimą jest tyle samo PM10 co latem | 2148 + 1996 dni | z = 11,70 | < 0,001 | P(dzień zimowy > letni) = 0,61 |
| 1 | U Manna–Whitneya | to samo dla PM2.5 | 2148 + 1996 dni | z = 25,33 | < 0,001 | P(dzień zimowy > letni) = 0,73 |
| 1 | permutacyjny | to samo na średnich miesięcznych (PM10) | 36 miesięcy | różnica 5,84 µg/m³ | < 0,001 | – |
| 1 | permutacyjny | to samo na średnich miesięcznych (PM2.5) | 36 miesięcy | różnica 8,12 µg/m³ | < 0,001 | – |
| 2 | Spearmana | czy pary zmiennych są powiązane | 4144 / 2148 / 1996 dni | istotne już od \|r\| 0,030 / 0,042 / 0,044 | – | istotne pary: 16 / 19 / 16 z 21 |
| 3 | Friedmana | czy miasta mają tyle samo PM10 (te same dni) | 1036 dni × 4 miasta | Q = 380,6 | < 0,001 | W Kendalla = 0,12 |
| 3 | Friedmana | to samo na średnich miesięcznych | 36 miesięcy × 4 miasta | Q = 32,2 | < 0,001 | W Kendalla = 0,30 |

Dla PM2.5 miasta też różnią się istotnie (p < 0,001), w kolejności Katowice > Warszawa > Gdańsk > Wrocław.

- **Pytanie 1:** zimą jest istotnie więcej pyłu. Losowy dzień zimowy ma wyższe PM10 od letniego
  z prawdopodobieństwem 0,61 (PM2.5: 0,73).
- **Pytanie 2:** przy tylu danych istotne są nawet bardzo słabe związki, więc ważniejsza jest siła związku.
  Nieistotne są głównie pary z ciśnieniem (np. PM10 i ciśnienie, r ≈ 0).
- **Pytanie 3:** miasta różnią się istotnie, w tej samej kolejności co w rankingu.
- **Uwaga:** smog trwa kilka dni, więc kolejne dni są do siebie podobne (autokorelacja 0,64–0,76). Przez to
  p z danych dziennych wychodzi za małe. Dlatego testy powtórzyliśmy na średnich miesięcznych – wnioski się
  nie zmieniły.

## Wyniki – odpowiedzi na pytania badawcze

Wszystkie liczby dotyczą **1036 pełnych dni** z lat 2023–2025 (4144 dni-miasto). Wykresy są w `data/processed/`
i na stronie.

### 1. Sezon grzewczy (paź–mar) a letni (kwi–wrz)

Zimą pyłu jest wyraźnie więcej, a najbardziej rośnie **PM2.5** – drobny pył z pieców w domach:

| | Sezon grzewczy | Sezon letni | Ile razy więcej |
|---|---|---|---|
| PM10 | 25,1 µg/m³ | 19,2 µg/m³ | **1,31×** |
| PM2.5 | 19,9 µg/m³ | 11,8 µg/m³ | **1,69×** |
| Dni z PM10 > 50 µg/m³ | 140 | 11 | – |
| Pełne dni / wszystkie dni | 537 / 547 | 499 / 549 | – |

- PM10 rośnie zimą najbardziej w **Katowicach (1,50×)**, potem Warszawa 1,39× i Wrocław 1,35×.
  **Gdańsk to wyjątek: PM10 zimą nie rośnie (0,98×)**, a PM2.5 tylko 1,29× – wiatr znad morza rozwiewa dym.
  PM2.5 rośnie najmocniej w Warszawie (1,96×) i Katowicach (1,82×).
- Najgorszy miesiąc: luty w Katowicach (PM10 36,5 µg/m³), marzec w Warszawie (29,8) i Wrocławiu (31,5).
  Najczystszy jest lipiec (15,0 w Warszawie – 17,5 we Wrocławiu); w Gdańsku październik (13,4).
- 93% dni z przekroczoną normą przypada na sezon grzewczy. Różnica jest istotna (p < 0,001).

### 2. Wpływ temperatury, wiatru i opadów

Korelacja PM10 z pogodą (metoda Spearmana, dane z 4 miast razem):

| Zmienna | Cały rok | Sezon grzewczy | Sezon letni |
|---|---|---|---|
| wiatr | −0,39 | **−0,57** | −0,23 |
| opad | −0,27 | **−0,35** | −0,20 |
| temperatura | −0,18 | −0,26 | **+0,18** |
| wilgotność | +0,06 | +0,10 | −0,22 |
| ciśnienie | −0,01 | +0,01 | −0,02 |

- **Wiatr** najmocniej obniża pył zimą – rozwiewa go. To najsilniejszy związek w całym badaniu.
- **Deszcz** wymywa pył z powietrza (zimą −0,35).
- **Temperatura** działa różnie: zimą zimno = więcej palenia w piecach = więcej pyłu, a latem ciepło = sucho =
  więcej kurzu. Dlatego w skali całego roku związek jest słaby. Dla PM2.5 (z pieców) jest wyraźniejszy: −0,40.
- **Ciśnienie** samo w sobie nie ma związku z pyłem (r ≈ 0).
- Związki są umiarkowane lub słabe (|r| ≤ 0,57), czyli pogoda tłumaczy tylko część zmian.

**Macierz korelacji** (wszystkie pary 7 zmiennych, panel 2 na stronie, `data/processed/korelacje.png`):

- **PM10 i PM2.5** idą w parze: 0,90 w całym roku, 0,97 zimą (to samo źródło – piece) i 0,83 latem.
- Najmocniej powiązane zmienne pogodowe to opad i wilgotność latem (+0,52) oraz temperatura i wilgotność (−0,49).
  Żadna para pogody nie przekracza 0,60, więc żadna zmienna nie powtarza w całości innej.

### 3. Ranking miast

| # | Miasto | PM10 śr. | PM2.5 śr. | Dni PM10 > 50 rocznie* |
|---|---|---|---|---|
| 1 | Katowice | **25,5** | **17,9** | **19,3** |
| 2 | Wrocław | 23,2 | 15,3 | 15,0 |
| 3 | Warszawa | 22,1 | 16,6 | 13,0 |
| 4 | Gdańsk | 18,2 | 14,2 | 3,0 |

\* liczone tylko na pełnych dniach, więc mniej niż w rzeczywistości.

- Najgorsze powietrze mają **Katowice** (i PM10, i PM2.5) – Śląsk z gęstą zabudową i piecami węglowymi.
  Najczystszy jest **Gdańsk** nad morzem.
- Normę 35 dni z przekroczeniem w roku sprawdzamy na wszystkich dniach z pomiarem. Żadne miasto jej nie przekroczyło,
  ale liczba takich dni rośnie: Wrocław 9 → 11 → 25, Warszawa 8 → 12 → 20, Katowice 12 → 23 → 23,
  Gdańsk 0 → 6 → 5 (2023 → 2025).
- Różnice między miastami są istotne (test Friedmana, p < 0,001).

**Teraz na tle historii (API GIOŚ).** Z najnowszych pomiarów liczymy średnią z ostatnich 24 godzin (co najmniej 18 h)
i porównujemy ją z dniami tego samego miesiąca w latach 2023–2025: jaki odsetek tych dni miał niższą średnią.
Wynik zmienia się przy każdym uruchomieniu (tabela `wyniki_biezace`, karta pod mapą w panelu 3). Przykład z 25.10.2026,
01:00: PM10 w Warszawie 22,1 µg/m³ – więcej niż w 69% dni października 2023–2025.
Ostatnie 24 h to nie doba kalendarzowa, więc porównanie jest orientacyjne.

### 4. Klasy jakości dnia

Każdy dzień ma klasę według dobowego PM10 (progi w sekcji „Klasy jakości dnia”). Tu po prostu liczymy,
ile było dni każdej klasy – bez żadnego modelu (`class_counts` w [`src/analyze.py`](src/analyze.py),
tabela `wyniki_klasy`, wykres `data/processed/klasy_dni.png`, panel 4 na stronie).

| | dobra | umiarkowana | zła |
|---|---|---|---|
| Warszawa | 741 (71,5%) | 256 (24,7%) | 39 (3,8%) |
| Katowice | 643 (62,1%) | **335 (32,3%)** | **58 (5,6%)** |
| Wrocław | 727 (70,2%) | 264 (25,5%) | 45 (4,3%) |
| Gdańsk | **820 (79,2%)** | 207 (20,0%) | 9 (0,9%) |
| sezon grzewczy | 1283 (59,7%) | 725 (33,8%) | **140 (6,5%)** |
| sezon letni | 1648 (82,6%) | 337 (16,9%) | 11 (0,6%) |
| **wszystkie dni** | **2931 (70,7%)** | **1062 (25,6%)** | **151 (3,6%)** |

- **7 na 10 dni są dobre**, a dni złe to 3,6%.
- **Najgorszy jest luty:** 13,8% dni złych i tylko 52,6% dobrych. Potem marzec (12,1%), grudzień (5,8%)
  i styczeń (5,2%).
- **Od maja do sierpnia nie było ani jednego dnia złego.** Najlepszy jest lipiec – 94,9% dni dobrych.
- Najwięcej dni złych i umiarkowanych mają **Katowice**, najwięcej dobrych – **Gdańsk** (9 złych dni w 3 lata).

### Rozszerzenie: typy dni pogodowych (klasteryzacja)

Algorytm k-średnich ([`src/cluster.py`](src/cluster.py)) dzieli 4144 dni (1036 dni × 4 stacje pogodowe) na typy
**wyłącznie według pogody**: temperatury, wiatru, opadu (logarytm), wilgotności i ciśnienia (różnica od średniej
stacji). Pył nie bierze udziału w podziale – dopiero potem sprawdzamy, w którym typie jest go najwięcej.

**Ile typów.** Najlepszy podział (najwyższy wskaźnik silhouette = 0,271) daje **4 typy**:

| Typ dnia | Dni | Temp. [°C] | Wiatr [m/s] | Opad [mm] | Wilg. [%] | Ciśn. [hPa]* | Sezon grzewczy | PM10 śr. | PM2.5 śr. | Dni złe | Udział w dniach złych |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1. zimne, wyżowe | 1192 | 3,6 | 2,6 | 0,2 | 84 | +6,9 | 89% | **29,1** | **23,4** | 8,8% | **70%** (105 ze 151) |
| 2. suche, ciepłe | 1510 | 17,0 | 2,6 | 0,2 | 65 | +1,1 | 10% | 21,5 | 12,7 | 2,4% | 24% |
| 3. deszczowe, niżowe | 689 | 11,7 | 3,0 | 8,2 | 84 | −6,6 | 46% | 18,4 | 13,9 | 1,0% | 5% |
| 4. wietrzne, niżowe | 753 | 6,4 | 5,0 | 0,9 | 78 | −7,1 | 82% | 16,3 | 12,8 | 0,4% | 2% |

\* różnica od średniego ciśnienia na danej stacji.

**Wnioski:**

- **Smog powstaje przy zimnej, bezwietrznej pogodzie wyżowej.** Typ 1 to 29% dni, ale 70% dni złych.
  PM2.5 jest w nim prawie dwa razy wyższe niż w pozostałych typach (23,4 wobec ok. 13 µg/m³).
- **Sam sezon grzewczy nie wystarcza.** Typ 4 (wietrzny) to w 82% dni zimowe, a ma najmniej pyłu
  (PM10 16,3 – o 44% mniej niż typ 1). W piecach pali się tyle samo, ale wiatr rozwiewa dym.
- Typ 1 dominuje w grudniu (64% dni) i lutym (61%), typ 2 od maja do września (69–76%).
- Typy nie pokrywają się z klasami jakości (ARI = 0,023), bo w każdym typie większość dni jest dobra.
  Typy pokazują raczej **ryzyko** złego dnia – od 0,4% do 8,8%.

**Ograniczenia:** typy nie są wyraźnie rozdzielone (silhouette 0,27), bo pogoda zmienia się płynnie.
Typ „wietrzny” częściej trafia się w Gdańsku nad morzem (32% dni wobec 9–18% gdzie indziej).

## Ograniczenia

- **Stacje pogodowe** stoją na lotniskach i obrzeżach (np. Gdańsk-Świbno ok. 20 km od centrum), a stacje pyłu
  w mieście – pogoda jest więc z tego samego miasta, ale nie z tego samego punktu.
- **Jedna stacja pyłu na miasto** – inne dzielnice mogą mieć inne stężenia.
- **Filtracja** usuwa 5% dni. Nie są to dni losowe, tylko dziury konkretnych stacji (najwięcej: Katowice,
  maj 2024 – z tego miesiąca zostało tylko 11 pełnych dni).
- **1 kwietnia 2024** wszystkie miasta naraz pokazały wysokie PM10 (Warszawa 111, Katowice 102 µg/m³) przy
  ok. 18 °C, z małym udziałem PM2.5. To najpewniej pył napływowy z daleka (np. z Sahary – nie sprawdzaliśmy).
  Pomiar jest prawdziwy, więc zostaje.
- Korelacja to nie przyczyna. Kolejne dni są do siebie podobne, więc testy powtórzyliśmy na średnich miesięcznych.

## Struktura projektu

Moduły w `src/` odpowiadają krokom w kolejności, w jakiej uruchamia je `src/main.py`. Każdy ma funkcję `run()`
i opis kroków na początku pliku.

```
air-quality-podkarpacie/   # nazwa katalogu z pierwszej wersji projektu (Podkarpacie)
├── data/raw/            # pobrane pliki (w .gitignore)
├── data/processed/      # wykresy PNG i raporty
├── db/air.sqlite        # baza danych (w .gitignore)
├── src/
│   ├── config.py        # ustawienia: lata, miasta i stacje, progi, nazwy zmiennych
│   ├── fetch_local.py   # TK02 – pobranie i wczytanie plików GIOŚ i IMGW
│   ├── fetch_api.py     # TK03 – API GIOŚ
│   ├── quality.py       # TK04 – kontrola jakości pomiarów godzinowych
│   ├── clean.py         # TK04 – średnie dobowe, pogoda, zapis do bazy, filtracja
│   ├── eda.py           # TK04 – eksploracja: statystyki opisowe, rozkłady, reguła 3σ
│   ├── analyze.py       # TK04 – pytania 1–4: sezony, korelacje, ranking miast, klasy dni
│   ├── significance.py  # TK04 – testy istotności (p-wartości)
│   ├── cluster.py       # TK04 – typy dni pogodowych
│   ├── plots.py         # wspólny wygląd wykresów PNG
│   ├── export.py        # TK05 – dane dla strony (w kolejności paneli)
│   ├── app.py           # TK05 – okno aplikacji
│   ├── build_exe.py     # TK05 – budowa pliku .exe z oknem aplikacji
│   └── main.py          # uruchamia wszystko po kolei
├── web/                 # strona: index.html, app.js (sekcje w kolejności paneli), style.css, data/*.json
├── requirements.txt
└── README.md
```

Nazwy w kodzie Pythona są po angielsku, komentarze po polsku. Nazwy w danych (tabele i kolumny w bazie,
klucze w plikach JSON) oraz cała strona są po polsku.

Biblioteki: `requests`, `pandas`, `openpyxl`, `scikit-learn`, `matplotlib`, a na stronie Chart.js 4.5.1 i Leaflet 1.9.4.
Mapa: © OpenStreetMap.
