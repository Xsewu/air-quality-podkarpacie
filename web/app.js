/* TK05 – strona z wynikami analizy (Chart.js + Leaflet, dane z data/*.json). */
"use strict";

// --- Stan strony ----------------------------------------------------------------
const STAN = {
  wskaznik: "PM10",        // "PM10" | "PM2.5"
  zakres: "cały rok",      // zakres sezonu w macierzy korelacji
  os: "temp",              // oś X wykresu rozrzutu: "temp" | "wiatr"
  zmiennaEda: "PM10",      // zmienna na histogramie w panelu eksploracji
  model: "random forest",
};
const DANE = {};
const WYKRESY = {};

const POLE = { "PM10": "pm10", "PM2.5": "pm25" };
const MIESIACE = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"];
// Zmienne macierzy korelacji: [nazwa w danych, skrót w nagłówku kolumny]; pierwsze dwie to pył
const ZMIENNE = [["PM10", "PM10"], ["PM2.5", "PM2.5"], ["temperatura", "temp."], ["prędkość wiatru", "wiatr"],
  ["opad", "opad"], ["wilgotność", "wilg."], ["ciśnienie", "ciśn."]];
const KOLOR_KLASY = { "dobra": "--class-dobra", "umiarkowana": "--class-umiarkowana", "zła": "--class-zla" };
// Te same barwy w wersji dla ciemnego tła (paleta kategoryczna, kroki „dark”)
const KOLOR_CIEMNY = { "#2a78d6": "#3987e5", "#eb6834": "#d95926", "#1baf7a": "#199e70",
                       "#eda100": "#c98500", "#e87ba4": "#d55181" };

// --- Pomocnicze -------------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const css = (nazwa) => getComputedStyle(document.documentElement).getPropertyValue(nazwa).trim();
const ciemny = () => matchMedia("(prefers-color-scheme: dark)").matches;

function fmt(v, cyfry = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return Number(v).toLocaleString("pl-PL", { minimumFractionDigits: cyfry, maximumFractionDigits: cyfry });
}
const fmtInt = (v) => (v == null ? "–" : Math.round(v).toLocaleString("pl-PL"));
const fmtR = (r) => (r == null ? "–" : (r > 0 ? "+" : r < 0 ? "−" : "") + fmt(Math.abs(r), 2));
const kolorWMotywie = (hex) => (ciemny() ? (KOLOR_CIEMNY[hex] || hex) : hex);
const kolorMiasta = (miasto) => kolorWMotywie(DANE.meta.miasta.find((x) => x.miasto === miasto).kolor);
const listaMiast = () => DANE.meta.miasta.map((m) => m.miasto);
const znacznikKlasy = (e) => `<span class="znacznik" style="--kolor:var(${KOLOR_KLASY[e]})">${e}</span>`;

function zPrzezroczystoscia(hex, alfa) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alfa})`;
}

// Kolor skali rozbieżnej dla korelacji: niebieski (−1) ↔ szary (0) ↔ czerwony (+1)
function kolorKorelacji(r) {
  const stopnie = [[-1, "#1c5cab"], [-0.5, "#86b6ef"], [0, css("--div-mid")], [0.5, "#ef8f8e"], [1, "#b52f2f"]];
  const v = Math.max(-1, Math.min(1, r));
  const i = stopnie.findIndex(([prog], k) => k > 0 && v <= prog) - 1;
  const [a, ka] = stopnie[i];
  const [b, kb] = stopnie[i + 1];
  const t = (v - a) / (b - a);
  const pa = parseInt(ka.slice(1), 16), pb = parseInt(kb.slice(1), 16);
  const kanal = (s) => Math.round(((pa >> s) & 255) + (((pb >> s) & 255) - ((pa >> s) & 255)) * t);
  return `rgb(${kanal(16)}, ${kanal(8)}, ${kanal(0)})`;
}

// Tekst na kolorowej komórce: ciemny na jasnym tle, biały na ciemnym (w obu motywach)
function kolorTekstu(tlo) {
  const [r, g, b] = tlo.match(/\d+/g).map(Number);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 ? "#0c2a47" : "#ffffff";
}

// --- Wczytanie danych -------------------------------------------------------------
async function wczytajDane() {
  const pliki = ["meta", "akwizycja", "przygotowanie", "eksploracja", "sezonowe", "korelacje", "stacje", "ranking", "model", "klastry"];
  const wyniki = await Promise.all(pliki.map(async (p) => {
    const odp = await fetch(`data/${p}.json`);
    if (!odp.ok) throw new Error(`data/${p}.json: HTTP ${odp.status}`);
    return odp.json();
  }));
  pliki.forEach((p, i) => { DANE[p] = wyniki[i]; });
}

function pokazBlad(err) {
  const el = $("#komunikat");
  el.hidden = false;
  el.innerHTML = `<strong>Nie udało się wczytać danych.</strong> ${err.message}<br>
    Uruchom pipeline (<code>python -m src.main</code>) i serwer (<code>python -m http.server -d web</code>),
    a następnie otwórz <code>http://localhost:8000</code>. Otwarcie pliku bezpośrednio z dysku blokuje pobieranie JSON.`;
}

// --- Wspólne ustawienia Chart.js ------------------------------------------------------
function ustawChartJs() {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = css("--text-secondary");
  Chart.defaults.borderColor = css("--grid");
  Chart.defaults.plugins.legend.position = "bottom";
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.boxHeight = 12;
  Chart.defaults.plugins.tooltip.backgroundColor = ciemny() ? "#15304b" : "#0c2a47";
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 0;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.animation.duration = 300;
}

function nowyWykres(id, konfiguracja) {
  WYKRESY[id]?.destroy();
  WYKRESY[id] = new Chart(document.getElementById(id), konfiguracja);
}

// Wtyczka: pasy sezonu grzewczego (sty–mar, paź–gru) na wykresie miesięcznym
const pasGrzewczy = {
  id: "pasGrzewczy",
  beforeDatasetsDraw(chart) {
    const { ctx, chartArea: a, scales: { x } } = chart;
    const krok = x.getPixelForValue(1) - x.getPixelForValue(0);
    ctx.save();
    ctx.fillStyle = css("--heat-band");
    for (const [od, doo] of [[0, 2], [9, 11]]) {
      const x0 = Math.max(a.left, x.getPixelForValue(od) - krok / 2);
      const x1 = Math.min(a.right, x.getPixelForValue(doo) + krok / 2);
      ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
    }
    ctx.restore();
  },
};

// Wtyczka: linia normy dobowej PM10 (50 µg/m³)
const liniaNormy = {
  id: "liniaNormy",
  afterDatasetsDraw(chart) {
    const { ctx, chartArea: a, scales: { y } } = chart;
    const py = y.getPixelForValue(DANE.meta.progi_pm10.granice[1]);
    if (py < a.top || py > a.bottom) return;
    ctx.save();
    ctx.strokeStyle = ctx.fillStyle = css("--text-secondary");
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke();
    ctx.font = `12px ${Chart.defaults.font.family}`;
    ctx.textAlign = "right";
    ctx.fillText("norma dobowa 50", a.right - 4, py - 6);
    ctx.restore();
  },
};

// --- Kafle z najważniejszymi liczbami ---------------------------------------------
// Liczba dni kalendarzowych sezonu w badanym okresie (np. 547 dni sezonu grzewczego w latach 2023–2025)
function dniSezonu(sezon) {
  const { od, do: koniec } = DANE.meta.zakres;
  const grzewcze = DANE.meta.miesiace_grzewcze;
  let n = 0;
  for (let d = new Date(`${od}T00:00:00Z`); d <= new Date(`${koniec}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + 1)) {
    if (grzewcze.includes(d.getUTCMonth() + 1) === (sezon === "grzewczy")) n++;
  }
  return n;
}
// Opis liczby pomiarów: dni-miasto (każde miasto osobno) po filtracji – tylko dni kompletne we wszystkich miastach
const opisPomiarow = (wiersz, sezon) =>
  `${fmtInt(wiersz.n_dni / listaMiast().length)} dni × ${listaMiast().length} miasta (z ${dniSezonu(sezon)} dni sezonu)`;

const wierszSezonu = (miasto, wskaznik, sezon) =>
  DANE.sezonowe.sezony.find((r) => r.miasto === miasto && r.wskaznik === wskaznik && r.sezon === sezon);

function renderKafle() {
  const g = wierszSezonu("Wszystkie miasta", STAN.wskaznik, "grzewczy");
  const l = wierszSezonu("Wszystkie miasta", STAN.wskaznik, "letni");
  const pm10g = wierszSezonu("Wszystkie miasta", "PM10", "grzewczy");
  const pm10l = wierszSezonu("Wszystkie miasta", "PM10", "letni");
  $("#kafle").innerHTML = `
    <div class="kafel wyrozniony">
      <div class="k-etykieta">Sezon grzewczy vs letni · ${STAN.wskaznik}</div>
      <div class="k-wartosc">×${fmt(g.iloraz_grzewczy_letni, 2)}</div>
      <div class="k-opis">tyle razy więcej pyłu zimą niż latem</div>
    </div>
    <div class="kafel">
      <div class="k-etykieta">Średnia w sezonie grzewczym</div>
      <div class="k-wartosc">${fmt(g.srednia)}<small>µg/m³</small></div>
      <div class="k-opis">${opisPomiarow(g, "grzewczy")}</div>
    </div>
    <div class="kafel">
      <div class="k-etykieta">Średnia w sezonie letnim</div>
      <div class="k-wartosc">${fmt(l.srednia)}<small>µg/m³</small></div>
      <div class="k-opis">${opisPomiarow(l, "letni")}</div>
    </div>
    <div class="kafel">
      <div class="k-etykieta">Dni z PM10 powyżej 50 µg/m³</div>
      <div class="k-wartosc">${fmtInt(pm10g.dni_powyzej_50 + pm10l.dni_powyzej_50)}</div>
      <div class="k-opis">z tego ${fmtInt(pm10g.dni_powyzej_50)} zimą</div>
    </div>`;
}

// --- Pytanie 1: sezony --------------------------------------------------------------
function renderSezonowy() {
  const miasta = listaMiast();
  const zbiory = [["letni", "--season-letni"], ["grzewczy", "--season-grzewczy"]].map(([sezon, token]) => ({
    label: `sezon ${sezon}`,
    data: miasta.map((m) => wierszSezonu(m, STAN.wskaznik, sezon)?.srednia ?? null),
    backgroundColor: css(token), borderColor: css("--surface-1"), borderWidth: 2, maxBarThickness: 40,
  }));
  $("#t-sezonowy").textContent = `Średnie ${STAN.wskaznik} według sezonu`;
  $("#p-sezonowy").textContent = "µg/m³ · średnia z dni 2023–2025";
  nowyWykres("wykres-sezonowy", {
    type: "bar",
    data: { labels: miasta, datasets: zbiory },
    options: {
      plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.raw == null ? "brak pomiaru" : fmt(c.raw) + " µg/m³"}` } } },
      scales: { x: { grid: { display: false } }, y: { beginAtZero: true, title: { display: true, text: "µg/m³" } } },
    },
  });
}

function renderMiesieczny() {
  const pole = POLE[STAN.wskaznik];
  const dane = DANE.sezonowe.miesieczne;
  const zbiory = listaMiast().filter((m) => dane[m][pole].some((v) => v != null)).map((m) => ({
    label: m, data: dane[m][pole], borderColor: kolorMiasta(m), backgroundColor: kolorMiasta(m),
    borderWidth: 2, pointRadius: 3, pointHoverRadius: 6, tension: 0.25, order: 1,
  }));
  // Średnia ze wszystkich dni wszystkich miast – gruba czerwona linia z kropkami, na wierzchu,
  // z obwódką w kolorze tła (osobna szersza linia pod spodem), żeby odcinała się od linii miast
  const czerwony = ciemny() ? "#ff5a5a" : "#d92b2b";
  const srednia = dane["Wszystkie miasta"][pole];
  zbiory.push(
    { label: "", obwodka: true, data: srednia, borderColor: css("--surface-1"), borderWidth: 8, pointRadius: 0,
      pointHoverRadius: 0, tension: 0.25, order: 0.5 },
    { label: "średnia wszystkich miast", srednia: true, data: srednia, borderColor: czerwony, backgroundColor: czerwony,
      borderWidth: 4.5, pointRadius: 4, pointHoverRadius: 7, pointBorderColor: css("--surface-1"), pointBorderWidth: 1.5,
      tension: 0.25, order: 0 },
  );
  $("#t-miesieczny").textContent = `${STAN.wskaznik} miesiąc po miesiącu`;
  nowyWykres("wykres-miesieczny", {
    type: "line",
    data: { labels: MIESIACE, datasets: zbiory },
    options: {
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: { filter: (el) => el.text !== "" },  // bez obwódki średniej w legendzie
          // Kliknięcie w legendzie ukrywa/pokazuje serię; dla średniej – razem z jej obwódką
          onClick: (e, el, legenda) => {
            const wykres = legenda.chart;
            const widoczna = !wykres.isDatasetVisible(el.datasetIndex);
            wykres.setDatasetVisibility(el.datasetIndex, widoczna);
            if (wykres.data.datasets[el.datasetIndex].srednia) {
              wykres.setDatasetVisibility(wykres.data.datasets.findIndex((d) => d.obwodka), widoczna);
            }
            wykres.update();
          },
        },
        tooltip: {
          filter: (el) => el.dataset.label !== "",
          itemSort: (a, b) => b.raw - a.raw,
          callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw)} µg/m³` },
        },
      },
      scales: { x: { grid: { display: false } }, y: { beginAtZero: true, title: { display: true, text: "µg/m³" } } },
    },
    plugins: [pasGrzewczy],
  });
}

// --- Pytanie 2: pogoda --------------------------------------------------------------
function renderKorelacje() {
  // Macierz korelacji Spearmana (odporna na skośny rozkład stężeń), jak na wykresie korelacje.png
  const wiersze = DANE.korelacje.macierz.filter((x) => x.zakres === STAN.zakres);
  const para = (a, b) => wiersze.find((x) => x.zmienna_a === a && x.zmienna_b === b);
  const r = (a, b) => para(a, b).r;
  const n = wiersze[0].n;
  const blok = (i) => (i === 2 ? "blok" : "");  // odstęp między pyłem a pogodą
  const naglowek = `<thead><tr><th scope="col"></th>${ZMIENNE.map(([z, skrot], j) =>
    `<th scope="col" class="${blok(j)}" title="${z}">${skrot}</th>`).join("")}</tr></thead>`;
  const cialo = ZMIENNE.map(([a, skrotA], i) => `<tr class="${blok(i)}">` +
    `<th scope="row"><span class="pelna">${a}</span><span class="skrot">${skrotA}</span></th>${ZMIENNE.map(([b], j) => {
    if (i === j) return `<td class="przekatna ${blok(j)}" title="${a} sama ze sobą: r = 1">1</td>`;
    const { r: v, p } = para(a, b);
    const tlo = kolorKorelacji(v);
    return `<td class="${blok(j)}${p >= 0.05 ? " ns" : ""}" style="background-color:${tlo};color:${kolorTekstu(tlo)}" ` +
      `title="${a} a ${b}: r = ${fmtR(v)}, p ${p < 0.001 ? "< 0,001" : `= ${fmt(p, 3)}`}${p >= 0.05 ? " (nieistotna)" : ""}, ` +
      `n = ${fmtInt(n)} dni-miasto">${fmtR(v)}</td>`;
  }).join("")}</tr>`).join("");
  $("#macierz-korelacji").innerHTML = `${naglowek}<tbody>${cialo}</tbody>`;

  // Najsilniejsza para: wybrany wskaźnik z pogodą oraz dwie zmienne pogodowe (współliniowość cech modelu)
  const najsilniejsza = (pary) => pary.reduce((x, y) => (Math.abs(y.r) > Math.abs(x.r) ? y : x));
  const pogoda = ZMIENNE.slice(2).map(([z]) => z);
  const zPylem = najsilniejsza(pogoda.map((z) => ({ opis: z, r: r(STAN.wskaznik, z) })));
  const wPogodzie = najsilniejsza(pogoda.flatMap((a, i) => pogoda.slice(i + 1).map((b) => ({ opis: `${a} i ${b}`, r: r(a, b) }))));
  $("#przypis-korelacji").textContent =
    `${STAN.wskaznik} najmocniej wiąże się z: ${zPylem.opis} (r = ${fmtR(zPylem.r)}). ` +
    `Z samej pogody najmocniej powiązane są ${wPogodzie.opis} (r = ${fmtR(wPogodzie.r)}). ` +
    `Ujemne r: gdy jedno rośnie, drugie spada. Przekreślone pary są nieistotne – najedź na komórkę, żeby zobaczyć p. ` +
    `Przy ${fmtInt(n)} dniach istotne jest już |r| > ${fmt(1.96 / Math.sqrt(n - 2 + 1.96 ** 2), 3)}, więc liczy się siła związku.`;
}

function renderRozrzut() {
  const [i, nazwa, jedn] = STAN.os === "temp" ? [0, "temperatura", "°C"] : [1, "prędkość wiatru", "m/s"];
  $("#t-rozrzut").textContent = nazwa;
  const zbiory = [["letni", "--season-letni"], ["grzewczy", "--season-grzewczy"]].map(([s, token]) => ({
    label: `sezon ${s}`,
    data: DANE.korelacje.punkty[s].map((p) => ({ x: p[i], y: p[2] })),
    backgroundColor: zPrzezroczystoscia(css(token), 0.35), borderWidth: 0, pointRadius: 2.5, pointHoverRadius: 5,
  }));
  nowyWykres("wykres-rozrzut", {
    type: "scatter",
    data: { datasets: zbiory },
    options: {
      plugins: {
        legend: { labels: { usePointStyle: true, pointStyle: "circle" } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${nazwa} ${fmt(c.raw.x)} ${jedn}, PM10 ${fmt(c.raw.y)} µg/m³` } },
      },
      scales: {
        x: { title: { display: true, text: `${nazwa} – średnia dobowa [${jedn}]` } },
        y: { beginAtZero: true, title: { display: true, text: "PM10 [µg/m³]" } },
      },
    },
    plugins: [liniaNormy],
  });
}

// --- Pytanie 3: mapa i ranking -------------------------------------------------------
function opisStacji(s) {
  const biezace = Object.entries(s.biezace || {})
    .map(([w, v]) => `${w}: <strong>${fmt(v.wartosc)}</strong> µg/m³ <span style="opacity:.7">(${v.czas})</span>`).join("<br>");
  return `<strong>${s.nazwa}</strong><br>${s.miasto} · stacja ${s.typ_stacji}<br>
    Średnia 2023–2025: PM10 ${fmt(s.pm10_srednia)}, PM2.5 ${s.pm25_srednia == null ? "brak pomiaru" : fmt(s.pm25_srednia)} µg/m³
    ${biezace ? `<br><br>Ostatni odczyt z API GIOŚ:<br>${biezace}` : ""}`;
}

function zbudujMape() {
  const mapa = L.map("mapa", { scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(mapa);

  // Pozostałe stacje województwa (z API GIOŚ) – szare, w tle
  for (const s of DANE.stacje.inne_stacje) {
    L.circleMarker([s.lat, s.lon], { radius: 5, color: "#ffffff", weight: 1.5, fillColor: "#8c8b85", fillOpacity: 0.9 })
      .bindPopup(`<strong>${s.nazwa}</strong><br>${s.miasto}<br><span style="opacity:.7">stacja spoza próby badawczej</span>`)
      .addTo(mapa);
  }
  // Stacje miast – w kolorach miast
  for (const s of DANE.stacje.stacje) {
    L.circleMarker([s.lat, s.lon], { radius: 10, color: "#ffffff", weight: 2, fillColor: s.kolor, fillOpacity: 0.95 })
      .bindPopup(opisStacji(s)).bindTooltip(s.miasto, { direction: "top", offset: [0, -8] }).addTo(mapa);
  }

  const legenda = L.control({ position: "bottomright" });
  legenda.onAdd = () => {
    const div = L.DomUtil.create("div", "legenda-mapy");
    div.innerHTML = DANE.meta.miasta.map((m) => `<span><i class="kropka" style="background:${m.kolor}"></i>${m.miasto}</span>`).join("")
      + `<span><i class="kropka" style="background:#8c8b85"></i>inne stacje</span>`;
    return div;
  };
  legenda.addTo(mapa);
  mapa.fitBounds(L.latLngBounds(DANE.stacje.stacje.map((s) => [s.lat, s.lon])).pad(0.15));
  $("#p-mapa").textContent = `kliknij stację: średnie 2023–2025 i ostatni odczyt z API GIOŚ (${DANE.meta.dane_biezace.do})`;
}

function renderRanking() {
  const kolumny = [
    ["#", (w) => w.pozycja],
    ["Miasto", (w) => `<span class="miasto"><i class="kropka" style="background:${kolorMiasta(w.miasto)}"></i>${w.miasto}</span>` +
      (w.jednostka.includes(w.miasto) ? "" : `<span class="podpis">GUS: ${w.jednostka}</span>`)],
    ["PM10 śr.", (w) => fmt(w.pm10_srednia)],
    ["PM2.5 śr.", (w) => (w.pm25_srednia == null ? '<span class="brak">brak</span>' : fmt(w.pm25_srednia))],
    ["Dni &gt;50 / rok", (w) => fmt(w.dni_powyzej_50_rocznie)],
    ["Dni złe %", (w) => fmt(w.dni_zla_pct)],
    ["Ludność", (w) => fmtInt(w.ludnosc)],
    ["Sam. os. / 1000", (w) => fmtInt(w.samochody_na_1000)],
    ["Os. / km²", (w) => fmtInt(w.gestosc_zaludnienia)],
  ];
  $("#tabela-ranking").innerHTML =
    `<thead><tr>${kolumny.map(([n]) => `<th scope="col">${n}</th>`).join("")}</tr></thead><tbody>` +
    DANE.ranking.ranking.map((w) => `<tr>${kolumny.map(([, f]) => `<td>${f(w)}</td>`).join("")}</tr>`).join("") + "</tbody>";

  const kor = Object.fromEntries(DANE.ranking.korelacje_gus.map((k) => [k.cecha, k.spearman_r]));
  const rok = Math.max(...DANE.ranking.gus.map((g) => g.rok));
  const pLudnosc = DANE.eksploracja.testy.find((t) => t.h0.includes("ludno"))?.p;
  $("#przypis-ranking").textContent =
    `Dane GUS za ${rok} r. Korelacja średniego PM10 z ludnością: ${fmtR(kor.ludnosc)}, z samochodami na 1000 mieszk.: ` +
    `${fmtR(kor.samochody_na_1000)}, z gęstością zaludnienia: ${fmtR(kor.gestosc_zaludnienia)}. ` +
    `Przy ${DANE.ranking.ranking.length} miastach to tylko wskazówka` +
    (pLudnosc == null ? "." : ` – nawet wynik dla ludności jest nieistotny (p = ${fmt(pLudnosc, 3)}).`);
}

// --- Pytanie 4: model ------------------------------------------------------------------
function renderWyborModelu() {
  $("#wybor-model").innerHTML = DANE.model.modele.map((m) =>
    `<button type="button" role="radio" data-wartosc="${m.model}" aria-checked="${m.model === STAN.model}">${m.model}</button>`).join("");
  const p = DANE.model.podzial;
  $("#opis-model").textContent =
    `Czy z samej pogody, sezonu i miasta da się przewidzieć, czy dzień będzie dobry, umiarkowany czy zły? ` +
    `Model uczy się na latach ${p.train} (${fmtInt(p.n_train)} dni), a sprawdzamy go na roku ${p.test} (${fmtInt(p.n_test)} dni).`;
}

function renderModel() {
  const m = DANE.model.modele.find((x) => x.model === STAN.model);
  const bazowy = DANE.model.modele.find((x) => x.model === "klasa większościowa");
  const porownanie = (k) => (m === bazowy ? "" : ` · bazowo ${fmt(bazowy[k], 2)}`);
  $("#kafle-model").innerHTML = `
    <div class="kafel"><div class="k-etykieta">Balanced accuracy</div><div class="k-wartosc">${fmt(m.balanced_accuracy, 2)}</div>
      <div class="k-opis">średnia trafność w każdej klasie${porownanie("balanced_accuracy")}</div></div>
    <div class="kafel"><div class="k-etykieta">F1 macro</div><div class="k-wartosc">${fmt(m.f1_macro, 2)}</div>
      <div class="k-opis">każda klasa liczy się tak samo${porownanie("f1_macro")}</div></div>
    <div class="kafel"><div class="k-etykieta">Accuracy</div><div class="k-wartosc">${fmt(m.accuracy, 2)}</div>
      <div class="k-opis">${m === bazowy ? "zawsze „dobra”, bo większość dni jest dobra" : "niższe niż bazowe, bo model celowo szuka rzadkich dni złych"}</div></div>
    <div class="kafel"><div class="k-etykieta">Walidacja (2023–2024)</div><div class="k-wartosc">${m.cv_balanced_accuracy == null ? "–" : fmt(m.cv_balanced_accuracy, 2)}</div>
      <div class="k-opis">${m.parametry ? Object.entries(m.parametry).map(([k, v]) => `${k}=${v ?? "brak"}`).join(", ") : "bez strojenia"}</div></div>`;

  // Macierz pomyłek: liczba dni i udział w wierszu (klasie rzeczywistej)
  const etyk = DANE.model.klasy.etykiety;
  const cialo = m.confusion_matrix.map((wiersz, i) => {
    const suma = wiersz.reduce((a, b) => a + b, 0);
    return `<tr><th scope="row">${znacznikKlasy(etyk[i])}</th>${wiersz.map((v, j) => {
      const udzial = suma ? v / suma : 0;
      const styl = `background:${zPrzezroczystoscia(css("--seq"), 0.08 + udzial * 0.85)};` +
        `color:${udzial > 0.55 ? css("--on-accent") : css("--text-primary")};` +
        (i === j ? "box-shadow: inset 0 0 0 2px var(--text-primary);" : "");
      return `<td style="${styl}" title="rzeczywista: ${etyk[i]}, przewidziana: ${etyk[j]}">${fmtInt(v)}<br><small>${fmt(udzial * 100, 0)}%</small></td>`;
    }).join("")}</tr>`;
  }).join("");
  $("#macierz-pomylek").innerHTML =
    `<thead><tr><th scope="col"></th>${etyk.map((e) => `<th scope="col">${e}</th>`).join("")}</tr></thead><tbody>${cialo}</tbody>`;

  $("#tabela-klasy").innerHTML =
    `<thead><tr><th scope="col">Klasa</th><th scope="col">Precyzja</th><th scope="col">Czułość</th><th scope="col">F1</th><th scope="col" title="liczba dni w zbiorze testowym">Dni</th></tr></thead><tbody>` +
    etyk.map((e) => {
      const k = m.per_class[e];
      return `<tr><td>${znacznikKlasy(e)}</td><td>${fmt(k.precision, 2)}</td><td>${fmt(k.recall, 2)}</td><td>${fmt(k["f1-score"], 2)}</td><td>${fmtInt(k.support)}</td></tr>`;
    }).join("") + "</tbody>";

  renderWaznosc(m);
}

// Selekcja cech przed uczeniem: moduł korelacji |r| – znak pominięty, liczy się tylko siła związku
function renderSelekcja() {
  const s = DANE.model.selekcja_cech;
  const skrot = { temperatura: "temp.", "prędkość wiatru": "wiatr", wilgotność: "wilg.", ciśnienie: "ciśn.", "sezon grzewczy": "sezon" };
  const krotko = (e) => skrot[e] ?? e;
  const naglowek = `<thead><tr><th scope="col"></th>${s.etykiety.map((e) => `<th scope="col" title="${e}">${krotko(e)}</th>`).join("")}</tr></thead>`;
  const cialo = s.etykiety.map((a, i) =>
    `<tr><th scope="row"><span class="pelna">${a}</span><span class="skrot">${krotko(a)}</span></th>${s.modul_korelacji[i].map((v, j) => {
      if (i === j) return `<td class="przekatna" title="${a} sama ze sobą">1</td>`;
      const tlo = kolorKorelacji(v);
      const ponadProgiem = v > s.prog ? "box-shadow: inset 0 0 0 2px var(--text-primary);" : "";
      return `<td style="background-color:${tlo};color:${kolorTekstu(tlo)};${ponadProgiem}" ` +
        `title="${a} i ${s.etykiety[j]}: |r| = ${fmt(v, 2)}">${fmt(v, 2)}</td>`;
    }).join("")}</tr>`).join("");
  $("#macierz-cech").innerHTML = `${naglowek}<tbody>${cialo}</tbody>`;

  const p = s.najsilniejsza_para;
  $("#przypis-cech").textContent =
    `Liczone tylko na danych uczących (${DANE.model.podzial.train}). Cechy z |r| > ${fmt(s.prog, 1)} byłyby zbędne. ` +
    `Najmocniej powiązane są ${p.cechy.join(" i ")} (|r| = ${fmt(p.r, 2)}) – ` +
    (s.usuniete.length ? `usunięto: ${s.usuniete.join(", ")}. ` : "to poniżej progu, więc zostają wszystkie cechy. ") +
    `Niosą one częściowo tę samą informację, więc ich ważność w modelu dzieli się między nie. ` +
    `VIF (próg ${s.prog_vif}): ` + s.etykiety.map((e, i) => `${e} ${fmt(s.vif[i], 2)}`).join(" · ") +
    (s.vif.some((v) => v > s.prog_vif) ? " – część cech przekracza próg." : " – wszystkie poniżej progu.");
}

function renderWaznosc(m) {
  if (m.model === "klasa większościowa") {
    WYKRESY["wykres-waznosc"]?.destroy();
    delete WYKRESY["wykres-waznosc"];
    $("#p-waznosc").textContent = "";
    $("#przypis-waznosc").textContent = "Model bazowy zawsze mówi „dobra” i nie korzysta z żadnych cech. To punkt odniesienia.";
    return;
  }
  const las = m.model === "random forest";
  const dane = las
    ? DANE.model.waznosc_cech_rf.map((x) => ({ e: x.etykieta, v: x.permutacyjna }))
    : DANE.model.waznosc_cech_drzewo.map((x) => ({ e: x.etykieta, v: x.gini })).filter((x) => x.v > 0);
  const os = las ? "spadek balanced accuracy" : "ważność Gini";
  dane.sort((a, b) => b.v - a.v);
  $("#t-waznosc").textContent = `Ważność cech – ${m.model}`;
  $("#p-waznosc").textContent = las
    ? "o ile spada wynik modelu, gdy wymieszamy wartości danej cechy"
    : "jak często drzewo dzieli dane według cechy (nieużyte pominięto)";
  $("#przypis-waznosc").textContent = "";
  nowyWykres("wykres-waznosc", {
    type: "bar",
    data: { labels: dane.map((d) => d.e), datasets: [{ label: os, data: dane.map((d) => d.v), backgroundColor: css("--accent"), maxBarThickness: 22 }] },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${os}: ${fmt(c.raw, 3)}` } } },
      scales: { x: { title: { display: true, text: os } }, y: { grid: { display: false } } },
    },
  });
}

// --- Typy dni pogodowych (klasteryzacja) ---------------------------------------------------
function renderKlastry() {
  const K = DANE.klastry;
  const prof = K.profil;
  const pole = POLE[STAN.wskaznik];
  const sr = (p) => p[`${pole}_srednia`];
  const kolor = (p) => kolorWMotywie(p.kolor);
  const najgorszy = prof[0];
  const najlepszy = prof[prof.length - 1];
  const dniZle = prof.reduce((a, p) => a + K.klaster_klasa[p.klaster]["zła"], 0);
  const silhouette = K.wybor_k.find((r) => r.k === K.k).silhouette;

  $("#opis-klastry").textContent =
    `Algorytm k-średnich podzielił ${fmtInt(K.metoda.n)} dni na typy pogody według cech: ${K.metoda.cechy.join(", ")}. ` +
    `Pył nie brał udziału w podziale – służy tylko do opisu typów.`;

  $("#kafle-klastry").innerHTML = `
    <div class="kafel wyrozniony"><div class="k-etykieta">Typ „${najgorszy.nazwa}”</div>
      <div class="k-wartosc">${fmt(najgorszy.udzial_w_dniach_zlych_pct)}<small>% dni złych</small></div>
      <div class="k-opis">${fmtInt(K.klaster_klasa[najgorszy.klaster]["zła"])} ze ${fmtInt(dniZle)} dni z PM10 &gt; 50 µg/m³</div></div>
    <div class="kafel"><div class="k-etykieta">${STAN.wskaznik}: typ ${najgorszy.klaster} vs typ ${najlepszy.klaster}</div>
      <div class="k-wartosc">×${fmt(sr(najgorszy) / sr(najlepszy), 2)}</div>
      <div class="k-opis">${fmt(sr(najgorszy))} vs ${fmt(sr(najlepszy))} µg/m³ średnio</div></div>
    <div class="kafel"><div class="k-etykieta">Typ „${najlepszy.nazwa}”</div>
      <div class="k-wartosc">${fmt(najlepszy.udzial_grzewczy_pct, 0)}<small>% w sezonie grzewczym</small></div>
      <div class="k-opis">${najlepszy.udzial_grzewczy_pct > 50 ? "mimo przewagi dni sezonu grzewczego – " : ""}najniższe średnie ${STAN.wskaznik}</div></div>
    <div class="kafel"><div class="k-etykieta">Liczba typów</div>
      <div class="k-wartosc">k = ${K.k}</div>
      <div class="k-opis">tyle grup daje najlepszy podział (silhouette ${fmt(silhouette, 2)})</div></div>`;

  // Tabela profili typów
  const kolumny = [
    ["Typ dnia", (p) => `<span class="miasto"><i class="kropka" style="background:${kolor(p)}"></i>${p.klaster}. ${p.nazwa}</span>`],
    ["Dni", (p) => fmtInt(p.n_dni_pogodowych)],
    ["Temp. °C", (p) => fmt(p.temp_sr)],
    ["Wiatr m/s", (p) => fmt(p.wiatr_sr)],
    ["Opad mm", (p) => fmt(p.opad)],
    ["Wilg. %", (p) => fmt(p.wilgotnosc, 0)],
    ["Ciśn. hPa", (p) => (p.cisnienie > 0 ? "+" : p.cisnienie < 0 ? "−" : "") + fmt(Math.abs(p.cisnienie))],
    ["Sezon grzewczy", (p) => `${fmt(p.udzial_grzewczy_pct, 0)}%`],
    [`${STAN.wskaznik} śr.`, (p) => fmt(sr(p))],
    ["Dni złe", (p) => `${fmt(p.dni_zla_pct)}%`],
  ];
  $("#tabela-klastry").innerHTML = `<thead><tr>${kolumny.map(([n]) => `<th scope="col">${n}</th>`).join("")}</tr></thead><tbody>` +
    prof.map((p) => `<tr>${kolumny.map(([, f]) => `<td>${f(p)}</td>`).join("")}</tr>`).join("") + "</tbody>";
  $("#p-tabela-klastry").textContent = "średnie dla każdego typu · ciśnienie: różnica od średniej stacji";

  // Rozkład stężenia w typach dni: 10–90 percentyl, kwartyle, mediana, średnia
  const q = K.kwartyle_pm[pole];
  const kolory = prof.map(kolor);
  $("#t-klastry-pm").textContent = `${STAN.wskaznik} w typach dni`;
  nowyWykres("wykres-klastry-pm", {
    type: "bar",
    data: {
      labels: prof.map((p) => [`${p.klaster}. ${p.nazwa.split(", ")[0]},`, p.nazwa.split(", ")[1] || ""]),
      datasets: [
        { label: "10–90 percentyl", data: prof.map((p) => [q[p.klaster][0], q[p.klaster][4]]),
          backgroundColor: kolory.map((k) => zPrzezroczystoscia(k, ciemny() ? 0.5 : 0.3)), barPercentage: 0.22, grouped: false, order: 3 },
        { label: "kwartyle 25–75%", data: prof.map((p) => [q[p.klaster][1], q[p.klaster][3]]),
          backgroundColor: kolory, borderSkipped: false, barPercentage: 0.6, grouped: false, order: 2 },
        { type: "line", label: "mediana", data: prof.map((p) => q[p.klaster][2]), showLine: false,
          pointStyle: "line", pointRadius: 22, pointBorderWidth: 3, borderColor: css("--text-primary"), order: 1 },
        { type: "line", label: "średnia", data: prof.map(sr), showLine: false, pointStyle: "rectRot",
          pointRadius: 5, backgroundColor: css("--text-primary"), borderColor: css("--surface-1"), order: 0 },
      ],
    },
    options: {
      plugins: {
        legend: { labels: { usePointStyle: true } },
        tooltip: { callbacks: { label: (c) => Array.isArray(c.raw)
          ? `${c.dataset.label}: ${fmt(c.raw[0])}–${fmt(c.raw[1])} µg/m³` : `${c.dataset.label}: ${fmt(c.raw)} µg/m³` } },
      },
      scales: { x: { grid: { display: false } }, y: { beginAtZero: true, title: { display: true, text: `${STAN.wskaznik} [µg/m³]` } } },
    },
    plugins: STAN.wskaznik === "PM10" ? [liniaNormy] : [],
  });

  // Udział typów w dniach każdego miesiąca (słupki skumulowane do 100%)
  nowyWykres("wykres-klastry-miesiace", {
    type: "bar",
    data: {
      labels: MIESIACE,
      datasets: prof.map((p) => ({
        label: `${p.klaster}. ${p.nazwa}`, data: MIESIACE.map((_, i) => K.miesiace[i + 1][p.klaster] ?? 0),
        backgroundColor: kolor(p), borderColor: css("--surface-1"), borderWidth: 1,
      })),
    },
    options: {
      plugins: { tooltip: { mode: "index", callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw)}%` } } },
      scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, max: 100, ticks: { callback: (v) => `${v}%` } } },
    },
  });
}

// --- Panele: jeden widoczny naraz, wybór z menu (adres #panel) ----------------------
const PANELE = ["akwizycja", "przygotowanie", "eksploracja", "sezony", "pogoda", "ranking", "model", "typy"];
let mapaGotowa = false;
const panelZAdresu = () => (PANELE.includes(location.hash.slice(1)) ? location.hash.slice(1) : PANELE[0]);

function pokazPanel(id) {
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.dataset.panel !== id; });
  document.querySelectorAll(".menu a").forEach((a) => {
    if (a.dataset.panel === id) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  // Mapa powstaje przy pierwszym otwarciu panelu – Leaflet potrzebuje widocznego kontenera
  if (id === "ranking" && !mapaGotowa) {
    zbudujMape();
    mapaGotowa = true;
  }
  window.scrollTo(0, 0);
}

// --- Panel A: akwizycja danych ----------------------------------------------------------
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const liczba = (v) => (Number.isInteger(v) ? fmtInt(v) : fmt(v));

function probkaHtml(p) {
  if (p.typ === "json") return `<pre class="surowe">${esc(p.tekst)}</pre>`;
  // Tabela: dla CSV pierwszy wiersz to nazwy kolumn, dla XLSX – wiersze arkusza jak w pliku
  const naglowek = p.wiersze.length === 2;
  return `<div class="tabela-wrap"><table class="surowe">${p.wiersze.map((w, i) => `<tr>${w.map((c, j) =>
    (naglowek && i === 0) || (!naglowek && j === 0) ? `<th>${esc(c)}</th>` : `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</table></div>`;
}

function statusHtml(w) {
  if (w.status === 200) return '<span class="status ok">200 OK</span>';
  if (w.status === 400) return '<span class="status uwaga" title="stanowisko manualne – API nie udostępnia danych bieżących">400 manualne</span>';
  return `<span class="status blad">${esc(w.status)}</span>`;
}

// Przepływ / lejek: kroki ze strzałkami; krok z adresem (#panel) jest odnośnikiem do kolejnego panelu
function przeplywHtml(kroki) {
  return kroki.map(([etap, wartosc, opis, adres], i) => {
    const tresc = `<div class="k-wartosc">${esc(wartosc)}</div><div class="k-etykieta">${esc(etap)}</div><div class="k-opis">${esc(opis)}</div>`;
    return `${i ? '<span class="strzalka" aria-hidden="true">→</span>' : ""}` +
      (adres ? `<a class="krok krok-link" href="${esc(adres)}">${tresc}</a>` : `<div class="krok">${tresc}</div>`);
  }).join("");
}

function renderAkwizycja() {
  const A = DANE.akwizycja;
  $("#przeplyw").innerHTML = przeplywHtml(A.przeplyw);

  $("#zrodla-karty").innerHTML = A.zrodla.map((z) => `
    <figure class="karta zrodlo">
      <figcaption>
        <span class="tytul">${esc(z.nazwa)}<span class="plakietka ${z.tk === "TK02" ? "lokalne" : "zdalne"}">${z.tk} · ${esc(z.dostep)}</span></span>
        <span class="pod"><a href="${esc(z.url)}" rel="noopener">${esc(z.url)}</a></span>
      </figcaption>
      <dl class="meta"><dt>Format</dt><dd>${esc(z.format)}</dd><dt>Pobieramy</dt><dd>${esc(z.co)}</dd></dl>
      <div class="liczby">${z.liczby.map(([e, v]) => `<span><strong>${liczba(v)}</strong> ${esc(e)}</span>`).join("")}</div>
      <details>
        <summary>Pobrane pliki (${z.pliki.length})</summary>
        <div class="tabela-wrap"><table class="klasy"><thead><tr><th scope="col">Plik</th><th scope="col">Rozmiar</th><th scope="col">Pobrano</th></tr></thead><tbody>
          ${z.pliki.map((f) => `<tr><td>${esc(f.nazwa)}</td><td>${f.kB >= 1024 ? `${fmt(f.kB / 1024)} MB` : `${fmtInt(f.kB)} kB`}</td><td>${esc(f.pobrano)}</td></tr>`).join("")}
        </tbody></table></div>
      </details>
      <div class="probka"><div class="pod">Surowe dane: ${esc(z.probka.opis)}</div>${probkaHtml(z.probka)}</div>
    </figure>`).join("");

  renderDziennik(A.dziennik);
}

// --- Panel P: przygotowanie danych --------------------------------------------------------
function renderPrzygotowanie() {
  const P = DANE.przygotowanie;
  $("#lejek").innerHTML = przeplywHtml(P.lejek);
  $("#tabela-kroki").innerHTML =
    `<thead><tr><th scope="col">#</th><th scope="col">Krok</th><th scope="col">Co robimy</th><th scope="col">Wynik</th></tr></thead><tbody>` +
    P.kroki.map(([nazwa, co, wynik], i) =>
      `<tr><td>${i + 1}</td><td><b>${esc(nazwa)}</b></td><td>${esc(co)}</td><td>${esc(wynik)}</td></tr>`).join("") + "</tbody>";
  $("#tabela-bazy").innerHTML =
    `<thead><tr><th scope="col">Tabela / widok</th><th scope="col">Typ</th><th scope="col">Wiersze</th></tr></thead><tbody>` +
    P.tabele.map((t) => `<tr><td><code>${esc(t.nazwa)}</code></td><td>${t.typ === "view" ? "widok" : "tabela"}</td>` +
      `<td>${fmtInt(t.wiersze)}</td></tr>`).join("") + "</tbody>";

  // Kontrola jakości: walidacja zakresu i błędy pomiarowe (usunięte / oznaczone, ale zostawione)
  const K = P.kontrola;
  const nazwaWsk = (w) => (w === "PM25" ? "PM2.5" : w);
  const prog = fmtInt(K.prog), x = fmtInt(K.krotnosc);
  $("#reguly-kontrola").innerHTML =
    `<b>Wartości niemożliwe</b> usuwamy: stężenie poniżej 0 albo PM2.5 większe niż PM10 (PM2.5 to część PM10). ` +
    `<b>Błąd czujnika</b> szukamy tylko powyżej ${prog} µg/m³. Sprawdzamy dwie rzeczy: a) czy wartość jest ponad ${x} razy ` +
    `większa niż na innych stacjach w tej samej godzinie, b) czy jest ponad ${x} razy większa niż godzinę przed i po. ` +
    `Oba warunki naraz to błąd – wartość usuwamy. Jeden warunek to tylko oznaczenie – wartość zostaje.`;
  const komorka = (n, klasa = "") => (n ? `<td class="${klasa}">${fmtInt(n)}</td>` : '<td class="brak">0</td>');
  $("#tabela-kontrola").innerHTML =
    `<thead><tr><th scope="col">Stacja</th><th scope="col">Wskaźnik</th><th scope="col">Godzin</th>` +
    `<th scope="col">&lt; 0</th><th scope="col">PM2.5 &gt; PM10</th><th scope="col">Usunięte (a i b)</th>` +
    `<th scope="col">Tylko a) – zostaje</th><th scope="col">Tylko b) – zostaje</th></tr></thead><tbody>` +
    K.stacje.map((s) => `<tr><td>${esc(s.miasto)}</td><td>${nazwaWsk(s.wskaznik)}</td><td>${fmtInt(s.godzin_po_kontroli)}</td>` +
      `${komorka(s.ujemne, "brak-dni")}${komorka(s.pm25_ponad_pm10, "brak-dni")}${komorka(s.usuniete, "brak-dni")}` +
      `${komorka(s.odstaje_od_innych)}${komorka(s.skok_w_czasie)}</tr>`).join("") + "</tbody>";
  const kiedy = (czas) => `${czas.slice(8, 10)}.${czas.slice(5, 7)}.${czas.slice(0, 4)} godz. ${czas.slice(11, 13)}`;
  const powod = { "usunięta": "oba warunki → USUNIĘTA",
    "odstaje od innych stacji": "tylko a), bo wysoko było też godzinę przed i po → ZOSTAJE",
    "skok w czasie": "tylko b), bo inne stacje też miały wysoko → ZOSTAJE" };
  $("#przyklady-kontrola").innerHTML = K.przyklady.map((p) =>
    `<p class="przypis"><b>${esc(p.miasto)}, ${nazwaWsk(p.wskaznik)}, ${kiedy(p.czas)}:</b> ${fmt(p.wartosc)} µg/m³, ` +
    `inne stacje ${fmt(p.mediana_innych)}, godzinę przed ${fmt(p.przed)}, godzinę po ${fmt(p.po)} → ${powod[p.decyzja]}</p>`).join("");
  const suma = (k) => K.stacje.reduce((a, s) => a + s[k], 0);
  const najw = K.stacje.reduce((a, s) => (s.odstaje_od_innych > a.odstaje_od_innych ? s : a));
  const niemozliwe = suma("ujemne") + K.stacje.filter((s) => s.wskaznik === "PM25").reduce((a, s) => a + s.pm25_ponad_pm10, 0);
  $("#przypis-kontrola").textContent =
    (niemozliwe ? `Wartości niemożliwych: ${fmtInt(niemozliwe)}. ` : "Wartości niemożliwych nie było. ") +
    `Usunięto ${fmtInt(suma("usuniete"))} błędnych wartości, a ${fmtInt(suma("odstaje_od_innych") + suma("skok_w_czasie"))} ` +
    `oznaczono i zostawiono. Najczęściej od innych stacji odstaje ${najw.miasto} ${nazwaWsk(najw.wskaznik)} ` +
    `(${fmtInt(najw.odstaje_od_innych)} godzin). Każde miasto ma jedną stację, więc porównujemy z innymi miastami – ` +
    `a te mogą się po prostu różnić. Dlatego sam warunek a) nic nie usuwa.`;

  // Filtracja: ile dni zostało i w którym mieście brakowało której zmiennej
  const F = P.filtracja;
  const ZMIENNE_F = [["pm10", "PM10"], ["pm25", "PM2.5"], ["temp_sr", "temperatura"], ["opad", "opad"],
                     ["wiatr_sr", "wiatr"], ["wilgotnosc", "wilgotność"], ["cisnienie", "ciśnienie"]];
  $("#p-filtracja").textContent = `dzień zostaje tylko, gdy wszystkie ${listaMiast().length} miasta mają PM10, PM2.5 i pełną pogodę`;
  const brak = (m, z) => F.braki.find((b) => b.miasto === m && b.zmienna === z)?.dni ?? 0;
  $("#tabela-filtracja").innerHTML =
    `<thead><tr><th scope="col">Dni z brakiem</th>${ZMIENNE_F.map(([, n]) => `<th scope="col">${n}</th>`).join("")}</tr></thead><tbody>` +
    listaMiast().map((m) => `<tr><td>${esc(m)}</td>${ZMIENNE_F.map(([z]) => {
      const n = brak(m, z);
      return n ? `<td class="brak-dni">${fmtInt(n)}</td>` : '<td class="brak">–</td>';
    }).join("")}</tr>`).join("") + "</tbody>";
  $("#przypis-filtracja").textContent =
    `Zostało ${fmtInt(F.dni_kompletne)} z ${fmtInt(F.dni_kalendarzowe)} dni (${fmt(100 * F.dni_kompletne / F.dni_kalendarzowe, 0)}%), ` +
    `usunięto ${fmtInt(F.dni_kalendarzowe - F.dni_kompletne)}. Jeden dzień może mieć kilka braków naraz, więc liczby ` +
    `w tabeli się nie sumują. Krosno i Jasło mają pogodę z tej samej stacji.`;
}

// Przykład jednej doby: 24 wartości godzinowe przed i po kontroli, średnia dobowa przed i po
function renderDoba() {
  const P = DANE.przygotowanie;
  const d = P.przyklad_doby;
  if (!d.length) { $("#karta-doba").hidden = true; return; }
  const sr = (k) => { const v = d.map((x) => x[k]).filter((x) => x != null); return [v.reduce((a, b) => a + b, 0) / v.length, v.length]; };
  const [srPrzed, nPrzed] = sr("przed"), [srPo, nPo] = sr("po");
  const usuniete = d.filter((x) => x.przed != null && x.po == null);
  const wsk = d[0].wskaznik === "PM25" ? "PM2.5" : d[0].wskaznik;
  const data = `${d[0].data.slice(8, 10)}.${d[0].data.slice(5, 7)}.${d[0].data.slice(0, 4)}`;
  $("#t-doba").textContent = `Przykład: ${d[0].miasto}, ${wsk}, ${data} – przed i po kontroli`;
  $("#p-doba").textContent = "słupek = jedna godzina [µg/m³] · czerwony: usunięty błąd · linie: średnia z doby";
  const stala = (v) => d.map(() => v);
  nowyWykres("wykres-doba", {
    type: "bar",
    data: {
      labels: d.map((x) => String(x.godzina)),
      datasets: [
        { type: "bar", label: "pomiar po kontroli", data: d.map((x) => x.po), backgroundColor: css("--accent"), grouped: false, order: 2 },
        { type: "bar", label: "usunięty (błąd)", data: d.map((x) => (x.przed != null && x.po == null ? x.przed : null)),
          backgroundColor: css("--class-zla"), grouped: false, order: 2 },
        { type: "line", label: `średnia przed kontrolą: ${fmt(srPrzed)}`, data: stala(srPrzed), borderColor: css("--text-muted"),
          borderDash: [6, 4], borderWidth: 2, pointRadius: 0, order: 1 },
        { type: "line", label: `średnia po kontroli: ${fmt(srPo)}`, data: stala(srPo), borderColor: css("--text-primary"),
          borderWidth: 2, pointRadius: 0, order: 1 },
      ],
    },
    options: {
      plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label.split(":")[0]}: ${fmt(c.raw)} µg/m³` } } },
      scales: { x: { title: { display: true, text: "godzina (koniec przedziału, CET)" }, grid: { display: false } },
                y: { beginAtZero: true, title: { display: true, text: `${wsk} [µg/m³]` } } },
    },
  });
  const godz = usuniete.map((x) => `${x.godzina}:00 (${fmt(x.przed)} µg/m³)`).join(", ");
  $("#przypis-doba").textContent =
    `Usunięta godzina: ${godz}. Średnia z doby spada z ${fmt(srPrzed)} do ${fmt(srPo)} µg/m³. ` +
    `Zostało ${nPo} z 24 godzin – ` +
    (nPo >= P.min_godzin ? `wystarczy (minimum ${P.min_godzin}), więc doba zostaje.`
                         : `za mało (minimum ${P.min_godzin}), więc doba nie ma średniej.`);
}

// --- Panel E: eksploracja danych (EDA) ----------------------------------------------------
const ZMIENNE_EDA = [["PM10", "PM10"], ["PM2.5", "PM2.5"], ["temperatura", "temp."], ["prędkość wiatru", "wiatr"],
  ["opad", "opad"], ["wilgotność", "wilg."], ["ciśnienie", "ciśn."]];
const statEda = (z) => DANE.eksploracja.statystyki.find((s) => s.zmienna === z);

function renderEksploracja() {
  const S = DANE.eksploracja.statystyki;
  const pm10 = statEda("PM10"), opad = statEda("opad"), cisn = statEda("ciśnienie"),
    temp = statEda("temperatura"), wiatr = statEda("prędkość wiatru");
  $("#kafle-eda").innerHTML = `
    <div class="kafel wyrozniony"><div class="k-etykieta">PM10: średnia vs mediana [µg/m³]</div>
      <div class="k-wartosc">${fmt(pm10.srednia)} vs ${fmt(pm10.mediana)}</div>
      <div class="k-opis">średnia jest wyższa, bo podbijają ją rzadkie dni ze smogiem</div></div>
    <div class="kafel"><div class="k-etykieta">Skośność PM10 po logarytmie</div>
      <div class="k-wartosc">${fmt(pm10.skosnosc_po_log, 2)}</div>
      <div class="k-opis">przed: ${fmt(pm10.skosnosc, 2)} – po logarytmie rozkład jest prawie symetryczny</div></div>
    <div class="kafel"><div class="k-etykieta">Opad: dni bez opadu</div>
      <div class="k-wartosc">${fmt(opad.udzial_zer_pct, 0)}%</div>
      <div class="k-opis">mediana ${fmt(opad.mediana)} mm, średnia ${fmt(opad.srednia)} mm</div></div>
    <div class="kafel"><div class="k-etykieta">Ciśnienie: odchylenie standardowe</div>
      <div class="k-wartosc">${fmt(cisn.odch_std)} hPa</div>
      <div class="k-opis">na jednej stacji tylko ${fmt(cisn.odch_std_w_grupie)} – resztę daje różna wysokość stacji</div></div>`;

  const dominanta = (s) => { const d = Number.isInteger(s.dominanta_do - s.dominanta_od) ? 0 : 1; return `${fmt(s.dominanta_od, d)}–${fmt(s.dominanta_do, d)}`; };
  $("#p-opisowe").textContent = "typowa wartość, rozrzut i kształt rozkładu każdej zmiennej";
  $("#tabela-opisowe").innerHTML =
    `<thead><tr><th scope="col">Zmienna</th><th scope="col">n</th><th scope="col">Średnia</th><th scope="col">Mediana</th>` +
    `<th scope="col" title="przedział modalny histogramu">Dominanta</th><th scope="col">Odch. std.</th>` +
    `<th scope="col" title="w jednostkach do kwadratu">Wariancja</th><th scope="col" title="odchylenie / średnia">Wsp. zmienności</th>` +
    `<th scope="col">Skośność</th><th scope="col" title="kurtoza nadwyżkowa – rozkład normalny ma 0">Kurtoza</th>` +
    `<th scope="col">Kształt rozkładu</th></tr></thead><tbody>` +
    S.map((s) => `<tr><td><b>${esc(s.zmienna)}</b> <span class="jedn">[${esc(s.jednostka)}]</span></td><td>${fmtInt(s.n)}</td>` +
      `<td>${fmt(s.srednia)}</td><td>${fmt(s.mediana)}</td><td>${dominanta(s)}</td><td>${fmt(s.odch_std)}</td>` +
      `<td>${fmt(s.wariancja)}</td>` +
      (s.wsp_zmiennosci_pct == null ? '<td class="brak" title="°C nie ma prawdziwego zera – stosunek odchylenia do średniej nic nie znaczy">–</td>'
                                    : `<td>${fmt(s.wsp_zmiennosci_pct, 0)}%</td>`) +
      `<td>${fmt(s.skosnosc, 2)}</td><td>${fmt(s.kurtoza, 2)}</td>` +
      `<td>${esc(s.ksztalt)}${s.skosnosc_po_log == null ? "" : ` <span class="jedn">(po log: ${fmt(s.skosnosc_po_log, 2)})</span>`}</td></tr>`).join("") +
    "</tbody>";
  $("#przypis-opisowe").textContent =
    `Pył: ${fmtInt(pm10.n)} dni (4 miasta). Pogoda: ${fmtInt(temp.n)} dni (3 stacje – Krosno i Jasło mają wspólną). ` +
    `Dominanta to najczęstszy przedział. Rozkład normalny ma skośność 0 i kurtozę 0. Skośność powyżej 1 oznacza ` +
    `wyraźnie niesymetryczny rozkład. Wariancja jest w jednostkach do kwadratu, dlatego łatwiej czytać odchylenie.`;

  const li = (t) => `<li>${t}</li>`;
  $("#wnioski-eda").innerHTML = [
    `<b>Pył jest mocno przesunięty w prawo</b> (skośność PM10 ${fmt(pm10.skosnosc, 2)}): większość dni jest czysta, ` +
      `a kilka bardzo brudnych. Dlatego mediana (${fmt(pm10.mediana)}) lepiej opisuje typowy dzień niż średnia ` +
      `(${fmt(pm10.srednia)}), a korelacje liczymy metodą Spearmana, która nie wymaga rozkładu normalnego.`,
    `<b>Regresja liniowa zakłada rozkład normalny</b> – dla pyłu trzeba by go najpierw zlogarytmować. ` +
      `Model w panelu 4 (drzewo i las losowy) nie ma takich wymagań.`,
    `<b>Opad:</b> ${fmt(opad.udzial_zer_pct, 0)}% dni bez deszczu, więc w klasteryzacji używamy jego logarytmu.`,
    `<b>Zmienne mają różne skale</b> (wariancja PM10 ${fmt(pm10.wariancja)}, wiatru ${fmt(wiatr.wariancja)}), ` +
      `więc przed klasteryzacją sprowadzamy je do tej samej skali. Inaczej PM10 przytłoczyłoby resztę.`,
    `<b>Ciśnienie</b> różni się między stacjami przez ich wysokość, więc w klasteryzacji liczy się ` +
      `tylko różnica od średniej danej stacji.`,
    `<b>Temperatura</b> ma dwa szczyty: zimowy i letni. Dlatego wyniki liczymy też osobno dla sezonów, ` +
      `a model dostaje informację o sezonie.`,
  ].map(li).join("");

  // Reguła 68–95–99,7: porównanie z rozkładem normalnym (różnica > 5 p.p. lub > 1% poza 3σ – wyróżniona)
  const NORMALNY = [68.269, 95.44997, 99.730];  // P(|Z| ≤ k) w % dla k = 1, 2, 3
  const pct = (v, wzor, tol) => `<td class="${Math.abs(v - wzor) > tol ? "odbiega" : ""}">${fmt(v)}%</td>`;
  $("#tabela-3sigma").innerHTML =
    `<thead><tr><th scope="col">Zmienna</th><th scope="col">Przekształcenie</th><th scope="col">w ±1σ</th>` +
    `<th scope="col">w ±2σ</th><th scope="col">w ±3σ</th><th scope="col">poza ±3σ</th></tr></thead><tbody>` +
    `<tr class="wzorzec"><td><b>rozkład normalny</b></td><td>–</td>${NORMALNY.map((v) => `<td>${fmt(v)}%</td>`).join("")}<td>0,3%</td></tr>` +
    DANE.eksploracja.regula_3sigma.map((r) => {
      const poza = 100 * r.poza_3_sigma / r.n;
      return `<tr><td>${esc(r.zmienna)}</td><td>${esc(r.przeksztalcenie)}</td>${pct(r.w_1_sigma_pct, NORMALNY[0], 5)}` +
        `${pct(r.w_2_sigma_pct, NORMALNY[1], 2)}${pct(r.w_3_sigma_pct, NORMALNY[2], 1)}` +
        `<td class="${poza > 1 ? "odbiega" : ""}">${fmtInt(r.poza_3_sigma)} (${fmt(poza)}%)</td></tr>`;
    }).join("") + "</tbody>";
  const T = DANE.eksploracja.pm10_ponad_3sigma;
  const log10 = DANE.eksploracja.regula_3sigma.find((r) => r.zmienna === "PM10" && r.przeksztalcenie !== "bez zmian");
  $("#przypis-3sigma").textContent =
    `PM10 powyżej średnia + 3σ (${fmt(T.granica)} µg/m³) ma ${fmtInt(T.dni)} dni ` +
    (T.w_tym_zle === T.dni ? "i wszystkie to dni ze smogiem" : `– z tego ${fmtInt(T.w_tym_zle)} to dni ze smogiem`) +
    ` (${fmtInt(T.w_tym_zle)} ze ${fmtInt(T.zle_wszystkie)}). Gdybyśmy usunęli je jako „odstające”, wycięlibyśmy połowę smogu. ` +
    `Dlatego reguły 3σ nie używamy do czyszczenia danych. Po logarytmie PM10 pasuje do niej prawie idealnie ` +
    `(${fmt(log10.w_1_sigma_pct)} / ${fmt(log10.w_2_sigma_pct)} / ${fmt(log10.w_3_sigma_pct)}%).`;

  $("#wybor-zmienna").innerHTML = ZMIENNE_EDA.map(([z, skrot]) =>
    `<button type="button" role="radio" data-wartosc="${z}" aria-checked="${z === STAN.zmiennaEda}" title="${z}">${skrot}</button>`).join("");
}

// Testy istotności dla pytań 1–3 (wiersze bez wskaźnika – korelacje i GUS – widoczne zawsze)
const fmtP = (p) => (p == null ? "–" : p < 0.001 ? "&lt; 0,001" : fmt(p, 3));
function renderTesty() {
  const E = DANE.eksploracja;
  const wiersze = E.testy.filter((t) => t.wskaznik == null || t.wskaznik === STAN.wskaznik)
    .sort((a, b) => a.pytanie - b.pytanie);
  $("#p-testy").textContent = `czy wyniki pytań 1–3 mogą być przypadkiem · wskaźnik: ${STAN.wskaznik}`;
  $("#tabela-testy").innerHTML =
    `<thead><tr><th scope="col">Pyt.</th><th scope="col">Test</th><th scope="col">Hipoteza zerowa (H0)</th><th scope="col">Dane</th>` +
    `<th scope="col">Statystyka</th><th scope="col">p</th><th scope="col">Wielkość efektu</th><th scope="col">Wniosek</th></tr></thead><tbody>` +
    wiersze.map((t) => {
      const efekt = t.efekt_nazwa.startsWith("pary") ? fmtInt(t.efekt) : t.efekt_nazwa.startsWith("r ") ? fmtR(t.efekt)
        : fmt(t.efekt, t.efekt_nazwa.startsWith("różnica") ? 1 : 2);
      const wniosek = t.wniosek === "odrzucamy H0" ? "<b>odrzucamy H0</b>" : t.wniosek === "zob. uwagi" ? "" : esc(t.wniosek);
      return `<tr><td>${t.pytanie}</td><td>${esc(t.test)}</td><td>${esc(t.h0)}</td><td>${esc(t.dane)}</td>` +
        `<td>${esc(t.statystyka)}</td><td class="${t.p != null && t.p >= 0.05 ? "odbiega" : ""}">${fmtP(t.p)}</td>` +
        `<td>${esc(t.efekt_nazwa)}: ${efekt}</td>` +
        `<td>${wniosek}${t.uwagi ? `<div class="jedn">${esc(t.uwagi)}</div>` : ""}</td></tr>`;
    }).join("") + "</tbody>";
  const ak = E.autokorelacja.filter((a) => a.wskaznik === STAN.wskaznik).map((a) => a.r_lag1);
  $("#przypis-testy").textContent =
    `p < 0,05 oznacza, że wynik raczej nie jest przypadkiem. Uwaga: smog trwa kilka dni, więc kolejne dni są do siebie ` +
    `podobne (autokorelacja ${fmt(Math.min(...ak), 2)}–${fmt(Math.max(...ak), 2)}) i p z danych dziennych wychodzi za małe. ` +
    `Dlatego testy powtarzamy na średnich miesięcznych. Przy tysiącach dni nawet słaby efekt jest „istotny”, ` +
    `więc podajemy też jego wielkość: P(dzień zimowy > letni) = 0,5 to brak różnicy, a W Kendalla = 1 to ta sama ` +
    `kolejność miast każdego dnia. Dla GUS są tylko 4 miasta, więc nawet r = −1 może wyjść przypadkiem.`;
}

// Wtyczka: pionowe linie średniej i mediany na histogramie (oś kategorii = kolejne przedziały)
const linieRozkladu = {
  id: "linieRozkladu",
  afterDatasetsDraw(chart, _args, opt) {
    const { ctx, chartArea: a, scales: { x } } = chart;
    const krok = x.getPixelForValue(1) - x.getPixelForValue(0);
    ctx.save();
    for (const l of opt.linie ?? []) {
      const px = x.getPixelForValue(0) + ((l.wartosc - opt.od) / opt.szer - 0.5) * krok;
      if (px < a.left || px > a.right) continue;
      ctx.strokeStyle = l.kolor; ctx.lineWidth = 2.5; ctx.setLineDash(l.przerywana ? [6, 4] : []);
      ctx.beginPath(); ctx.moveTo(px, a.top); ctx.lineTo(px, a.bottom); ctx.stroke();
    }
    ctx.restore();
  },
};

function renderHistogram() {
  const s = statEda(STAN.zmiennaEda);
  const h = DANE.eksploracja.histogramy[STAN.zmiennaEda];
  // Długi ogon: oś do 99,5 percentyla, żeby było widać kształt (pominięte obserwacje opisane pod wykresem)
  let koniec = h.length;
  if (s.skosnosc >= 1) {
    const suma = h.reduce((a, b) => a + b[2], 0);
    let narast = 0;
    koniec = h.findIndex((b) => (narast += b[2]) / suma >= 0.995) + 1;
  }
  const widoczne = h.slice(0, koniec);
  const pominiete = h.slice(koniec).reduce((a, b) => a + b[2], 0);
  const szer = h[0][1] - h[0][0];
  const d = Number.isInteger(szer) ? 0 : 1;
  $("#t-histogram").textContent = `Rozkład: ${s.zmienna} [${s.jednostka}]`;
  nowyWykres("wykres-histogram", {
    type: "bar",
    data: {
      labels: widoczne.map(([od]) => fmt(od, d)),
      datasets: [{ label: "obserwacje", data: widoczne.map((b) => b[2]), barPercentage: 1, categoryPercentage: 1,
        backgroundColor: widoczne.map(([od]) => (od === s.dominanta_od ? css("--accent") : zPrzezroczystoscia(css("--accent"), 0.45))) }],
    },
    options: {
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { title: (c) => { const [od, doo] = widoczne[c[0].dataIndex]; return `${fmt(od, d)}–${fmt(doo, d)} ${s.jednostka}`; },
                                label: (c) => `${fmtInt(c.raw)} obserwacji` } },
        linieRozkladu: { od: h[0][0], szer, linie: [
          { wartosc: s.srednia, kolor: css("--season-grzewczy") },
          { wartosc: s.mediana, kolor: css("--text-primary"), przerywana: true }] },
      },
      scales: { x: { title: { display: true, text: `${s.zmienna} [${s.jednostka}] – początek przedziału` }, grid: { display: false } },
                y: { beginAtZero: true, title: { display: true, text: "liczba obserwacji" } } },
    },
    plugins: [linieRozkladu],
  });
  const wGrupie = s.odch_std_w_grupie / s.odch_std < 0.8
    ? ` Na jednej stacji odchylenie to tylko ${fmt(s.odch_std_w_grupie)} (łącznie ${fmt(s.odch_std)}) – część rozrzutu to różnice między stacjami.` : "";
  $("#przypis-histogram").innerHTML =
    `<span class="legenda-linia srednia"></span> średnia ${fmt(s.srednia)} · <span class="legenda-linia mediana"></span> mediana ` +
    `${fmt(s.mediana)} · dominanta ${fmt(s.dominanta_od, d)}–${fmt(s.dominanta_do, d)} ${esc(s.jednostka)} · ${esc(s.ksztalt)}.` +
    (pominiete ? ` Na wykresie pominięto ${fmtInt(pominiete)} najwyższych wartości (do ${fmt(s.max)}), żeby było widać kształt.` : "") +
    wGrupie;
}

function renderDziennik(D) {
  if (!D) {
    $("#p-dziennik").textContent = "Brak dziennika – uruchom python -m src.main.";
    return;
  }
  const ile = (warunek) => D.zapytania.filter(warunek).length;
  const zDysku = D.pamiec_podreczna.filter((p) => p.z_dysku).map((p) => `${p.plik} (pobrany ${p.data_pliku})`);
  $("#p-dziennik").textContent = `Uruchomienie ${D.uruchomienie} · ${D.zapytania.length} zapytań: ${ile((w) => w.status === 200)} × 200, ` +
    `${ile((w) => w.status === 400)} × 400 (stanowiska manualne – bez danych bieżących)` +
    (zDysku.length ? ` · z dysku zamiast z sieci: ${zDysku.join(", ")}` : "");
  $("#tabela-dziennik").innerHTML =
    `<thead><tr><th scope="col">Czas</th><th scope="col">API</th><th scope="col">Zapytanie</th><th scope="col">Status</th><th scope="col">Czas odp.</th><th scope="col">Rozmiar</th></tr></thead><tbody>` +
    D.zapytania.map((w) => {
      const u = new URL(w.adres);
      const sciezka = (u.pathname + u.search).replace(/^\/pjp-api\/v1\/rest\/|^\/api\/v1\//, "");
      return `<tr><td>${esc(w.czas.slice(11))}</td><td>${esc(w.api)}</td><td title="${esc(w.adres)}">${esc(sciezka)}</td>` +
        `<td>${statusHtml(w)}</td><td>${fmtInt(w.ms)} ms</td><td>${w.bajty == null ? "–" : `${fmt(w.bajty / 1024)} kB`}</td></tr>`;
    }).join("") + "</tbody>";
}

// Progi klas (panel modelu), data wygenerowania i zakres lat (pasek boczny)
function renderMeta() {
  const m = DANE.meta;
  const [a, b] = m.progi_pm10.granice;
  const [k1, k2, k3] = m.progi_pm10.klasy;
  $("#progi").innerHTML = `Klasy dnia według dobowego PM10: ${k1} ≤ ${a} µg/m³ · ${k2} ${a}–${b} µg/m³ · ${k3} &gt; ${b} µg/m³ (dobowa wartość dopuszczalna UE).`;
  $("#wygenerowano").textContent = `Dane wygenerowane ${m.wygenerowano}. Pogoda: ${m.miasta.map((x) => `${x.miasto} ← ${x.stacja_meteo}`).join(", ")}.`;
  $('[data-meta="zakres"]').textContent = `${m.zakres.od.slice(0, 4)}–${m.zakres.do.slice(0, 4)}`;
}

// --- Przełączniki -----------------------------------------------------------------------
function podepnijPrzelacznik(id, klucz, poZmianie) {
  $(id).addEventListener("click", (e) => {
    const b = e.target.closest("button[data-wartosc]");
    if (!b) return;
    STAN[klucz] = b.dataset.wartosc;
    $(id).querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", x === b));
    poZmianie();
  });
}

function renderWszystko() {
  ustawChartJs();
  renderKafle(); renderSezonowy(); renderMiesieczny();
  renderKorelacje(); renderRozrzut();
  renderRanking(); renderModel(); renderSelekcja(); renderKlastry(); renderDoba(); renderHistogram();
}

// --- Start ----------------------------------------------------------------------------
(async function start() {
  try {
    await wczytajDane();
  } catch (err) {
    pokazBlad(err);
    return;
  }
  renderMeta();
  renderAkwizycja();
  renderPrzygotowanie();
  renderEksploracja();
  renderTesty();
  renderWyborModelu();
  podepnijPrzelacznik("#wybor-wskaznik", "wskaznik", () => {
    renderKafle(); renderSezonowy(); renderMiesieczny(); renderKorelacje(); renderKlastry(); renderTesty();
  });
  podepnijPrzelacznik("#wybor-zakres", "zakres", renderKorelacje);
  podepnijPrzelacznik("#wybor-os", "os", renderRozrzut);
  podepnijPrzelacznik("#wybor-zmienna", "zmiennaEda", renderHistogram);
  podepnijPrzelacznik("#wybor-model", "model", renderModel);
  // Zmiana motywu systemu → wykresy w nowych kolorach
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderWszystko);
  renderWszystko();
  // Linki menu zmieniają adres (#panel); przyciski Wstecz/Dalej też przełączają panele
  window.addEventListener("hashchange", () => pokazPanel(panelZAdresu()));
  pokazPanel(panelZAdresu());
})();
