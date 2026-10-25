/* TK05 – strona z wynikami (Chart.js + Leaflet, dane z data/*.json).
 *
 * Układ pliku:
 *   stan strony → pomocnicze → wczytanie danych → Chart.js i wtyczki → nawigacja między panelami
 *   → panele w kolejności menu: A (akwizycja), P (przygotowanie), E (eksploracja), 1 (sezony),
 *     2 (pogoda), 3 (ranking i mapa), 4 (klasy dni), 5 (typy dni) → pasek boczny → przełączniki → start.
 * Każdy panel ma funkcje render…(), które wpisują dane z DANE.<plik> do elementów strony.
 */
"use strict";

// --- Stan strony ----------------------------------------------------------------
const STAN = {
  wskaznik: "PM10",        // "PM10" | "PM2.5"
  zakres: "cały rok",      // zakres sezonu w macierzy korelacji
  os: "temp",              // oś X wykresu rozrzutu: "temp" | "wiatr"
  zmiennaEda: "PM10",      // zmienna na histogramie w panelu eksploracji
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
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const liczba = (v) => (Number.isInteger(v) ? fmtInt(v) : fmt(v));

// --- Wczytanie danych -------------------------------------------------------------
async function wczytajDane() {
  const pliki = ["meta", "akwizycja", "przygotowanie", "eksploracja", "sezonowe", "korelacje", "stacje", "ranking", "klasy", "klastry"];
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

// --- Nawigacja: jeden panel widoczny naraz, wybór z menu (adres #panel) ----------------------
const PANELE = ["akwizycja", "przygotowanie", "eksploracja", "sezony", "pogoda", "ranking", "klasy", "typy"];
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
// Schemat: 3 źródła (pliki z archiwów TK02, zapytania do API TK03) zbiegają się w jeden strumień
// → surowe pliki → pomiary godzinowe → panel P. Pod schematem podgląd surowych danych (zakładki).

const GRUPY_ZRODEL = [["TK02", "Pliki z archiwów"], ["TK03", "Zapytania do API"]];
// Strzałka schematu: linia + grot; rysowana poziomo, w układzie pionowym obracana w CSS
const GROT = '<svg class="grot" viewBox="0 0 32 12" aria-hidden="true" focusable="false">' +
  '<path class="g-linia" d="M0 6h24"/><path class="g-grot" d="M21 0l11 6-11 6z"/></svg>';

// Krótki znacznik formatu (XLSX/CSV/JSON-LD/JSON) – z eksportu, a w starszych danych z opisu formatu
const znacznikFormatu = (z) => z.znacznik ?? z.format.match(/JSON-LD|JSON|XLSX|CSV/)?.[0] ?? "plik";
const rozmiarPliku = (kB) => (kB >= 1024 ? `${fmt(kB / 1024)} MB` : `${fmtInt(kB)} kB`);
const dataPobrania = (t) => `${t.slice(8, 10)}.${t.slice(5, 7)}.${t.slice(0, 4)} ${t.slice(11, 16)}`;
// „GIOŚ – archiwum pomiarów” → ["GIOŚ", "archiwum pomiarów"] (instytucja pogrubiona w zakładce)
const czesciNazwy = (nazwa) => { const [kto, ...co] = nazwa.split(" – "); return [kto, co.join(" – ")]; };

function probkaHtml(p) {
  if (p.typ === "json") return `<pre class="surowe">${esc(p.tekst)}</pre>`;
  // Tabela: dla CSV pierwszy wiersz to nazwy kolumn, dla XLSX – wiersze arkusza jak w pliku
  const naglowek = p.wiersze.length === 2;
  return `<div class="tabela-wrap"><table class="surowe">${p.wiersze.map((w, i) => `<tr>${w.map((c, j) =>
    (naglowek && i === 0) || (!naglowek && j === 0) ? `<th>${esc(c)}</th>` : `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</table></div>`;
}

// Lewa kolumna schematu: etykiety grup i węzły źródeł. Klasy szyna-* rysują wspólną pionową linię
// od środka pierwszego do środka ostatniego węzła (każdy wiersz dorysowuje swój odcinek).
function schematZrodelHtml(zrodla) {
  const wiersze = GRUPY_ZRODEL.flatMap(([tk, nazwa]) => {
    const wGrupie = zrodla.filter((z) => z.tk === tk);
    return wGrupie.length ? [{ tk, nazwa }, ...wGrupie] : [];
  });
  const wezly = wiersze.flatMap((w, i) => (w.id ? [i] : []));
  const [pierwszy, ostatni] = [wezly[0], wezly.at(-1)];
  const szyna = (i) => (pierwszy === ostatni ? "" : i === pierwszy ? "szyna-od" : i === ostatni ? "szyna-do"
    : i > pierwszy && i < ostatni ? "szyna" : "");
  let nr = 0;
  return wiersze.map((w, i) => {
    if (!w.id) return `<p class="grupa-etykieta ${w.tk.toLowerCase()} ${szyna(i)}"><i aria-hidden="true"></i>${esc(w.nazwa)}</p>`;
    const domena = new URL(w.url).host;
    return `<div class="wezel ${w.tk.toLowerCase()} ${szyna(i)}" data-zrodlo="${esc(w.id)}" style="--i:${nr++}">
        <span class="format">${esc(znacznikFormatu(w))}</span>
        <div class="wezel-tresc">
          <button type="button" class="wezel-nazwa" aria-controls="podglad-${esc(w.id)}" title="Pokaż surowe dane">${esc(w.nazwa)}</button>
          <span class="wezel-co">${esc(w.krotko ?? w.co)}</span>
          <span class="wezel-liczby">${w.liczby.slice(0, 3).map(([e, v]) => `<span><b>${liczba(v)}</b> ${esc(e)}</span>`).join("")}</span>
        </div>
        <a class="wezel-url" href="${esc(w.url)}" target="_blank" rel="noopener" title="Strona źródła: ${esc(domena)}"
           aria-label="Strona źródła: ${esc(domena)}">↗</a>
      </div>`;
  }).join("");
}

// Prawa część schematu: etapy z przeplyw (bez pierwszego – źródła są narysowane obok); ostatni prowadzi do panelu P
function schematEtapowHtml(etapy) {
  return etapy.map(([etap, wartosc, opis, adres], i) => {
    const tresc = `<span class="e-wartosc">${esc(wartosc)}</span><span class="e-etykieta">${esc(etap)}</span><span class="e-opis">${esc(opis)}</span>`;
    return `<li class="etap" style="--i:${i}">${GROT}` +
      (adres ? `<a class="etap-pole etap-link" href="${esc(adres)}">${tresc}</a>` : `<div class="etap-pole">${tresc}</div>`) + "</li>";
  }).join("");
}

// Podgląd: zakładka (format + nazwa) i panel (opis, próbka surowych danych, pobrane pliki) dla każdego źródła
function podgladHtml(zrodla) {
  const zakladki = zrodla.map((z, i) => {
    const [kto, co] = czesciNazwy(z.nazwa);
    return `<button type="button" role="tab" class="zakladka ${z.tk.toLowerCase()}" id="zakladka-${esc(z.id)}" data-zrodlo="${esc(z.id)}"
      aria-controls="podglad-${esc(z.id)}" aria-selected="${i === 0}" tabindex="${i === 0 ? 0 : -1}">
      <span class="format">${esc(znacznikFormatu(z))}</span><span class="z-nazwa"><b>${esc(kto)}</b> <small>${esc(co)}</small></span></button>`;
  }).join("");
  const panele = zrodla.map((z, i) => `
    <div class="podglad-panel" role="tabpanel" id="podglad-${esc(z.id)}" aria-labelledby="zakladka-${esc(z.id)}" tabindex="0"${i ? " hidden" : ""}>
      <dl class="podglad-opis">
        <dt>Format</dt><dd>${esc(z.format)}</dd>
        <dt>Pobieramy</dt><dd>${esc(z.co)}</dd>
        <dt>Adres</dt><dd><a href="${esc(z.url)}" target="_blank" rel="noopener">${esc(z.url)}</a></dd>
      </dl>
      <div class="podglad-siatka">
        <div class="podglad-probka">
          <h3 class="podglad-naglowek">Surowe dane <span>${esc(z.probka.opis)}</span></h3>
          ${probkaHtml(z.probka)}
        </div>
        <div class="podglad-pliki">
          <h3 class="podglad-naglowek">Pobrane pliki <span>${z.pliki.length}</span></h3>
          ${z.pliki.length ? `<div class="tabela-wrap"><table class="pliki">
            <thead><tr><th scope="col">Plik</th><th scope="col">Rozmiar</th><th scope="col">Pobrano</th></tr></thead>
            <tbody>${z.pliki.map((f) => `<tr><td>${esc(f.nazwa)}</td><td>${rozmiarPliku(f.kB)}</td><td>${dataPobrania(f.pobrano)}</td></tr>`).join("")}</tbody>
          </table></div>` : '<p class="przypis">Brak pobranych plików.</p>'}
        </div>
      </div>
    </div>`).join("");
  return [zakladki, panele];
}

// Wybór źródła: zakładka, jej panel i podświetlony węzeł na schemacie
function wybierzZrodlo(id, { fokus = false, przewin = false } = {}) {
  document.querySelectorAll("#zakladki-zrodel [role=tab]").forEach((t) => {
    const wybrana = t.dataset.zrodlo === id;
    t.setAttribute("aria-selected", wybrana);
    t.tabIndex = wybrana ? 0 : -1;
    if (wybrana && fokus) t.focus();
  });
  document.querySelectorAll("#panele-zrodel [role=tabpanel]").forEach((p) => { p.hidden = p.id !== `podglad-${id}`; });
  document.querySelectorAll("#schemat-zrodla .wezel").forEach((w) => w.classList.toggle("wybrany", w.dataset.zrodlo === id));
  if (przewin) {
    const ruch = matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    $("#podglad").scrollIntoView({ behavior: ruch, block: "nearest" });
  }
}

function renderAkwizycja() {
  const A = DANE.akwizycja;
  const [zrodla, ...etapy] = A.przeplyw;  // pierwszy krok („3 źródła danych”) opisuje lewą kolumnę schematu
  $("#p-schemat").textContent = `${zrodla[1]} ${zrodla[0]}: ${zrodla[2]} · kliknij źródło, aby zobaczyć surowe dane`;
  $("#schemat-zrodla").innerHTML = schematZrodelHtml(A.zrodla);
  $("#schemat-etapy").innerHTML = schematEtapowHtml(etapy);
  [$("#zakladki-zrodel").innerHTML, $("#panele-zrodel").innerHTML] = podgladHtml(A.zrodla);

  // Kliknięcie węzła na schemacie przełącza zakładkę podglądu
  $("#schemat-zrodla").addEventListener("click", (e) => {
    const b = e.target.closest(".wezel-nazwa");
    if (b) wybierzZrodlo(b.closest(".wezel").dataset.zrodlo, { przewin: true });
  });
  $("#zakladki-zrodel").addEventListener("click", (e) => {
    const t = e.target.closest("[role=tab]");
    if (t) wybierzZrodlo(t.dataset.zrodlo);
  });
  // Klawiatura w zakładkach: strzałki (z zawijaniem), Home i End; Enter/spacja działają jak kliknięcie
  $("#zakladki-zrodel").addEventListener("keydown", (e) => {
    const zakladki = [...e.currentTarget.querySelectorAll("[role=tab]")];
    const i = zakladki.indexOf(document.activeElement);
    const cel = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: zakladki.length - 1 }[e.key];
    if (i < 0 || cel === undefined) return;
    e.preventDefault();
    wybierzZrodlo(zakladki[(cel + zakladki.length) % zakladki.length].dataset.zrodlo, { fokus: true });
  });
  wybierzZrodlo(A.zrodla[0].id);
}

// --- Panel P: przygotowanie danych --------------------------------------------------------
// „202 904” / „−15” → liczba (spacje tysięcy i minus typograficzny)
const liczbaZTekstu = (t) => Number(String(t).replace(/[\s ]/g, "").replace("−", "-").replace(",", "."));

// Lejek: wejście (godziny, z ubytkami kontroli jakości) → znacznik zmiany jednostki (godziny → doby)
// → lejek dób na jednej wspólnej skali: pełna siatka dni-miasto, kolejne ubytki („−X: powód”), wynik do analizy.
// tysiące zawsze ze spacją (pl-PL nie grupuje liczb 4-cyfrowych), jak „4 384” w danych
const tysiace = (n) => String(Math.round(n)).replace(/\B(?=(\d{3})+$)/g, " ");
function lejekHtml(P) {
  const [wejscie, ...reszta] = P.lejek;
  const ubytkiGodz = reszta.filter(([, w], i) => liczbaZTekstu(w) <= 0);
  const L = P.lejek_dni;
  const [siatka, opisSiatki] = L.siatka;
  const proc = (n) => `${(100 * n) / siatka}%`;
  let zostaje = siatka;
  const wiersze = L.ubytki.map(([powod, n, opis], i) => {
    const przed = zostaje; zostaje -= n;
    return `<li class="ld-wiersz ld-ubytek" style="--i:${i + 1}">
      <span class="ld-pasek" aria-hidden="true"><i style="width:${proc(zostaje)}"></i><i class="duch" style="left:${proc(zostaje)};width:${proc(przed - zostaje)}"></i></span>
      <span class="ld-tekst"><b class="ubytek">−${tysiace(n)}</b> ${esc(powod)} <small>${esc(opis)}</small></span></li>`;
  }).join("");
  const [wynik, opisWyniku] = L.wynik;
  return `<div class="lejek-wejscie">
      <span class="lp-jedn">wejście · godziny</span>
      <span class="lp-wartosc">${esc(wejscie[1])}</span>
      <span class="lp-etykieta">${esc(wejscie[0])}</span>
      <ul class="lw-ubytki">${ubytkiGodz.map(([etap, w]) =>
        `<li><b class="${/^−[1-9]/.test(w) ? "ubytek" : ""}">${esc(w)}</b> ${esc(etap)}</li>`).join("")}</ul>
    </div>
    <div class="lejek-zmiana">${GROT}<span><b>godziny → doby</b>średnia z min. ${P.min_godzin} z 24 h</span></div>
    <ol class="lejek-dni" aria-label="Dni-miasto: od pełnej siatki do danych do analizy">
      <li class="ld-wiersz ld-start" style="--i:0">
        <span class="ld-pasek" aria-hidden="true"><i style="width:100%"></i></span>
        <span class="ld-tekst"><b>${tysiace(siatka)}</b> dni-miasto <small>${esc(opisSiatki)}</small></span></li>
      ${wiersze}
      <li class="ld-wiersz ld-wynik" style="--i:${L.ubytki.length + 1}">
        <span class="ld-pasek" aria-hidden="true"><i style="width:${proc(wynik)}"></i></span>
        <span class="ld-tekst"><b>${tysiace(wynik)}</b> do analizy <small>${esc(opisWyniku)} · ${fmt((100 * wynik) / siatka, 0)}%</small></span></li>
    </ol>`;
}

function wybierzZakladkeP(zakladka, fokus = false) {
  document.querySelectorAll("#zakladki-p [role=tab]").forEach((t) => {
    const wybrana = t === zakladka;
    t.setAttribute("aria-selected", wybrana);
    t.tabIndex = wybrana ? 0 : -1;
    $(`#${t.getAttribute("aria-controls")}`).hidden = !wybrana;
  });
  if (fokus) zakladka.focus();
}

function renderPrzygotowanie() {
  const P = DANE.przygotowanie;
  const F = P.filtracja;
  const pierwszy = P.lejek[0], ostatni = P.lejek.at(-1);
  $("#p-lejek").textContent = `${pierwszy[1]} ${pierwszy[0]} → ${ostatni[1]} dni-miasto do analizy`;
  $("#lejek").innerHTML = lejekHtml(P);

  // Kroki: numer, nazwa, jedna linia, wynik
  $("#lista-krokow").innerHTML = P.kroki.map(([nazwa, co, wynik], i) =>
    `<li style="--i:${i}"><span class="kp-nr">${i + 1}</span><b class="kp-nazwa">${esc(nazwa)}</b>` +
    `<span class="kp-co">${esc(co)}</span><span class="kp-wynik">${esc(wynik)}</span></li>`).join("");

  $("#tabela-bazy").innerHTML =
    `<thead><tr><th scope="col">Tabela / widok</th><th scope="col">Typ</th><th scope="col">Wiersze</th></tr></thead><tbody>` +
    P.tabele.map((t) => `<tr><td><code>${esc(t.nazwa)}</code></td><td>${t.typ === "view" ? "widok" : "tabela"}</td>` +
      `<td>${fmtInt(t.wiersze)}</td></tr>`).join("") + "</tbody>";

  // Kontrola jakości: walidacja zakresu i błędy pomiarowe (usunięte / oznaczone, ale zostawione)
  const K = P.kontrola;
  const nazwaWsk = (w) => (w === "PM25" ? "PM2.5" : w);
  const prog = fmtInt(K.prog), x = fmtInt(K.krotnosc);
  $("#reguly-kontrola").innerHTML =
    `Powyżej ${prog} µg/m³: a) ${x}× więcej niż inne stacje, b) ${x}× więcej niż godzinę przed i po. ` +
    `<b>Oba = błąd, usuwamy.</b> Jeden = zostaje.`;
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
  $("#przyklady-kontrola").innerHTML = (K.przyklady ?? []).map((p) =>
    `<p class="przypis"><b>${esc(p.miasto)}, ${nazwaWsk(p.wskaznik)}, ${kiedy(p.czas)}:</b> ${fmt(p.wartosc)} µg/m³, ` +
    `inne stacje ${fmt(p.mediana_innych)}, godzinę przed ${fmt(p.przed)}, godzinę po ${fmt(p.po)} → ${esc(powod[p.decyzja] ?? p.decyzja)}</p>`).join("");
  const suma = (k) => K.stacje.reduce((a, s) => a + s[k], 0);
  const niemozliwe = suma("ujemne") + K.stacje.filter((s) => s.wskaznik === "PM25").reduce((a, s) => a + s.pm25_ponad_pm10, 0);
  $("#przypis-kontrola").textContent =
    `Usunięto ${fmtInt(suma("usuniete"))}, oznaczono ${fmtInt(suma("odstaje_od_innych") + suma("skok_w_czasie"))}. ` +
    `Wartości niemożliwych (< 0, PM2.5 > PM10): ${fmtInt(niemozliwe)}.`;

  // Filtracja: tabela braków (szczegóły) i wykres powodów (PM10, PM2.5, pogoda – najgorsza zmienna pogodowa)
  const ZMIENNE_F = [["pm10", "PM10"], ["pm25", "PM2.5"], ["temp_sr", "temperatura"], ["opad", "opad"],
                     ["wiatr_sr", "wiatr"], ["wilgotnosc", "wilgotność"], ["cisnienie", "ciśnienie"]];
  const brak = (m, z) => F.braki.find((b) => b.miasto === m && b.zmienna === z)?.dni ?? 0;
  $("#tabela-filtracja").innerHTML =
    `<thead><tr><th scope="col">Dni z brakiem</th>${ZMIENNE_F.map(([, n]) => `<th scope="col">${n}</th>`).join("")}</tr></thead><tbody>` +
    listaMiast().map((m) => `<tr><td>${esc(m)}</td>${ZMIENNE_F.map(([z]) => {
      const n = brak(m, z);
      return n ? `<td class="brak-dni">${fmtInt(n)}</td>` : '<td class="brak">–</td>';
    }).join("")}</tr>`).join("") + "</tbody>";

  $("#p-filtracja").textContent = `dzień zostaje, gdy wszystkie ${listaMiast().length} miasta mają PM i pogodę · dni z brakiem`;
  const POWODY = [["pm10", "PM10"], ["pm25", "PM2.5"], ["pogoda", "pogoda"]];
  const ilePowod = (m, z) => (z === "pogoda" ? Math.max(0, ...ZMIENNE_F.slice(2).map(([k]) => brak(m, k))) : brak(m, z));
  const maxB = Math.max(1, ...listaMiast().flatMap((m) => POWODY.map(([z]) => ilePowod(m, z))));
  $("#wykres-brakow").innerHTML =
    `<p class="braki-legenda" aria-hidden="true">${POWODY.map(([z, n]) => `<span><i class="b-${z}"></i>${n}</span>`).join("")}</p>` +
    listaMiast().map((m) => `<div class="braki-miasto"><span class="bm-nazwa">${esc(m)}</span><div class="bm-paski">` +
      POWODY.map(([z, n]) => { const v = ilePowod(m, z);
        return `<span class="bm-wiersz" title="${n}: ${v} dni"><i class="b-${z}" style="width:${(100 * v) / maxB}%"></i><small>${v || "–"}</small></span>`; }).join("") +
      "</div></div>").join("");
  $("#wykres-brakow").setAttribute("aria-label", "Dni z brakiem danych: " + listaMiast().map((m) =>
    `${m} – ${POWODY.map(([z, n]) => `${n} ${ilePowod(m, z)}`).join(", ")}`).join("; "));
  let najw = ["", "", -1];
  listaMiast().forEach((m) => POWODY.forEach(([z, n]) => { const v = ilePowod(m, z); if (v > najw[2]) najw = [m, n, v]; }));
  $("#przypis-filtracja").innerHTML =
    `Odpadło <b>${fmtInt(F.dni_kalendarzowe - F.dni_kompletne)}</b> z ${fmtInt(F.dni_kalendarzowe)} dni, zostało ` +
    `<b>${fmtInt(F.dni_kompletne)}</b> (${fmt(100 * F.dni_kompletne / F.dni_kalendarzowe, 0)}%). ` +
    `Najwięcej braków: ${esc(najw[1])}, ${esc(najw[0])} (${fmtInt(najw[2])} dni).`;

  // Zakładki szczegółów: kliknięcie, strzałki (z zawijaniem), Home i End
  const lista = $("#zakladki-p");
  lista.addEventListener("click", (e) => { const t = e.target.closest("[role=tab]"); if (t) wybierzZakladkeP(t); });
  lista.addEventListener("keydown", (e) => {
    const zakladki = [...lista.querySelectorAll("[role=tab]")];
    const i = zakladki.indexOf(document.activeElement);
    const cel = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: zakladki.length - 1 }[e.key];
    if (i < 0 || cel === undefined) return;
    e.preventDefault();
    wybierzZakladkeP(zakladki[(cel + zakladki.length) % zakladki.length], true);
  });
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
  $("#t-doba").textContent = `Kontrola jakości: ${d[0].miasto}, ${wsk}, ${data}`;
  $("#p-doba").textContent = "słupek = godzina · czerwony = usunięty błąd · linie = średnia doby";
  const stala = (v) => d.map(() => v);
  nowyWykres("wykres-doba", {
    type: "bar",
    data: {
      labels: d.map((x) => String(x.godzina)),
      datasets: [
        { type: "bar", label: "pomiar po kontroli", data: d.map((x) => x.po), backgroundColor: css("--accent"), grouped: false, order: 2 },
        { type: "bar", label: "usunięty (błąd)", data: d.map((x) => (x.przed != null && x.po == null ? x.przed : null)),
          backgroundColor: css("--class-zla"), grouped: false, order: 2 },
        { type: "line", label: `średnia przed kontrolą: ${fmt(srPrzed)}`, data: stala(srPrzed), borderColor: css("--text-secondary"),
          borderDash: [6, 4], borderWidth: 2, pointRadius: 0, order: 1 },
        { type: "line", label: `średnia po kontroli: ${fmt(srPo)}`, data: stala(srPo), borderColor: css("--text-primary"),
          borderWidth: 3, pointRadius: 0, order: 1 },
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
    `Usunięto ${godz}. Średnia: ${fmt(srPrzed)} → ${fmt(srPo)} µg/m³. ` +
    `Zostało ${nPo}/24 h – doba ${nPo >= P.min_godzin ? "zostaje" : "odpada"} (minimum ${P.min_godzin}).`;
}

// --- Panel E: eksploracja danych (EDA) i testy -----------------------------------------------
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
  $("#przypis-opisowe").textContent = `Pył: ${fmtInt(pm10.n)} dni, pogoda: ${fmtInt(temp.n)} dni. Rozkład normalny: skośność 0, kurtoza 0.`;

  const li = (t) => `<li>${t}</li>`;
  $("#wnioski-eda").innerHTML = [
    `<b>Pył jest skośny</b> (mediana ${fmt(pm10.mediana)} < średnia ${fmt(pm10.srednia)}) → korelacja Spearmana zamiast Pearsona i mediana obok średniej.`,
    `<b>Opad:</b> ${fmt(opad.udzial_zer_pct, 0)}% dni bez deszczu → logarytm w klasteryzacji.`,
    `<b>Różne skale</b> (wariancja PM10 ${fmt(pm10.wariancja)}, wiatru ${fmt(wiatr.wariancja)}) → standaryzacja przed klasteryzacją.`,
    `<b>Ciśnienie</b> zależy od wysokości stacji → liczymy różnicę od średniej stacji.`,
    `<b>Temperatura</b> ma dwa szczyty (zima, lato) → wyniki osobno dla sezonów.`,
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
  $("#przypis-3sigma").textContent =
    `Poza 3σ PM10 jest ${fmtInt(T.dni)} dni – ${T.w_tym_zle === T.dni ? "wszystkie" : fmtInt(T.w_tym_zle)} ze smogiem. ` +
    `Dlatego nie usuwamy danych regułą 3σ.`;

  $("#wybor-zmienna").innerHTML = ZMIENNE.map(([z, skrot]) =>
    `<button type="button" role="radio" data-wartosc="${z}" aria-checked="${z === STAN.zmiennaEda}" title="${z}">${skrot}</button>`).join("");
}

// Testy istotności dla pytań 1–3 (wiersze bez wskaźnika – korelacje – widoczne zawsze)
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
      const efekt = t.efekt_nazwa.includes("pary") ? fmtInt(t.efekt) : t.efekt_nazwa.startsWith("r ") ? fmtR(t.efekt)
        : fmt(t.efekt, t.efekt_nazwa.startsWith("różnica") ? 1 : 2);
      const wniosek = t.wniosek === "odrzucamy H0" ? "<b>odrzucamy H0</b>" : t.wniosek === "zob. uwagi" ? "" : esc(t.wniosek);
      return `<tr><td>${t.pytanie}</td><td>${esc(t.test)}</td><td>${esc(t.h0)}</td><td>${esc(t.dane)}</td>` +
        `<td>${esc(t.statystyka)}</td><td class="${t.p != null && t.p >= 0.05 ? "odbiega" : ""}">${fmtP(t.p)}</td>` +
        `<td>${esc(t.efekt_nazwa)}: ${efekt}</td>` +
        `<td>${wniosek}${t.uwagi ? `<div class="jedn">${esc(t.uwagi)}</div>` : ""}</td></tr>`;
    }).join("") + "</tbody>";
  const ak = E.autokorelacja.filter((a) => a.wskaznik === STAN.wskaznik).map((a) => a.r_lag1);
  $("#przypis-testy").textContent =
    `p < 0,05 = raczej nie przypadek. Kolejne dni są podobne (autokorelacja ${fmt(Math.min(...ak), 2)}–${fmt(Math.max(...ak), 2)}), ` +
    `dlatego testy powtórzono na średnich miesięcznych.`;
}

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
  $("#przypis-histogram").innerHTML =
    `<span class="legenda-linia srednia"></span> średnia ${fmt(s.srednia)} · <span class="legenda-linia mediana"></span> mediana ` +
    `${fmt(s.mediana)} · dominanta ${fmt(s.dominanta_od, d)}–${fmt(s.dominanta_do, d)} ${esc(s.jednostka)}` +
    (pominiete ? ` · bez ${fmtInt(pominiete)} najwyższych wartości` : "");
}

// --- Panel 1: sezony -------------------------------------------------------------------------
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

// --- Panel 2: pogoda -------------------------------------------------------------------------
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

  // Najsilniejszy związek wybranego wskaźnika z pogodą
  const pogoda = ZMIENNE.slice(2).map(([z]) => z);
  const zPylem = pogoda.map((z) => ({ opis: z, r: r(STAN.wskaznik, z) }))
    .reduce((x, y) => (Math.abs(y.r) > Math.abs(x.r) ? y : x));
  $("#przypis-korelacji").textContent =
    `${STAN.wskaznik} najmocniej wiąże się z: ${zPylem.opis} (r = ${fmtR(zPylem.r)}). Przekreślone = nieistotne.`;
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

// --- Panel 3: ranking i mapa -----------------------------------------------------------------
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
    ["Miasto", (w) => `<span class="miasto"><i class="kropka" style="background:${kolorMiasta(w.miasto)}"></i>${w.miasto}</span>`],
    ["PM10 śr.", (w) => fmt(w.pm10_srednia)],
    ["PM2.5 śr.", (w) => (w.pm25_srednia == null ? '<span class="brak">brak</span>' : fmt(w.pm25_srednia))],
    ["Dni &gt;50 / rok", (w) => fmt(w.dni_powyzej_50_rocznie)],
    ["Dni złe %", (w) => fmt(w.dni_zla_pct)],
  ];
  $("#tabela-ranking").innerHTML =
    `<thead><tr>${kolumny.map(([n]) => `<th scope="col">${n}</th>`).join("")}</tr></thead><tbody>` +
    DANE.ranking.ranking.map((w) => `<tr>${kolumny.map(([, f]) => `<td>${f(w)}</td>`).join("")}</tr>`).join("") + "</tbody>";

  // Test Friedmana na średnich miesięcznych (ostrożniejszy niż na dniach) – czy kolejność miast nie jest przypadkiem
  const t = DANE.eksploracja.testy.find((x) => x.pytanie === 3 && x.wskaznik === "PM10" && x.test.includes("miesięczne"));
  $("#przypis-ranking").innerHTML = t == null ? "" :
    `Różnice między miastami ${t.p < 0.05 ? "są" : "nie są"} istotne (test Friedmana na średnich miesięcznych, p ${t.p < 0.001 ? "" : "= "}${fmtP(t.p)}).`;
}

// Ostatnie 24 h z API GIOŚ na tle dni tego samego miesiąca z lat 2023–2025 (kreska = jeden dzień)
const MIESIACE_DOPELNIACZ = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia",
  "września", "października", "listopada", "grudnia"];

function renderTeraz() {
  const T = DANE.stacje.teraz;
  $("#karta-teraz").hidden = !T;
  if (!T) return;
  const pole = POLE[STAN.wskaznik];
  const wiersze = listaMiast().map((m) => T.wiersze.find((w) => w.miasto === m && w.wskaznik === pole)).filter(Boolean);
  const miesiac = MIESIACE_DOPELNIACZ[T.miesiac - 1];
  const lata = `${DANE.meta.zakres.od.slice(0, 4)}–${DANE.meta.zakres.do.slice(0, 4)}`;
  // Wspólna skala dla wszystkich miast, żeby paski dało się porównać
  const najwyzej = Math.max(...wiersze.flatMap((w) => [w.srednia_24h, ...T.historia[w.miasto][pole]]));
  const krok = najwyzej <= 50 ? 10 : 25;
  const max = Math.ceil(najwyzej / krok) * krok;
  const x = (v) => `${(100 * v) / max}%`;
  const opis = (w) => `${w.miasto}: ostatnie 24 h ${fmt(w.srednia_24h)} µg/m³, mediana dni ${miesiac} ${fmt(w.mediana_historia)} µg/m³`;

  $("#t-teraz").textContent = `${STAN.wskaznik} teraz na tle lat ${lata}`;
  $("#p-teraz").textContent = `średnia z ostatnich 24 h z API GIOŚ (do ${dataPobrania(wiersze[0].do)}) · µg/m³`;
  $("#teraz").innerHTML = wiersze.map((w) => `<div class="t-wiersz">
      <span class="miasto"><i class="kropka" style="background:${kolorMiasta(w.miasto)}"></i>${w.miasto}</span>
      <span class="t-wartosc">${fmt(w.srednia_24h)}<small>µg/m³</small>${w.klasa ? znacznikKlasy(w.klasa) : ""}</span>
      <div class="t-pasek" role="img" aria-label="${esc(opis(w))}">
        ${T.historia[w.miasto][pole].map((v) => `<i class="t-dzien" style="left:${x(v)}"></i>`).join("")}
        <i class="t-teraz" style="left:${x(w.srednia_24h)};background:${kolorMiasta(w.miasto)}" title="${esc(opis(w))}"></i>
      </div>
      <span class="t-opis">więcej niż w <strong>${fmtInt(w.nizej_pct)}%</strong> dni ${miesiac}</span>
    </div>`).join("") +
    `<div class="t-wiersz t-os" aria-hidden="true"><span></span><span></span><div class="t-skala">${
      Array.from({ length: max / krok + 1 }, (_, i) => `<span style="left:${x(i * krok)}">${i * krok}</span>`).join("")
    }</div><span></span></div>`;
  $("#przypis-teraz").textContent = `Kreska = średnia jednego dnia ${miesiac} ${lata} (${wiersze[0].dni_historia} dni po filtracji), ` +
    "kółko = ostatnie 24 h. Ostatnie 24 h to nie doba kalendarzowa, więc porównanie jest orientacyjne.";
}

// --- Panel 4: klasy jakości dnia --------------------------------------------------------
const MIESIACE_PELNE = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień",
  "październik", "listopad", "grudzień"];

function renderKlasy() {
  const K = DANE.klasy.klasy;
  const [dobra, , zla] = DANE.meta.progi_pm10.klasy;
  const etykiety = DANE.meta.progi_pm10.klasy;
  const wiersz = (podzial, grupa, klasa) => K.find((r) => r.podzial === podzial && r.grupa === grupa && r.klasa === klasa);
  const najwiecej = (wiersze) => wiersze.reduce((a, b) => (b.udzial_pct > a.udzial_pct ? b : a));
  const miesiace = MIESIACE.map((_, i) => String(i + 1));
  const wszystkie = (klasa) => wiersz("wszystkie", "wszystkie dni", klasa);
  const dniRazem = etykiety.reduce((a, k) => a + wszystkie(k).dni, 0);
  const najgorszeMiasto = najwiecej(listaMiast().map((m) => wiersz("miasto", m, zla)));
  const najgorszyMiesiac = najwiecej(miesiace.map((m) => wiersz("miesiac", m, zla)));

  $("#kafle-klasy").innerHTML = `
    <div class="kafel wyrozniony"><div class="k-etykieta">Dni złe</div>
      <div class="k-wartosc">${fmt(wszystkie(zla).udzial_pct)}<small>%</small></div>
      <div class="k-opis">${fmtInt(wszystkie(zla).dni)} z ${fmtInt(dniRazem)} dni</div></div>
    <div class="kafel"><div class="k-etykieta">Dni dobre</div>
      <div class="k-wartosc">${fmt(wszystkie(dobra).udzial_pct)}<small>%</small></div>
      <div class="k-opis">${fmtInt(wszystkie(dobra).dni)} dni</div></div>
    <div class="kafel"><div class="k-etykieta">Najwięcej dni złych</div>
      <div class="k-wartosc">${esc(najgorszeMiasto.grupa)}</div>
      <div class="k-opis">${fmt(najgorszeMiasto.udzial_pct)}% dni</div></div>
    <div class="kafel"><div class="k-etykieta">Najgorszy miesiąc</div>
      <div class="k-wartosc">${MIESIACE_PELNE[Number(najgorszyMiesiac.grupa) - 1]}</div>
      <div class="k-opis">${fmt(najgorszyMiesiac.udzial_pct)}% dni złych</div></div>`;

  // Udział klas w miesiącach – słupki do 100%
  nowyWykres("wykres-klasy", {
    type: "bar",
    data: {
      labels: MIESIACE,
      datasets: etykiety.map((k) => ({
        label: k, data: miesiace.map((m) => wiersz("miesiac", m, k).udzial_pct),
        backgroundColor: css(KOLOR_KLASY[k]), borderColor: css("--surface-1"), borderWidth: 1,
      })),
    },
    options: {
      plugins: { tooltip: { callbacks: { label: (c) =>
        `${c.dataset.label}: ${fmt(c.raw)}% (${fmtInt(wiersz("miesiac", miesiace[c.dataIndex], c.dataset.label).dni)} dni)` } } },
      scales: { x: { stacked: true, grid: { display: false } },
                y: { stacked: true, max: 100, title: { display: true, text: "% dni" } } },
    },
  });

  // Tabela: miasta, sezony i wszystkie dni
  const grupy = [...listaMiast().map((m) => ["miasto", m, m]),
    ["sezon", "grzewczy", "sezon grzewczy"], ["sezon", "letni", "sezon letni"], ["wszystkie", "wszystkie dni", "wszystkie dni"]];
  $("#tabela-klasy").innerHTML =
    `<thead><tr><th scope="col"></th>${etykiety.map((k) => `<th scope="col">${znacznikKlasy(k)}</th>`).join("")}</tr></thead><tbody>` +
    grupy.map(([podzial, grupa, nazwa]) => `<tr class="${podzial === "wszystkie" ? "suma" : ""}"><td>${esc(nazwa)}</td>` +
      etykiety.map((k) => { const w = wiersz(podzial, grupa, k); return `<td>${fmtInt(w.dni)} (${fmt(w.udzial_pct)}%)</td>`; }).join("") +
      "</tr>").join("") + "</tbody>";
}

// --- Panel 5: typy dni pogodowych (klasteryzacja) --------------------------------------------
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

  $("#opis-klastry").textContent = `${fmtInt(K.metoda.n)} dni podzielonych na typy według samej pogody (k-średnich).`;

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

// --- Pasek boczny i dane wspólne -------------------------------------------------------------
// Progi klas (panel 4), data wygenerowania i zakres lat (pasek boczny)
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
  renderRanking(); renderTeraz(); renderKlasy(); renderKlastry(); renderDoba(); renderHistogram();
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
  podepnijPrzelacznik("#wybor-wskaznik", "wskaznik", () => {
    renderKafle(); renderSezonowy(); renderMiesieczny(); renderKorelacje(); renderTeraz(); renderKlastry(); renderTesty();
  });
  podepnijPrzelacznik("#wybor-zakres", "zakres", renderKorelacje);
  podepnijPrzelacznik("#wybor-os", "os", renderRozrzut);
  podepnijPrzelacznik("#wybor-zmienna", "zmiennaEda", renderHistogram);
  // Zmiana motywu systemu → wykresy w nowych kolorach
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderWszystko);
  renderWszystko();
  // Linki menu zmieniają adres (#panel); przyciski Wstecz/Dalej też przełączają panele
  window.addEventListener("hashchange", () => pokazPanel(panelZAdresu()));
  pokazPanel(panelZAdresu());
})();
