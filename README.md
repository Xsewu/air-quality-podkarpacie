# Jakość powietrza na Podkarpaciu: analiza PM10 i PM2.5 w zależności od pogody i pory roku

Projekt zaliczeniowy z przedmiotu **Techniki analizy danych** (Informatyka, sem. 5, specjalność AI, Politechnika Rzeszowska).

Badamy pył zawieszony PM10 i PM2.5 w czterech miastach Podkarpacia: Rzeszowie, Krośnie, Przemyślu i Jaśle,
w latach **2023–2025**. Porównujemy go z pogodą (IMGW) i danymi o powiatach (GUS). Tarnobrzeg odpadł,
bo jego stacja nie mierzy PM2.5.

## Treści programowe

| Kod | Wymaganie | Gdzie w projekcie |
|-----|-----------|-------------------|
| TK01 | Źródła i formaty danych | sekcja [Źródła i formaty danych](#tk01--źródła-i-formaty-danych) |
| TK02 | Dane z plików | [`src/fetch_local.py`](src/fetch_local.py) – archiwa GIOŚ (XLSX), dane IMGW (CSV) |
| TK03 | Dane z internetu | [`src/fetch_api.py`](src/fetch_api.py) – API GIOŚ i API GUS (JSON) |
| TK04 | Analiza, czyszczenie, zapis | [`src/clean.py`](src/clean.py), [`src/analyze.py`](src/analyze.py), [`src/model.py`](src/model.py), [`src/cluster.py`](src/cluster.py) |
| TK05 | Prezentacja wyników | [`src/export.py`](src/export.py), strona [`web/`](web/index.html), okno aplikacji [`src/app.py`](src/app.py) |

## Uruchomienie

Potrzebny jest Python 3.11+ i internet przy pierwszym uruchomieniu (archiwa GIOŚ mają ok. 170 MB).

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows; Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

python -m src.main                # wszystko: pobranie → czyszczenie → analiza → model → klastry → eksport
python -m src.app                 # strona w osobnym oknie
python -m http.server -d web      # albo strona w przeglądarce: http://localhost:8000
```

- Pierwsze uruchomienie pobiera dane do `data/raw/` (kilka minut). Kolejne korzystają z plików na dysku.
  Pobrane pliki nigdy nie są nadpisywane.
- Każde uruchomienie pobiera też najnowsze pomiary z API GIOŚ – widać je po kliknięciu stacji na mapie.
- Strony nie da się otworzyć prosto z dysku (`file://`), bo przeglądarka zablokuje pliki z danymi.
- `python -m src.app` otwiera stronę w oknie Edge (lub Chrome) bez paska adresu. Zamknięcie okna kończy program.
- Etapy można uruchamiać osobno, np. `python -m src.analyze` (analiza, model, klastry i eksport potrzebują bazy
  `db/air.sqlite`).

**Klucz API (nieobowiązkowy).** API GUS działa bez rejestracji. Kto ma klucz, może go podać w zmiennej
środowiskowej – nie jest zapisany w kodzie:

```bash
set BDL_API_KEY=twoj_klucz        # Windows (cmd); PowerShell: $env:BDL_API_KEY="twoj_klucz"; bash: export BDL_API_KEY=twoj_klucz
```

**Panele strony:**

- **A – Akwizycja danych:** skąd są dane, pobrane pliki, fragmenty surowych danych i lista zapytań do API.
- **P – Przygotowanie danych:** jak z surowych pomiarów powstają czyste dane – kroki, kontrola jakości, filtracja.
- **E – Eksploracja (EDA):** typowe wartości, rozrzut i rozkład każdej zmiennej, reguła 3σ, testy istotności.
- **1–5:** odpowiedzi na pytania badawcze i typy dni pogodowych.

## TK01 – Źródła i formaty danych

| Źródło | Co pobieramy | Format | Dostęp |
|--------|--------------|--------|--------|
| **GIOŚ – archiwum** ([powietrze.gios.gov.pl/pjp/archives](https://powietrze.gios.gov.pl/pjp/archives)) | pomiary godzinowe PM10 i PM2.5 z lat 2023–2025 | ZIP z plikami **XLSX** | plik (TK02) |
| **GIOŚ – metadane stacji** | kody, nazwy i położenie stacji | **XLSX** | plik (TK02) |
| **IMGW** ([danepubliczne.imgw.pl](https://danepubliczne.imgw.pl)) | pogoda co godzinę ze stacji 580, 670, 690 | ZIP z plikami **CSV**, kodowanie **cp1250** | plik (TK02) |
| **GIOŚ – API** ([api.gios.gov.pl](https://api.gios.gov.pl/pjp-api/swagger-ui/index.html)) | lista stacji i najnowsze pomiary | **JSON-LD** | internet (TK03) |
| **GUS – Bank Danych Lokalnych** ([bdl.stat.gov.pl](https://bdl.stat.gov.pl/api/v1)) | ludność, samochody, gęstość zaludnienia dla 4 powiatów | **JSON** | internet (TK03) |

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
- **API GUS:** zmienne 60641 (ludność), 32561 (samochody osobowe), 60533 (samochody na 1000 osób),
  60559 (osoby na km²).

## Przygotowanie danych (TK04)

**Jedna stacja na miasto.** Wybieramy stację z pełnymi danymi z 3 lat, która nie stoi przy ruchliwej ulicy:

| Miasto | Stacja GIOŚ | Stacja pogodowa IMGW | Powiat w GUS |
|--------|-------------|----------------------|--------------|
| Rzeszów | PkRzeszStarz | 580 Rzeszów-Jasionka | Rzeszów |
| Krosno | PkKrosKletow | 670 Krosno | Krosno |
| Przemyśl | PkPrzemGrunw | **690 Lesko** (ok. 45 km) – w Przemyślu brak stacji | Przemyśl |
| Jasło | PkJasloSikor | **670 Krosno** (ok. 25 km) – w Jaśle brak stacji | **powiat jasielski** |

**Kroki** ([`src/clean.py`](src/clean.py)):

1. **Czas.** Wszystko przeliczamy na czas zimowy (CET), żeby doba pyłu i pogody była ta sama.
2. **Wartości niemożliwe.** Stężenie poniżej 0 albo PM2.5 większe niż PM10 (PM2.5 to część PM10) zamieniamy
   na brak. **Takich wartości nie było** (sprawdzone 99 623 godziny). Krok zostaje jako zabezpieczenie.
3. **Błędy czujników.** Wysoka wartość może być smogiem albo awarią. Dla wartości powyżej **30 µg/m³**
   sprawdzamy dwie rzeczy:

   | Warunek | Co oznacza |
   |---|---|
   | a) ponad 3 razy więcej niż na innych stacjach w tej samej godzinie | stacja widzi coś, czego nie widzą inne |
   | b) ponad 3 razy więcej niż godzinę przed i po | nagły skok, który zaraz znika |

   **Oba warunki naraz = błąd – usuwamy.** Jeden warunek = tylko oznaczenie, wartość zostaje. Każde miasto ma
   jedną stację, więc porównujemy z pozostałymi miastami.

   Przykłady:
   - **Jasło, PM10, 18.12.2023, godz. 13:** 122,4 µg/m³, inne stacje 11,5, godzinę przed 20,4, po 51,0
     → oba warunki → **usunięta**.
   - **Rzeszów, PM2.5, 9.01.2024, godz. 21:** 376,3 µg/m³, inne stacje 117,1, godzinę przed 331,2, po 353,8
     → tylko a), bo smog narastał kilka godzin → **zostaje**. To szczyt największego epizodu smogowego.
     Gdybyśmy usuwali „wszystko powyżej 150”, stracilibyśmy właśnie ten epizod.

   | Stacja | Wskaźnik | Usunięte | Tylko a) – zostaje | Tylko b) – zostaje |
   |---|---|---|---|---|
   | Jasło | PM10 | 6 | 164 | 6 |
   | Jasło | PM2.5 | 2 | 155 | 0 |
   | Krosno | PM10 | 0 | 189 | 0 |
   | Krosno | PM2.5 | 0 | 63 | 0 |
   | Przemyśl | PM10 | 4 | 490 | 0 |
   | Przemyśl | PM2.5 | 3 | 703 | 0 |
   | Rzeszów | PM10 | 0 | 77 | 0 |
   | Rzeszów | PM2.5 | 0 | 183 | 0 |

   Usunięto **15 z 202 904** pomiarów (0,007%), a 2030 tylko oznaczono. Najczęściej od innych odstaje
   **Przemyśl** – głównie zimą (93%) i wieczorem (53% w godzinach 17–24), czyli przez ogrzewanie domów.
   Usunięcie 15 godzin praktycznie nie zmieniło wyników (średnie o najwyżej 0,02 µg/m³).
4. **Braki w pogodzie.** Kod 8 zamieniamy na brak, opad z kodem 9 na 0 mm. Braków nie zgadujemy.
5. **Średnie dobowe.** Średnią z doby liczymy tylko, gdy jest co najmniej **18 z 24 godzin** (tak każe dyrektywa
   UE 2008/50/WE). Opad liczymy tylko przy wszystkich 4 sumach 6-godzinnych.
6. **Zapis do bazy** SQLite `db/air.sqlite`:

| Tabela | Co zawiera | Wiersze |
|---|---|---|
| `stacje`, `stacje_meteo` | stacje pyłu i pogody | 4, 3 |
| `kontrola_pomiarow`, `kontrola_flagi` | wynik kontroli jakości i każda oznaczona godzina | 8, 2 045 |
| `przyklad_doby` | doba z przykładu powyżej, przed i po kontroli | 24 |
| `pm_dobowe` | średnie dobowe PM10 i PM2.5 | 4 384 |
| `pogoda_dobowa` | temperatura, opad, wiatr, wilgotność, ciśnienie | 3 287 |
| `gus_powiaty` | dane GUS – 4 powiaty × 3 lata | 12 |
| `pm_biezace` | najnowsze pomiary z API GIOŚ | kilkaset |
| `dzien_miasto_pelny` (widok) | wszystkie dni: pył + pogoda + sezon + klasa dnia | 4 384 |
| `dni_kompletne`, `filtracja_braki` | pełne dni i powody odrzucenia pozostałych | 867, 16 |
| `dzien_miasto` (widok) | **dane do analizy** – tylko pełne dni | 3 468 |
| `wyniki_*`, `model_*`, `klastry_*` | wyniki analiz | – |

7. **Filtracja.** Dzień zostaje tylko wtedy, gdy **wszystkie 4 miasta** mają PM10, PM2.5 i pełną pogodę.
   Dzięki temu miasta i sezony porównujemy na tych samych dniach. Wszystkie analizy korzystają z tych danych.

**Ile danych brakowało** (z 1096 dni):

| Miasto | Dni z PM10 | Brak PM10 | Dni z PM2.5 | Brak PM2.5 |
|---|---|---|---|---|
| Jasło | 1060 | 3,3% | 1047 | 4,5% |
| Krosno | 973 | 11,2% | 1048 | 4,4% |
| Przemyśl | 1074 | 2,0% | 1074 | 2,0% |
| Rzeszów | 1082 | 1,3% | 1082 | 1,3% |

**Wynik filtracji:** zostaje **867 z 1096 dni (79%)**, czyli 3468 dni-miasto (4 × 867). Powody odrzucenia:

| Dni z brakiem | PM10 | PM2.5 | opad | reszta pogody |
|---|---|---|---|---|
| Rzeszów | 14 | 14 | 1 | – |
| Krosno | **123** | 48 | 1 | – |
| Przemyśl | 22 | 22 | 4 | po 2 |
| Jasło | 36 | 49 | 1 | – |

Jednego dnia może brakować kilku rzeczy, więc liczby się nie sumują. Najwięcej dni zabrała dziura w PM10 w Krośnie
(sierpień–październik 2024, 70 dni).

## Klasy jakości dnia

Klasę dnia wyznacza **średnie dobowe PM10** (progi w [`src/config.py`](src/config.py)):

| Klasa | PM10 | Dlaczego |
|---|---|---|
| dobra | do 25 µg/m³ | do połowy normy |
| umiarkowana | 25–50 µg/m³ | do normy UE |
| zła | ponad 50 µg/m³ | norma przekroczona |

W danych do analizy: 2783 dni dobrych, 585 umiarkowanych, 100 złych (dni-miasto).

## Eksploracja danych (EDA)

Zanim wybierzemy metody, sprawdzamy, jak wyglądają dane (wykres: `data/processed/rozklady.png`, panel E na stronie).
Pył liczymy na 3468 dniach-miasto, pogodę na 2601 dniach (3 stacje – Krosno i Jasło mają wspólną).

| Zmienna | n | Średnia | Mediana | Dominanta | Odch. std. | Wariancja | Wsp. zmienności | Skośność | Kurtoza | Kształt |
|---|---|---|---|---|---|---|---|---|---|---|
| PM10 [µg/m³] | 3468 | 19,8 | 16,8 | 12–14 | 12,8 | 164,3 | 65% | 4,35 | 36,89 | mocno skośny w prawo (po logarytmie: 0,49) |
| PM2.5 [µg/m³] | 3468 | 13,2 | 10,4 | 6–8 | 10,6 | 111,7 | 80% | 4,43 | 40,26 | mocno skośny w prawo (po logarytmie: 0,51) |
| temperatura [°C] | 2601 | 9,3 | 8,7 | 0–1 | 7,8 | 60,4 | – | 0,08 | −0,96 | symetryczny, dwa szczyty: zima i lato |
| wiatr [m/s] | 2601 | 3,1 | 2,8 | 2,0–2,5 | 1,4 | 1,9 | 45% | 1,26 | 2,54 | mocno skośny w prawo |
| opad [mm] | 2601 | 1,9 | 0,0 | 0–1 | 4,4 | 19,5 | 237% | 4,93 | 39,25 | mocno skośny; 52% dni bez opadu |
| wilgotność [%] | 2601 | 76,0 | 77,1 | 76–78 | 12,8 | 163,2 | 17% | −0,49 | −0,22 | bliski normalnemu |
| ciśnienie [hPa] | 2601 | 978,1 | 977,7 | 970–972 | 12,6 | 158,8 | 1% | 0,05 | −0,44 | bliski normalnemu |

Jak czytać tabelę:

- **Dominanta** to najczęstszy przedział wartości (przy pomiarach ciągłych pojedyncze liczby prawie się nie powtarzają).
- **Wariancja** jest w jednostkach do kwadratu, dlatego łatwiej czytać odchylenie standardowe.
- **Współczynnika zmienności** nie liczymy dla temperatury – skala °C nie ma prawdziwego zera.
- **Rozkład normalny** ma skośność 0 i kurtozę 0. Skośność powyżej 1 oznacza wyraźnie niesymetryczny rozkład.
- **Testu normalności nie liczymy:** wymagałby biblioteki `scipy`, a przy tysiącach danych i tak wykazuje
  „nienormalność” przy drobnych odchyleniach. Oceniamy kształt ze skośności, kurtozy i histogramu.

**Co z tego wynika:**

- **Pył jest mocno skośny:** większość dni jest czysta, a kilka bardzo brudnych. Dlatego średnia (19,8) jest
  wyższa od mediany (16,8), a korelacje liczymy metodą Spearmana, która nie wymaga rozkładu normalnego.
- **Regresja liniowa** wymaga rozkładu normalnego – dla pyłu trzeba by go zlogarytmować. Nasz model (drzewa)
  nie ma takich wymagań.
- **Opad:** połowa dni bez deszczu, więc w klasteryzacji używamy jego logarytmu.
- **Różne skale zmiennych** (wariancja PM10 164,3, wiatru 1,9) – przed klasteryzacją sprowadzamy je do jednej skali.
- **Ciśnienie:** odchylenie 12,6 hPa, ale na jednej stacji tylko 8,0 – resztę daje różna wysokość stacji.
  Dlatego w klasteryzacji liczy się tylko różnica od średniej danej stacji.
- **Temperatura** ma dwa szczyty (zima i lato), więc wyniki liczymy też osobno dla sezonów.

### Reguła 68–95–99,7 (3σ)

W rozkładzie normalnym 68% danych leży w odległości 1 odchylenia od średniej, 95% – 2 odchyleń, 99,7% – 3 odchyleń.

| Zmienna | Przekształcenie | w ±1σ | w ±2σ | w ±3σ | poza ±3σ |
|---|---|---|---|---|---|
| *rozkład normalny* | – | *68,3%* | *95,4%* | *99,7%* | *0,3%* |
| PM10 | bez zmian | **87,4%** | 96,2% | **98,5%** | **51 (1,5%)** |
| PM10 | logarytm | 72,1% | 95,0% | 99,3% | 26 (0,7%) |
| PM2.5 | bez zmian | **89,0%** | 96,0% | **98,3%** | **60 (1,7%)** |
| PM2.5 | logarytm | 70,7% | 94,8% | 99,4% | 20 (0,6%) |
| temperatura | bez zmian | **59,6%** | **98,9%** | 99,9% | 2 (0,1%) |
| wiatr | bez zmian | 72,4% | 95,9% | 98,8% | **30 (1,2%)** |
| wiatr | logarytm | 66,2% | 96,1% | 99,7% | 7 (0,3%) |
| opad | bez zmian | **90,0%** | 95,6% | **97,8%** | **57 (2,2%)** |
| opad | logarytm | **81,8%** | 93,6% | 98,9% | **28 (1,1%)** |
| wilgotność | bez zmian | 67,4% | 96,5% | 99,8% | 4 (0,2%) |
| ciśnienie | bez zmian | 65,4% | 96,4% | 100,0% | 1 (0,0%) |

- **Pył nie pasuje do reguły.** Powyżej średnia + 3σ (58,2 µg/m³) jest 51 dni PM10 i **wszystkie to dni ze smogiem**
  (51 ze 100). Usunięcie ich jako „odstających” wycięłoby połowę smogu. Dlatego błędy czujników szukamy innym
  sposobem (krok 3 przygotowania).
- **Po logarytmie** pył pasuje do reguły prawie idealnie. Wilgotność i ciśnienie pasują bez zmian.

## Testy istotności (p-wartości)

Testy sprawdzają, czy wyniki pytań 1–3 mogą być przypadkiem. Wynik jest istotny, gdy p < 0,05.
Liczymy je bez `scipy` – wzorami i przez losowanie (`numpy`).

| Pyt. | Test | Co sprawdza | Dane | Wynik | p | Wielkość efektu |
|---|---|---|---|---|---|---|
| 1 | U Manna–Whitneya | czy zimą jest tyle samo PM10 co latem | 1876 + 1592 dni | z = 11,80 | < 0,001 | P(dzień zimowy > letni) = 0,62 |
| 1 | U Manna–Whitneya | to samo dla PM2.5 | 1876 + 1592 dni | z = 24,43 | < 0,001 | P(dzień zimowy > letni) = 0,74 |
| 1 | permutacyjny | to samo na średnich miesięcznych (PM10) | 35 miesięcy | różnica 5,92 µg/m³ | < 0,001 | – |
| 1 | permutacyjny | to samo na średnich miesięcznych (PM2.5) | 35 miesięcy | różnica 7,77 µg/m³ | < 0,001 | – |
| 2 | Spearmana | czy pary zmiennych są powiązane | 3468 / 1876 / 1592 dni | istotne już od \|r\| 0,033 / 0,045 / 0,049 | – | istotne pary: 21 / 19 / 18 z 21 |
| 3 | Friedmana | czy miasta mają tyle samo PM10 (te same dni) | 867 dni × 4 miasta | Q = 428,9 | < 0,001 | W Kendalla = 0,16 |
| 3 | Friedmana | to samo na średnich miesięcznych | 35 miesięcy × 4 miasta | Q = 32,0 | < 0,001 | W Kendalla = 0,31 |
| 3 | Spearmana, dokładny | czy PM10 zależy od ludności | 4 miasta | r = −1,00 | **0,083** | – |
| 3 | Spearmana, dokładny | … od liczby samochodów na 1000 osób | 4 miasta | r = 0,00 | 1,000 | – |
| 3 | Spearmana, dokładny | … od gęstości zaludnienia | 4 miasta | r = −0,40 | 0,750 | – |

Dla PM2.5 miasta też różnią się istotnie (p < 0,001), w kolejności Przemyśl > Jasło > Rzeszów > Krosno.

- **Pytanie 1:** zimą jest istotnie więcej pyłu. Losowy dzień zimowy ma wyższe PM10 od letniego
  z prawdopodobieństwem 0,62 (PM2.5: 0,74).
- **Pytanie 2:** przy tylu danych istotne są nawet bardzo słabe związki, więc ważniejsza jest siła związku.
  Nieistotne są tylko pary z |r| poniżej 0,03 (np. latem PM2.5 i temperatura).
- **Pytanie 3:** miasta różnią się istotnie, w tej samej kolejności co w rankingu. Ale związek z ludnością,
  mimo r = −1,0, jest **nieistotny** (p = 0,083) – przy 4 miastach taka kolejność wychodzi przypadkiem raz na 12.
- **Uwaga:** smog trwa kilka dni, więc kolejne dni są do siebie podobne (autokorelacja 0,62–0,77). Przez to
  p z danych dziennych wychodzi za małe. Dlatego testy powtórzyliśmy na średnich miesięcznych – wnioski się
  nie zmieniły. (Miesięcy jest 35, bo we wrześniu 2024 nie było ani jednego pełnego dnia.)

## Wyniki – odpowiedzi na pytania badawcze

Wszystkie liczby dotyczą **867 pełnych dni** z lat 2023–2025 (3468 dni-miasto). Wykresy są w `data/processed/`
i na stronie.

### 1. Sezon grzewczy (paź–mar) a letni (kwi–wrz)

Zimą pyłu jest wyraźnie więcej, a najbardziej rośnie **PM2.5** – drobny pył z pieców w domach:

| | Sezon grzewczy | Sezon letni | Ile razy więcej |
|---|---|---|---|
| PM10 | 22,5 µg/m³ | 16,6 µg/m³ | **1,35×** |
| PM2.5 | 16,8 µg/m³ | 9,1 µg/m³ | **1,85×** |
| Dni z PM10 > 50 µg/m³ | 96 | 4 | – |
| Pełne dni / wszystkie dni | 469 / 547 | 398 / 549 | – |

- W miastach PM10 rośnie zimą od 1,24 raza (Krosno) do 1,42 raza (Przemyśl), a PM2.5 od 1,73 do 1,95 raza (Rzeszów).
- Najgorszy miesiąc to luty (PM10 29,6 µg/m³ w Przemyślu, 29,0 w Krośnie; w Rzeszowie marzec – 22,8).
  Najlepsze są czerwiec–wrzesień (od 12,5 w Rzeszowie do 18,1 w Krośnie).
- 96% dni z przekroczoną normą przypada na sezon grzewczy. Różnica jest istotna (p < 0,001).

### 2. Wpływ temperatury, wiatru i opadów

Korelacja PM10 z pogodą (metoda Spearmana, dane z 4 miast razem):

| Zmienna | Cały rok | Sezon grzewczy | Sezon letni |
|---|---|---|---|
| wiatr | −0,28 | **−0,50** | −0,13 |
| opad | −0,29 | **−0,39** | −0,20 |
| temperatura | −0,15 | −0,15 | **+0,22** |
| ciśnienie | +0,10 | +0,20 | −0,08 |
| wilgotność | −0,04 | −0,05 | −0,20 |

- **Wiatr** najmocniej obniża pył zimą – rozwiewa go.
- **Deszcz** wymywa pył z powietrza (zimą −0,39).
- **Temperatura** działa różnie: zimą zimno = więcej palenia w piecach = więcej pyłu, a latem ciepło = sucho =
  więcej kurzu. Dlatego w skali całego roku związek jest słaby.
- **Wysokie ciśnienie** (wyż, brak wiatru) zimą sprzyja smogowi.
- Wszystkie związki są umiarkowane lub słabe (|r| ≤ 0,5), czyli pogoda tłumaczy tylko część zmian.
  Wszystkie są istotne.

**Macierz korelacji** (wszystkie pary 7 zmiennych, panel 2 na stronie, `data/processed/korelacje.png`):

- **PM10 i PM2.5** idą w parze: 0,83 w całym roku, 0,92 zimą (to samo źródło – piece) i 0,71 latem.
- Najmocniej powiązane zmienne pogodowe to opad i wilgotność (+0,45, latem +0,60). Żadna para pogody nie
  przekracza 0,60, więc każda wnosi do modelu coś innego. Najmocniej powiązane cechy modelu to temperatura
  i sezon grzewczy (0,75 – zob. pytanie 4).

### 3. Ranking miast i dane GUS

| # | Miasto | PM10 śr. | PM2.5 śr. | Dni PM10 > 50 rocznie** | Ludność (2025) | Samochody / 1000 osób (2025) |
|---|---|---|---|---|---|---|
| 1 | Krosno | 21,9 | 11,9 | 7,3 | 43 274 | 628 |
| 2 | Przemyśl | 20,4 | **15,5** | 10,3 | 54 352 | 893 |
| 3 | Jasło* | 19,6 | 12,9 | 8,7 | 107 200 | 591 |
| 4 | Rzeszów | 17,2 | 12,6 | 7,0 | 199 436 | 657 |

\* dane GUS dla całego powiatu jasielskiego.
\*\* liczone tylko na pełnych dniach, więc mniej niż w rzeczywistości.

- Najwięcej PM10 ma **Krosno**, najwięcej PM2.5 – **Przemyśl**. Najczystszy jest **Rzeszów**, czyli największe miasto.
- Normę 35 dni z przekroczeniem w roku sprawdzamy na wszystkich dniach z pomiarem. Żadne miasto jej nie przekroczyło,
  ale w trzech liczba takich dni rośnie: Przemyśl 3 → 10 → 20, Krosno 1 → 9 → 12, Jasło 9 → 11 → 11
  (2023 → 2025); w Rzeszowie 8 → 12 → 5.
- Liczba samochodów nie tłumaczy różnic (r = 0,0). Większe miasta mają czystsze powietrze (r = −1,0 z ludnością),
  co pasuje do tego, że głównym źródłem są piece w domach. Ale przy 4 miastach to tylko wskazówka – ten wynik
  jest nieistotny (p = 0,083).
- Same różnice między miastami są istotne (p < 0,001).

### 4. Czy pogoda przewiduje jakość dnia?

Model dostaje pogodę z danego dnia (temperatura, opad, wiatr, wilgotność, ciśnienie), sezon i miasto – bez
pomiarów pyłu. Uczy się na latach 2023–2024 (2260 dni), a sprawdzamy go na roku 2025 (1208 dni).
Rzadkie dni złe mają większą wagę, żeby model ich nie pomijał.

**Jak przygotowaliśmy model:**

1. **Zebranie danych** – pliki i API (TK02, TK03).
2. **Przygotowanie** – kontrola jakości, średnie dobowe, filtracja ([`src/clean.py`](src/clean.py)).
3. **Eksploracja** – rozkłady i korelacje (sekcja EDA, pytanie 2).
4. **Wybór cech** – sprawdzamy, czy jakieś cechy nie są zbyt podobne (|r| > 0,8). Najbardziej podobne są
   temperatura i sezon grzewczy (|r| = 0,75 – sezon grzewczy to po prostu zimne miesiące). To poniżej progu,
   więc zostają wszystkie. VIF (inna miara tego samego) jest najwyżej 2,62, przy progu 5 – problemu nie ma.
5. **Uczenie** – drzewo decyzyjne i las losowy (random forest).
6. **Ocena** – dobór ustawień na latach 2023–2024, wynik na roku 2025, porównanie z modelem bazowym.

| Model | Accuracy | Balanced accuracy | F1 macro |
|---|---|---|---|
| model bazowy (zawsze „dobra”) | 0,775 | 0,333 | 0,291 |
| drzewo decyzyjne | 0,679 | 0,491 | 0,453 |
| **random forest** (300 drzew) | 0,666 | **0,600** | **0,468** |

Random forest dla każdej klasy (rok 2025):

| Klasa | Precyzja | Czułość | F1 | Dni |
|---|---|---|---|---|
| dobra | 0,93 | 0,74 | 0,83 | 936 |
| umiarkowana | 0,31 | 0,35 | 0,33 | 227 |
| zła | 0,15 | 0,71 | 0,25 | 45 |

- **Random forest jest wyraźnie lepszy od modelu bazowego** (balanced accuracy 0,60 zamiast 0,33). Model bazowy
  ma wyższe „zwykłe” accuracy tylko dlatego, że 77,5% dni jest dobrych, a on zawsze mówi „dobra”.
- Model znajduje **71% dni złych** (32 z 45), ale tylko co siódmy jego alarm jest trafny. Nigdy nie pomylił
  dnia złego z dobrym. Sama pogoda pozwala więc wskazać dni **ryzykowne**, ale nie przewidzieć dokładnie
  jakości powietrza – brakuje danych o emisjach i o pyle z poprzedniego dnia.
- Najważniejsze cechy: **sezon grzewczy** (0,154), **wiatr** (0,068), **opad** (0,061), temperatura (0,039).
  Temperatura wypada nisko, bo jej informację częściowo „zabiera” sezon grzewczy.

Pełny raport: `data/processed/model_raport.json`.

### Rozszerzenie: typy dni pogodowych (klasteryzacja)

Algorytm k-średnich ([`src/cluster.py`](src/cluster.py)) dzieli 2601 dni (867 dni × 3 stacje pogodowe) na typy
**wyłącznie według pogody**: temperatury, wiatru, opadu (logarytm), wilgotności i ciśnienia (różnica od średniej
stacji). Pył nie bierze udziału w podziale – dopiero potem sprawdzamy, w którym typie jest go najwięcej.

**Ile typów.** Najlepszy podział (najwyższy wskaźnik silhouette = 0,276) daje **4 typy**:

| Typ dnia | Dni | Temp. [°C] | Wiatr [m/s] | Opad [mm] | Wilg. [%] | Ciśn. [hPa]* | Sezon grzewczy | PM10 śr. | PM2.5 śr. | Dni złe | Udział w dniach złych |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1. zimne, wyżowe | 739 | 2,6 | 2,6 | 0,3 | 82 | +6,7 | 91% | **26,5** | **20,3** | 7,4% | **73%** (73 ze 100) |
| 2. ciepłe, suche | 931 | 16,1 | 2,6 | 0,2 | 66 | +1,0 | 13% | 18,8 | 10,3 | 1,7% | 21% |
| 3. deszczowe, wilgotne | 461 | 10,3 | 2,7 | 8,3 | 86 | −5,3 | 44% | 15,7 | 10,4 | 0,2% | 1% |
| 4. wietrzne, niżowe | 470 | 5,5 | 5,0 | 1,3 | 76 | −7,2 | 88% | 14,9 | 10,6 | 0,8% | 5% |

\* różnica od średniego ciśnienia na danej stacji.

**Wnioski:**

- **Smog powstaje przy zimnej, bezwietrznej pogodzie wyżowej.** Typ 1 to 28% dni, ale 73% dni złych.
  PM2.5 jest w nim dwa razy wyższe niż w pozostałych typach (20,3 wobec ok. 10,4 µg/m³).
- **Sam sezon grzewczy nie wystarcza.** Typ 4 (wietrzny) to w 88% dni zimowe, a ma najmniej pyłu
  (PM10 14,9 – o 43% mniej niż typ 1). W piecach pali się tyle samo, ale wiatr rozwiewa dym.
- Typ 1 dominuje w grudniu (65% dni) i lutym (57%), typ 2 od maja do września (69–78%).
- Typy nie pokrywają się z klasami jakości (ARI = 0,013), bo w każdym typie większość dni jest dobra.
  Typy pokazują raczej **ryzyko** złego dnia – od 0,2% do 7,4%.

**Ograniczenia:** typy nie są wyraźnie rozdzielone (silhouette 0,28), bo pogoda zmienia się płynnie.
Typ „wietrzny” częściej trafia się na lotnisku Rzeszów-Jasionka (26% dni wobec 13–15% gdzie indziej).

## Ograniczenia

- **Przemyśl** ma pogodę z Leska (ok. 45 km), a **Jasło** z Krosna.
- **Filtracja** usuwa 21% dni. Nie są to dni losowe, tylko dziury konkretnych stacji, więc średnie mogą się trochę
  różnić od średnich ze wszystkich pomiarów. W zamian wszystkie miasta liczymy na tych samych dniach.
- **Krosno** nie ma PM10 w sierpniu–październiku 2024, więc ten okres jest w analizie słabiej reprezentowany.
- **1 kwietnia 2024** wszystkie stacje naraz pokazały PM10 110–153 µg/m³ przy 20 °C, z małym udziałem PM2.5.
  To najpewniej pył napływowy z daleka (np. z Sahary – nie sprawdzaliśmy). Pomiar jest prawdziwy, więc zostaje.
- **GUS – samochody:** w 2024 r. liczba samochodów we wszystkich powiatach spada o 6–23% i wraca w 2025 r. – to
  raczej zmiana w danych GUS. W rankingu użyto roku 2025.
- Korelacja to nie przyczyna. Kolejne dni są do siebie podobne, więc testy powtórzyliśmy na średnich miesięcznych.

## Struktura projektu

```
air-quality-podkarpacie/
├── data/raw/            # pobrane pliki (w .gitignore)
├── data/processed/      # wykresy PNG i raporty
├── db/air.sqlite        # baza danych (w .gitignore)
├── src/
│   ├── config.py        # stacje, lata, ścieżki, progi
│   ├── fetch_local.py   # TK02 – dane z plików
│   ├── fetch_api.py     # TK03 – dane z API
│   ├── clean.py         # TK04 – czyszczenie i baza
│   ├── analyze.py       # TK04 – statystyki, testy, wykresy
│   ├── model.py         # TK04 – model
│   ├── cluster.py       # TK04 – typy dni
│   ├── export.py        # TK05 – dane dla strony
│   ├── app.py           # TK05 – okno aplikacji
│   └── main.py          # uruchamia wszystko
├── web/                 # strona: index.html, app.js, style.css, data/*.json
├── requirements.txt
└── README.md
```

Biblioteki: `requests`, `pandas`, `openpyxl`, `scikit-learn`, `matplotlib`, a na stronie Chart.js 4.5.1 i Leaflet 1.9.4.
Mapa: © OpenStreetMap.
