// Le icone di AIOS: tratto unico, angoli tondi, disegnate qui (niente emoji, niente icone di Linux).
"use strict";
const ICONE = {
  file: "M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z",
  foto: "M5 4.5h14A1.5 1.5 0 0 1 20.5 6v12a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18V6A1.5 1.5 0 0 1 5 4.5z M3.5 16l5-5 4 4 3-3 5 5 M15.5 8.2a1.3 1.3 0 1 0 0 2.6 1.3 1.3 0 0 0 0-2.6z",
  musica: "M9 18V5.5l11-2V16 M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0z M20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
  video: "M4.5 6h10A1.5 1.5 0 0 1 16 7.5v9a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 3 16.5v-9A1.5 1.5 0 0 1 4.5 6z M16 10.5l5-3v9l-5-3",
  note: "M6.5 3h8.5l4 4v12.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19.5v-15A1.5 1.5 0 0 1 6.5 3z M15 3v4h4 M8.5 12h7 M8.5 16h5",
  impostazioni: "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z M12 2.5v2.5 M12 19v2.5 M4.6 4.6l1.8 1.8 M17.6 17.6l1.8 1.8 M2.5 12H5 M19 12h2.5 M4.6 19.4l1.8-1.8 M17.6 6.4l1.8-1.8",
  internet: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M3.5 9h17 M3.5 15h17 M12 3c2.8 3.2 2.8 14.8 0 18 M12 3c-2.8 3.2-2.8 14.8 0 18",
  wifi: "M2.5 9a14 14 0 0 1 19 0 M5.5 12.5a9.5 9.5 0 0 1 13 0 M8.8 16a5 5 0 0 1 6.4 0 M12 19.5h.01",
  bluetooth: "M7 7.5l10 9-5 4.5V3l5 4.5-10 9",
  suono: "M4 9.5h3.5L13 5v14l-5.5-4.5H4z M16.5 9a4.2 4.2 0 0 1 0 6 M19.3 6.2a8 8 0 0 1 0 11.6",
  voce: "M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3z M5.5 11.5a6.5 6.5 0 0 0 13 0 M12 18v3",
  tastiera: "M4 6.5h16A1.5 1.5 0 0 1 21.5 8v8a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 1 2.5 16V8A1.5 1.5 0 0 1 4 6.5z M6 10h.01 M9 10h.01 M12 10h.01 M15 10h.01 M18 10h.01 M7.5 14h9",
  aspetto: "M4 19L9.5 5h1L16 19 M6 14h8 M17 19l2.2-6h.6l2.2 6 M17.8 17h3.4",
  aggiornamenti: "M12 3.5v11 M7.5 10l4.5 4.5 4.5-4.5 M4.5 19.5h15",
  account: "M15 4.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9z M11.8 12.2L4 20 M7 17l2.2 2.2 M5 19l1.6 1.6",
  privacy: "M6.5 10.5h11A1.5 1.5 0 0 1 19 12v7.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19.5V12a1.5 1.5 0 0 1 1.5-1.5z M8 10.5V7.5a4 4 0 0 1 8 0v3 M12 14.5v2.5",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 11v5.5 M12 7.6h.01",
  energia: "M12 3v8.5 M6.5 6.8a7.8 7.8 0 1 0 11 0",
  posta: "M4.5 5.5h15A1.5 1.5 0 0 1 21 7v10a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17V7a1.5 1.5 0 0 1 1.5-1.5z M3.5 7l8.5 6 8.5-6",
  telefono: "M8 2.5h8A1.5 1.5 0 0 1 17.5 4v16a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 20V4A1.5 1.5 0 0 1 8 2.5z M11 18.5h2",
  internet_cavo: "M8 3v4 M16 3v4 M6 7h12v4a6 6 0 0 1-12 0z M12 17v4",
  batteria: "M4.5 7.5h13A1.5 1.5 0 0 1 19 9v6a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 3 15V9a1.5 1.5 0 0 1 1.5-1.5z M21 10.5v3",
  carica: "M13 2.5L5.5 13.5H12l-1 8 7.5-11H12z",
  avviso: "M12 4l9 16H3z M12 10v4.5 M12 17.5h.01",
  documento: "M6.5 3h8.5l4 4v13.5a.5.5 0 0 1-.5.5h-12a.5.5 0 0 1-.5-.5v-17a.5.5 0 0 1 .5-.5z M15 3v4h4",
  pacco: "M12 3l8 4.5v9L12 21l-8-4.5v-9z M4 7.5l8 4.5 8-4.5 M12 12v9",
  indietro: "M14.5 5.5L8 12l6.5 6.5",
  chiudi: "M6.5 6.5l11 11 M17.5 6.5l-11 11",
  cerca: "M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z M15.5 15.5L20 20",
};
// i colori delle «piastrelle» delle app di AIOS nel dock
const TINTE = { file: ["#3BA3D6", "#1867A6"], foto: ["#F2A65A", "#D9534F"], musica: ["#E36397", "#8E44AD"],
                video: ["#7B6CF6", "#3D3BB7"], note: ["#F4C95D", "#E09F3E"], impostazioni: ["#7D8A96", "#45525E"],
                internet: ["#2EC4B6", "#0B6E99"] };

function svgIcona(nome, cls = "icona") {
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", cls); s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", ICONE[nome] || ICONE.info);
  s.append(p);
  return s;
}

function piastrella(nome) {
  const [a, b] = TINTE[nome] || TINTE.impostazioni;
  const t = document.createElement("span");
  t.className = "piastrella";
  t.style.background = `linear-gradient(145deg, ${a}, ${b})`;
  t.append(svgIcona(nome));
  return t;
}

// testo preceduto da un'icona (barra, elenchi)
function conIcona(nome, testo, cls = "con-icona") {
  const s = document.createElement("span"); s.className = cls;
  s.append(svgIcona(nome), document.createTextNode(testo));
  return s;
}
