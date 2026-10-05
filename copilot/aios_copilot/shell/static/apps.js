"use strict";
// Le app di AIOS dentro la shell (File, Foto, Musica, Video, Note, documenti, Impostazioni).
// Usa api(), el(), $(), TOKEN e chiedi() di home.html.

const ICONA_PASSO = { internet: "wifi", posta: "posta", telefono: "telefono", aggiornamenti: "aggiornamenti" };
const SIMBOLI = { cartella: "file", immagine: "foto", audio: "musica", video: "video", testo: "note", documento: "documento", altro: "pacco" };
const fileUrl = p => `/file/${encodeURIComponent(p)}?t=${encodeURIComponent(TOKEN)}`;
let vistaAttuale = null;
const audio = new Audio();

let vistaArgomenti = [], vistaGiro = 0;
function apriVista(nome, ...args) {
  const v = VISTE[nome];
  if (!v) return;
  if (nome !== "musica" && vistaAttuale === "musica" && !audio.paused) { /* la musica continua in sottofondo */ }
  document.body.classList.add("in-vista");
  $("tutte").hidden = true;
  const box = $("vista"); box.hidden = false; box.replaceChildren();
  vistaAttuale = nome; vistaArgomenti = args; vistaGiro++;
  api("/api/casa-vai", { chiudi_viste: false }).catch(() => {});  // i programmi a finestra si fanno da parte
  v(box, ...args);
}

// Le schermate con uno stato che cambia da solo (un aggiornamento che scarica, le reti Wi-Fi, il volume…) si
// ridisegnano ogni pochi secondi: fuori pagina, poi al posto di quella vecchia (niente sfarfallio), tenendo il
// punto dove eri arrivato. Mai mentre stai scrivendo o scegliendo qualcosa.
const VISTE_VIVE = { impostazioni: { aggiornamenti: 3000, info: 10000, wifi: 8000, bluetooth: 6000, suono: 8000 } };
function ogniQuanto() {
  const regole = VISTE_VIVE[vistaAttuale];
  if (!regole) return 0;
  return regole[vistaArgomenti[0] || "wifi"] || 0;
}
let ultimoTocco = 0;  // dopo un clic si lascia il tempo di leggere l'esito («Fatto», «Collegato»…)
document.addEventListener("pointerdown", e => { if (e.target.closest && e.target.closest("#vista")) ultimoTocco = Date.now(); }, true);
function occupato() {
  if (Date.now() - ultimoTocco < 8000) return true;
  const a = document.activeElement;
  if (a && (a.matches("input, textarea, select, [contenteditable]") || a.closest(".menu"))) return true;
  return !!document.querySelector(".menu, #lampada") || [...document.body.children].some(c => c.style && c.style.position === "fixed" && c.style.zIndex === "50");
}
async function rinfrescaVista() {
  if (!vistaAttuale || document.hidden || occupato()) return;
  const nome = vistaAttuale, giro = vistaGiro, args = vistaArgomenti;
  const box = $("vista"), nuovo = el("div");
  try { await VISTE[nome](nuovo, ...args); } catch { return; }
  if (nome !== vistaAttuale || giro !== vistaGiro || occupato()) return;  // nel frattempo hai cambiato pagina o stai scrivendo
  const scorre = [...box.querySelectorAll(".corpo-vista, .pannello-imp, .sezioni")].map(e => e.scrollTop);
  box.replaceChildren(...nuovo.childNodes);
  box.querySelectorAll(".corpo-vista, .pannello-imp, .sezioni").forEach((e, i) => { e.scrollTop = scorre[i] || 0; });
}
let ultimoRinfresco = 0;
setInterval(() => {
  const ogni = ogniQuanto();
  if (ogni && Date.now() - ultimoRinfresco >= ogni) { ultimoRinfresco = Date.now(); rinfrescaVista(); }
}, 1000);
function chiudiVista() {
  if (!vistaAttuale) return;
  document.body.classList.remove("in-vista");
  $("vista").hidden = true; $("vista").replaceChildren();
  vistaAttuale = null;
  $("testo") && $("testo").focus();
}
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && $("lampada")) { $("lampada").remove(); return; }
  if (e.key === "Escape" && vistaAttuale && !document.querySelector(".menu")) chiudiVista();
});
document.addEventListener("click", e => { const m = document.querySelector(".menu"); if (m && !m.contains(e.target)) m.remove(); });

function testa(box, titolo, extra) {
  const t = el("div", "testa-vista");
  const back = el("button", "torna"); back.append(svgIcona("indietro")); back.title = "Torna alla schermata (Esc)"; back.onclick = chiudiVista;
  const h = el("h2"); h.append(typeof titolo === "string" ? titolo.replace(/^[^\p{L}\p{N}«"]+\s*/u, "") : titolo);
  t.append(back, h);
  if (extra) for (const x of [].concat(extra)) t.append(x);
  box.append(t);
  const corpo = el("div", "corpo-vista"); box.append(corpo);
  return corpo;
}
function bottone(testo, fn, cls = "bottone") { const b = el("button", cls, testo); b.onclick = fn; return b; }
function menu(x, y, voci) {
  document.querySelector(".menu")?.remove();
  const m = el("div", "menu");
  for (const [t, fn] of voci) m.append(bottone(t, () => { m.remove(); fn(); }, ""));
  m.style.left = Math.min(x, innerWidth - 230) + "px"; m.style.top = Math.min(y, innerHeight - 50 * voci.length) + "px";
  document.body.append(m);
}


// Domande dentro la pagina (mai finestre del sistema): testo, password, conferma, avviso.
function dialogo(titolo, { valore = "", password = false, campo = true, si = "OK", no = "Annulla" } = {}) {
  return new Promise(resolve => {
    const fondo = el("div"); fondo.style.cssText = "position:fixed;inset:0;z-index:50;background:rgba(3,12,18,.55);display:grid;place-items:center";
    const c = el("div", "carta"); c.style.cssText = "width:min(460px,90vw);display:flex;flex-direction:column;gap:14px";
    c.append(el("h3", "", titolo));
    const input = el("input", "campo"); input.type = password ? "password" : "text"; input.value = valore;
    if (campo) c.append(input);
    const fine = v => { fondo.remove(); resolve(v); };
    const a = el("div", "avanti"); a.style.cssText = "display:flex;gap:10px;justify-content:flex-end";
    if (no) a.append(bottone(no, () => fine(null)));
    a.append(bottone(si, () => fine(campo ? input.value : true), "bottone primo"));
    c.append(a); fondo.append(c); document.body.append(fondo);
    fondo.onkeydown = e => { e.stopPropagation(); if (e.key === "Escape") fine(null); if (e.key === "Enter") fine(campo ? input.value : true); };
    setTimeout(() => (campo ? input : a.lastChild).focus(), 30);
  });
}
const chiediTesto = (t, v = "") => dialogo(t, { valore: v });
const chiediPassword = t => dialogo(t, { password: true });
const chiediConferma = t => dialogo(t, { campo: false, si: "Sì", no: "No" });
const avviso = t => dialogo(t, { campo: false, no: null });

// --- aprire un file con la vista giusta ---------------------------------------------------------------
function apriFile(f) {
  f = { nome: f.percorso.split("/").pop(), ...f };
  const tipo = f.tipo || tipoDa(f.percorso);
  if (tipo === "cartella") return apriVista("file", f.percorso);
  if (tipo === "immagine") return lampada([f], 0);
  if (tipo === "audio") return apriVista("musica", f.percorso);
  if (tipo === "video") return guardaVideo(f);
  if (tipo === "testo" && /\.(txt|md)$/i.test(f.percorso)) return apriVista("note", f.percorso);
  if (tipo === "documento" || tipo === "testo") return apriVista("documento", f);
  api("/api/file/apri-con", { p: f.percorso }).catch(e => nova(e.message));
}
function tipoDa(p) {
  const ext = (p.match(/\.[^./]+$/) || [""])[0].toLowerCase();
  if ([".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".avif"].includes(ext)) return "immagine";
  if ([".mp3", ".ogg", ".opus", ".flac", ".wav", ".m4a", ".aac"].includes(ext)) return "audio";
  if ([".mp4", ".webm", ".mkv", ".mov", ".m4v", ".ogv"].includes(ext)) return "video";
  if ([".txt", ".md", ".csv", ".log", ".json"].includes(ext)) return "testo";
  return "documento";
}
window.apriFile = apriFile;
window.apriVista = apriVista;

function lampada(foto, i) {
  document.getElementById("lampada")?.remove();
  const box = el("div"); box.id = "lampada";
  const img = el("img"); const did = el("div", "didascalia");
  const mostra = () => { img.src = fileUrl(foto[i].percorso); did.textContent = `${foto[i].nome} · ${i + 1} di ${foto.length}`; };
  const chiudi = bottone("✕", () => box.remove(), "chiudi");
  const prec = bottone("‹", () => { i = (i - 1 + foto.length) % foto.length; mostra(); }, "prec");
  const succ = bottone("›", () => { i = (i + 1) % foto.length; mostra(); }, "succ");
  const modifica = bottone("✏️ Modifica", () => { box.remove(); apriVista("modifica", foto[i].percorso); }, "modifica");
  modifica.title = "Disegna, evidenzia, scrivi, oscura o ritaglia";
  box.append(img, did, chiudi, modifica);
  if (foto.length > 1) box.append(prec, succ);
  box.onkeydown = null;
  document.addEventListener("keydown", function k(e) {
    if (!document.body.contains(box)) return document.removeEventListener("keydown", k);
    if (e.key === "ArrowLeft") prec.click(); if (e.key === "ArrowRight") succ.click();
  });
  document.body.append(box); mostra();
}
function guardaVideo(f) {
  document.getElementById("lampada")?.remove();
  const box = el("div"); box.id = "lampada";
  const v = el("video"); v.src = fileUrl(f.percorso); v.controls = true; v.autoplay = true;
  const chiudi = bottone("✕", () => { v.pause(); box.remove(); }, "chiudi");
  box.append(v, chiudi, el("div", "didascalia", f.nome));
  document.body.append(box);
  v.requestFullscreen?.().catch(() => {});
}

// --- le viste ------------------------------------------------------------------------------------------
const VISTE = {
  async file(box, cartella = "") {
    let data;
    try { data = await api(`/api/cartella?p=${encodeURIComponent(cartella)}`); }
    catch (e) { testa(box, "File").append(el("p", "vuoto", `Non riesco ad aprire la cartella (${e.message}).`)); return; }
    const briciole = el("span", "briciole");
    const parti = data.cartella ? data.cartella.split("/") : [];
    const casaB = bottone("Casa", () => apriVista("file", "")); briciole.append(casaB);
    parti.forEach((p, i) => { briciole.append(" › ", bottone(p, () => apriVista("file", parti.slice(0, i + 1).join("/")))); });
    const nuova = bottone("＋ Nuova cartella", async () => {
      const nome = await chiediTesto("Nome della nuova cartella");
      if (nome) { await api("/api/file/nuova-cartella", { p: data.cartella, nome }).catch(e => avviso(e.message)); apriVista("file", data.cartella); }
    });
    const corpo = testa(box, "File", [briciole, nuova]);
    if (!data.voci.length) { corpo.append(el("p", "vuoto", "Questa cartella è vuota.")); return; }
    const griglia = el("div", "elenco-file");
    for (const f of data.voci) {
      const b = el("button", "voce-file"); b.title = f.nome;
      const ic = el("div", "icona-file");
      if (f.tipo === "immagine") { const i = el("img"); i.loading = "lazy"; i.src = fileUrl(f.percorso); i.alt = ""; ic.append(i); }
      else ic.replaceChildren(svgIcona(SIMBOLI[f.tipo] || "pacco"));
      const altro = el("span", "altro", "⋯");
      b.append(ic, el("span", "nome-file", f.nome), altro);
      b.onclick = e => { if (e.target === altro) return; apriFile(f); };
      const azioni = ev => {
        ev.preventDefault(); ev.stopPropagation();
        menu(ev.clientX, ev.clientY, [
          ["Apri", () => apriFile(f)],
          ["Apri con un programma", () => api("/api/file/apri-con", { p: f.percorso }).catch(e => avviso(e.message))],
          ["Rinomina", async () => {
            const nome = await chiediTesto("Nuovo nome", f.nome);
            if (nome && nome !== f.nome) { await api("/api/file/rinomina", { p: f.percorso, nome }).catch(e => avviso(e.message)); apriVista("file", data.cartella); }
          }],
          ["Chiedi a Nova…", () => { chiudiVista(); $("testo").value = `Su «${f.nome}»: `; $("testo").focus(); }],
          ["Sposta nel cestino", async () => {
            await api("/api/file/cestino", { p: f.percorso }).catch(e => avviso(e.message)); apriVista("file", data.cartella);
          }],
        ]);
      };
      altro.onclick = azioni; b.oncontextmenu = azioni;
      griglia.append(b);
    }
    corpo.append(griglia);
  },

  async foto(box) {
    const corpo = testa(box, "Foto");
    const { voci, cartella } = await api("/api/raccolta/foto").catch(() => ({ voci: [], cartella: "Immagini" }));
    if (!voci.length) { corpo.append(el("p", "vuoto", `Nessuna foto in «${cartella}».\nCollega il telefono e chiedi a Nova di copiare le foto.`)); return; }
    // le persone riconosciute dai volti: un tocco su chi non ha nome per dirlo a Nova
    const pers = await api("/api/persone").catch(() => ({ persone: [], attivo: false }));
    if (pers.persone.length) {
      const fila = el("div", "persone");
      for (const p of pers.persone) {
        const b = el("button", "persona" + (p.nome ? "" : " senza-nome"));
        const img = el("img"); img.alt = p.nome || "?"; img.src = `/api/miniatura-volto/${p.volto}?t=${encodeURIComponent(TOKEN)}`;
        b.append(img, el("span", "", p.nome || "Chi è?"), el("small", "", `${p.foto} foto`));
        b.onclick = async () => {
          if (p.nome) { chiudiVista(); chiedi(`mostrami le foto di ${p.nome}`); return; }
          const nome = await chiediTesto("Chi è questa persona?");
          if (nome) { await api("/api/persona", { id: p.id, nome }).catch(e => avviso(e.message)); apriVista("foto"); }
        };
        fila.append(b);
      }
      corpo.append(el("div", "etichetta-sez", "Persone"), fila);
    } else if (!pers.attivo) {
      const invito = el("div", "invito");
      invito.append(el("span", "", "Vuoi cercare le foto per persone e per cosa c'è dentro («il mare», «la torta»)?"),
                    bottone("Riconosci le foto", () => { chiudiVista(); chiedi("riconosci le mie foto"); }, "bottone primo"));
      corpo.append(invito);
    }
    const g = el("div", "griglia-foto");
    voci.forEach((f, i) => {
      const b = el("button"); const img = el("img"); img.loading = "lazy"; img.alt = f.nome;
      img.src = `/api/miniatura-file?p=${encodeURIComponent(f.percorso)}&t=${encodeURIComponent(TOKEN)}`;
      b.append(img); b.onclick = () => lampada(voci, i); g.append(b);
    });
    corpo.append(g);
  },

  async musica(box, daSuonare) {
    const corpo = testa(box, "Musica");
    const { voci, cartella } = await api("/api/raccolta/musica").catch(() => ({ voci: [], cartella: "Musica" }));
    let lista = voci;
    if (daSuonare && !lista.some(v => v.percorso === daSuonare)) lista = [{ nome: daSuonare.split("/").pop(), percorso: daSuonare }, ...lista];
    if (!lista.length) { corpo.append(el("p", "vuoto", `Nessun brano in «${cartella}».`)); return; }
    const elenco = el("div", "brani");
    let attuale = -1;
    const titolo = el("div", "titolo", "—");
    const barra = el("input"); barra.type = "range"; barra.min = 0; barra.max = 1000; barra.value = 0;
    const play = bottone("▶", () => { if (attuale < 0) suona(0); else if (audio.paused) audio.play(); else audio.pause(); }, "tondo grande");
    const suona = i => {
      attuale = (i + lista.length) % lista.length;
      audio.src = fileUrl(lista[attuale].percorso); audio.play().catch(() => {});
      titolo.textContent = lista[attuale].nome.replace(/\.[^.]+$/, "");
      elenco.querySelectorAll(".brano").forEach((b, j) => b.classList.toggle("attivo", j === attuale));
    };
    lista.forEach((f, i) => {
      const b = el("button", "brano"); b.append(el("span", "n", String(i + 1)), el("span", "", f.nome.replace(/\.[^.]+$/, "")));
      b.onclick = () => suona(i); elenco.append(b);
    });
    audio.onplay = () => play.textContent = "⏸"; audio.onpause = () => play.textContent = "▶";
    audio.ontimeupdate = () => { if (audio.duration) barra.value = Math.round(audio.currentTime / audio.duration * 1000); };
    audio.onended = () => suona(attuale + 1);
    barra.oninput = () => { if (audio.duration) audio.currentTime = barra.value / 1000 * audio.duration; };
    corpo.append(elenco);
    const lettore = el("div", "lettore");
    lettore.append(bottone("⏮", () => suona(attuale - 1), "tondo"), play, bottone("⏭", () => suona(attuale + 1), "tondo"), titolo, barra);
    box.append(lettore);
    if (daSuonare) suona(lista.findIndex(v => v.percorso === daSuonare));
  },

  async video(box) {
    const corpo = testa(box, "Video");
    const { voci, cartella } = await api("/api/raccolta/video").catch(() => ({ voci: [], cartella: "Video" }));
    if (!voci.length) { corpo.append(el("p", "vuoto", `Nessun video in «${cartella}».`)); return; }
    const g = el("div", "griglia-video");
    for (const f of voci) {
      const b = el("button"); b.append(el("div", "anteprima", "▶"), el("span", "", f.nome));
      b.onclick = () => guardaVideo(f); g.append(b);
    }
    corpo.append(g);
  },

  async note(box, daAprire) {
    const nuova = bottone("＋ Nuova nota", () => apri(null), "bottone primo");
    const corpo = testa(box, "Note", nuova);
    const wrap = el("div", "note"); const lista = el("div", "lista-note"); const foglio = el("div", "foglio");
    const area = el("textarea"); area.placeholder = "Scrivi qui… (si salva da sola)";
    const stato = el("div", "salvata", "");
    foglio.append(area, stato); wrap.append(lista, foglio); corpo.append(wrap);
    let percorso = null, timer = null;
    const salva = async () => {
      if (!area.value.trim() && !percorso) return;
      try {
        const r = await api("/api/nota", { p: percorso || "", testo: area.value });
        const nuovaNota = !percorso; percorso = r.percorso; stato.textContent = "Salvata ✓";
        if (nuovaNota) carica();
      } catch (e) { stato.textContent = `Non salvata: ${e.message}`; }
    };
    area.oninput = () => { stato.textContent = "…"; clearTimeout(timer); timer = setTimeout(salva, 800); };
    async function apri(p) {
      percorso = p; area.value = ""; stato.textContent = "";
      if (p) { try { area.value = (await api(`/api/nota?p=${encodeURIComponent(p)}`)).testo; } catch (e) { stato.textContent = e.message; } }
      lista.querySelectorAll("button").forEach(b => b.classList.toggle("attiva", b.dataset.p === p));
      area.focus();
    }
    async function carica() {
      const { voci } = await api("/api/raccolta/note").catch(() => ({ voci: [] }));
      lista.replaceChildren();
      for (const n of voci) {
        const b = el("button"); b.dataset.p = n.percorso;
        b.append(n.nome.replace(/\.(txt|md)$/, ""), el("small", "", new Date(n.modificato * 1000).toLocaleString("it-IT", { dateStyle: "medium", timeStyle: "short" })));
        b.onclick = () => apri(n.percorso); b.classList.toggle("attiva", n.percorso === percorso); lista.append(b);
      }
      if (!voci.length) lista.append(el("p", "vuoto", "Ancora nessuna nota."));
    }
    await carica();
    apri(daAprire || null);
  },

  documento(box, f) {
    const conProgramma = bottone("Apri con un programma", () => api("/api/file/apri-con", { p: f.percorso }).catch(e => avviso(e.message)));
    const chiediNova = bottone("Chiedi a Nova", () => { chiudiVista(); $("testo").value = `Riassumi «${f.nome}»`; $("testo").focus(); });
    testa(box, f.nome, [chiediNova, conProgramma]).remove();
    const fr = el("iframe", "riquadro-doc");
    fr.setAttribute("sandbox", "");  // il documento non esegue nulla
    fr.src = `/doc/vedi?p=${encodeURIComponent(f.percorso)}&t=${encodeURIComponent(TOKEN)}`;
    box.append(fr);
  },

  async impostazioni(box, sezione = "wifi") {
    const corpo = testa(box, "Impostazioni");
    const wrap = el("div", "impostazioni"); const nav = el("div", "sezioni"); const pan = el("div", "pannello-imp");
    wrap.append(nav, pan); corpo.append(wrap);
    const SEZ = [["wifi", "Wi-Fi"], ["bluetooth", "Bluetooth"], ["suono", "Suono e schermo"], ["voce", "Voce di Nova"], ["tastiera", "Tastiera"],
                 ["aspetto", "Testo e carattere"], ["aggiornamenti", "Aggiornamenti"], ["posta", "Posta"], ["account", "Password"], ["privacy", "Privacy e memoria"],
                 ["info", "Questo computer"], ["energia", "Spegni"]];
    for (const [id, t] of SEZ) {
      const b = bottone("", () => apriVista("impostazioni", id), ""); b.classList.toggle("attiva", id === sezione);
      b.append(svgIcona(id), t); nav.append(b);
    }
    const carta = (...figli) => { const c = el("div", "carta"); c.append(...figli); pan.append(c); return c; };
    const riga = (titolo, nota, ...ctrl) => {
      const r = el("div", "riga-imp"); const c = el("div", "cosa"); c.append(titolo); if (nota) c.append(el("small", "", nota));
      r.append(c, ...ctrl); return r;
    };
    const interruttore = (acceso, fn) => { const b = el("button", "interruttore" + (acceso ? " acceso" : "")); b.onclick = fn; return b; };
    const esito = el("div", "esito");
    const dici = (r) => { esito.textContent = r.messaggio || (r.ok ? "Fatto." : "Non è riuscito."); esito.className = "esito " + (r.ok ? "ok" : "no"); };
    const parte = { suono: "suono", wifi: "wifi", bluetooth: "bluetooth", voce: "voce", info: "info", aggiornamenti: "info",
                    tastiera: "tastiera", privacy: "privacy" }[sezione];
    const d = parte ? await api(`/api/impostazioni?parte=${parte}`).catch(e => ({ errore: e.message })) : {};

    if (sezione === "wifi") {
      const w = d.wifi || {};
      const c = carta(riga("Wi-Fi", w.scheda === false ? "Non trovo la scheda Wi-Fi in questo computer." : (w.acceso ? "Acceso" : "Spento"),
        interruttore(w.acceso, () => api("/api/impostazioni/wifi", { azione: w.acceso ? "spegni" : "accendi" }).then(() => setTimeout(() => apriVista("impostazioni", "wifi"), 1500)))));
      if (w.acceso) {
        if (!(w.reti || []).length) c.append(el("p", "nota", "Cerco le reti… se non ne compare nessuna, riprova tra poco."));
        for (const n of w.reti || []) {
          const collega = bottone(n.attiva ? "Collegato" : "Collega", async () => {
            let password = "";
            if (n.protetta && !n.attiva) {
              password = await chiediPassword(`Password della rete «${n.nome}»`);
              if (password === null) return;
            }
            esito.textContent = "Mi collego…"; esito.className = "esito";
            dici(await api("/api/impostazioni/wifi", { azione: "collega", nome: n.nome, password }).catch(e => ({ ok: false, messaggio: e.message })));
            setTimeout(() => apriVista("impostazioni", "wifi"), 1500);
          }, n.attiva ? "bottone" : "bottone primo");
          c.append(riga(n.protetta ? conIcona("privacy", n.nome) : n.nome, null, el("span", "segnale", `${n.segnale}%`), collega));
        }
      }
      pan.append(esito);
    } else if (sezione === "bluetooth") {
      const bt = d.bluetooth || {};
      const c = carta(riga("Bluetooth", bt.presente ? (bt.acceso ? "Acceso" : "Spento") : "Non trovo il Bluetooth in questo computer.",
        interruttore(bt.acceso, () => api("/api/impostazioni/bluetooth", { azione: bt.acceso ? "spegni" : "accendi" }).then(() => apriVista("impostazioni", "bluetooth")))));
      for (const dev of bt.dispositivi || []) {
        c.append(riga(dev.nome, dev.collegato ? "Collegato" : "Non collegato", bottone(dev.collegato ? "Scollega" : "Collega", async () => {
          dici(await api("/api/impostazioni/bluetooth", { azione: dev.collegato ? "scollega" : "collega", indirizzo: dev.indirizzo }));
          apriVista("impostazioni", "bluetooth");
        })));
      }
      c.append(el("p", "nota", "Per abbinare un dispositivo nuovo (cuffie, telefono) di' a Nova: «abbina le cuffie Bluetooth»."));
      pan.append(esito);
    } else if (sezione === "suono") {
      const cursore = (valore, rotta) => {
        const r = el("input"); r.type = "range"; r.min = 0; r.max = 100; r.value = valore ?? 50; r.disabled = valore == null;
        r.onchange = () => api(`/api/impostazioni/${rotta}`, { livello: +r.value }).catch(() => {}); return r;
      };
      const au = d.audio || { uscite: [], ingressi: [], programmi: [] };
      const azione = (corpo, poi) => api("/api/impostazioni/audio", corpo).then(r => { if (r.messaggio) dici(r); if (poi) poi(r); }).catch(e => dici({ ok: false, messaggio: e.message }));
      const livello = (e) => {  // volume e muto di un'uscita, di un microfono o di un programma
        const r = el("input"); r.type = "range"; r.min = 0; r.max = 150; r.value = e.livello ?? 50; r.disabled = e.livello == null;
        r.className = "volume-audio"; r.title = "Fino a 150%: oltre 100 il suono si amplifica";
        r.onchange = () => azione({ azione: "volume", id: e.id, livello: +r.value });
        const m = el("button", "muto-audio" + (e.muto ? " acceso" : ""), e.muto ? "🔇" : "🔈"); m.title = e.muto ? "Riattiva" : "Silenzia";
        m.onclick = () => { e.muto = !e.muto; m.textContent = e.muto ? "🔇" : "🔈"; m.classList.toggle("acceso", e.muto); azione({ azione: "muto", id: e.id, muto: e.muto }); };
        const box = el("div", "livello-audio"); box.append(m, r); return box;
      };
      const elenco = (titolo, nota, voci, vuoto, extra) => {
        const c = carta(el("h3", "", titolo));
        if (nota) c.append(el("p", "nota", nota));
        if (!voci.length) c.append(el("p", "nota", vuoto));
        for (const e of voci) {
          const r = el("div", "scelta-audio" + (e.predefinito ? " in-uso" : ""));
          const nome = el("button", "nome-audio");
          nome.append(el("span", "icona-audio", e.icona), el("span", "", e.nome),
                      el("small", "", e.predefinito ? "in uso" : e.da_attivare && Object.keys(e.da_attivare).length ? "spenta: tocca per attivarla" : ""));
          nome.onclick = () => azione({ azione: "scegli", id: e.id, profilo: e.da_attivare }, () => setTimeout(() => apriVista("impostazioni", "suono"), 600));
          r.append(nome);
          if (e.predefinito) r.append(livello(e));
          c.append(r);
        }
        if (extra) c.append(extra);
        return c;
      };
      const prova = el("div", "azioni");
      prova.append(bottone("🔔 Prova l'uscita", () => azione({ azione: "prova" }), "bottone"));
      elenco("Uscita", "Da dove esce il suono. Tocca per sceglierla: AIOS se la ricorda.", au.uscite,
             "Non trovo uscite audio.", prova);
      const provaMic = el("div", "azioni");
      provaMic.append(bottone("🎙️ Prova il microfono", (ev) => { ev.target.disabled = true; dici({ ok: true, messaggio: "Parla per 4 secondi… poi ti faccio riascoltare." });
        azione({ azione: "prova-microfono" }, () => { ev.target.disabled = false; }); }, "bottone"));
      elenco("Ingresso", "Il microfono che usano Nova e i programmi.", au.ingressi, "Nessun microfono collegato.", provaMic);
      const app = carta(el("h3", "", "Volume dei programmi"));
      if (!au.programmi.length) app.append(el("p", "nota", "Nessun programma sta suonando adesso."));
      for (const p of au.programmi) app.append(riga(p.programma, null, livello(p)));
      carta(riga("☀️ Luminosità", d.luminosita?.livello == null ? "Questo schermo non la regola da qui" : null, cursore(d.luminosita?.livello, "luminosita")));
      pan.append(esito);
    } else if (sezione === "voce") {
      const v = d.voce || { voci: [] };
      const c = carta(el("p", "nota", "Scegli come parla Nova. Tocca una voce per sentirla."));
      for (const voce of v.voci) {
        c.append(riga(voce.nome, voce.id === v.scelta ? "In uso" : null, bottone(voce.id === v.scelta ? "Ascolta" : "Usa questa", async () => {
          await api("/api/impostazioni/voce", { voce: voce.id }).catch(e => avviso(e.message)); apriVista("impostazioni", "voce");
        }, voce.id === v.scelta ? "bottone" : "bottone primo")));
      }
      if (!v.voci.length) c.append(el("p", "nota", "Nessuna voce installata."));
      const imp = await api("/api/impronta").catch(() => ({ persone: [] }));
      const c2 = carta(el("h3", "", "Chi può parlare con Nova"));
      c2.append(riga("Rispondi solo alle voci che conosci", imp.persone.length ? "Ignora la TV, i film e le voci sconosciute" : "Prima fammi imparare almeno una voce",
        interruttore(imp.solo_conosciute, () => api("/api/impronta/modo", { solo_conosciute: !imp.solo_conosciute }).then(() => apriVista("impostazioni", "voce")))));
      for (const nome of imp.persone) {
        c2.append(riga(`🗣️ ${nome}`, null, bottone("Togli", async () => {
          await api("/api/impronta/modo", { togli: nome }); apriVista("impostazioni", "voce");
        })));
      }
      const zona = el("div", ""); c2.append(zona);
      c2.append(el("div", "azioni"));
      c2.lastChild.append(
        bottone(imp.persone.length ? "Reimpara la mia voce" : "Impara la mia voce", () => { zona.replaceChildren(); imparaVoce(zona, { fine: () => apriVista("impostazioni", "voce") }); }, "bottone primo"),
        bottone("Aggiungi una persona", async () => {
          const nome = await chiediTesto("Come si chiama?"); if (!nome) return;
          zona.replaceChildren(el("p", "nota", `Ora fai leggere le frasi a ${nome}.`));
          imparaVoce(zona, { nome, fine: () => apriVista("impostazioni", "voce") });
        }));
    } else if (sezione === "tastiera") {
      const k = d.tastiera || { lingue: {} };
      const c = carta(el("p", "nota", "La disposizione dei tasti. Cambia subito, senza riavviare. Puoi anche dire a Nova «metti la tastiera inglese»."));
      for (const [id, nome] of Object.entries(k.lingue)) {
        c.append(riga(nome, id === k.scelta ? "In uso" : null, id === k.scelta ? el("span", "", "✓") : bottone("Usa questa", async () => {
          dici(await api("/api/impostazioni/tastiera", { lingua: id }).catch(e => ({ ok: false, messaggio: e.message })));
          apriVista("impostazioni", "tastiera");
        }, "bottone primo")));
      }
      pan.append(esito);
    } else if (sezione === "aggiornamenti") {
      const c = carta(el("p", "", d.aggiornamenti || ""));
      c.append(el("div", "azioni"));
      c.lastChild.append(bottone("Cerca aggiornamenti", () => { chiudiVista(); chiedi("aggiorna il sistema"); }, "bottone primo"));
      const tok = el("input", "campo"); tok.type = "password"; tok.placeholder = "Token di GitHub (sola lettura)";
      carta(el("h3", "", "Aggiornamenti da GitHub"),
            el("p", "nota", "Se il repository di AIOS è pubblico non serve nulla: le nuove versioni arrivano da sole. Solo per un repository privato: crea un token con il solo permesso «Contents: read» e incollalo qui. Resta nel portachiavi del computer."),
            riga("Token", null, tok, bottone("Collega", async () => {
              dici(await api("/api/impostazioni/github", { token: tok.value }).catch(e => ({ ok: false, messaggio: e.message }))); tok.value = "";
            }, "bottone primo")));
      pan.append(esito);
    } else if (sezione === "posta") {
      const ora = await api("/api/impostazioni/posta").catch(() => ({ account: [] }));
      if ((ora.account || []).length) carta(riga("Account collegati", null, el("b", "", ora.account.join(" · "))));
      const indirizzo = el("input", "campo"); indirizzo.type = "email"; indirizzo.placeholder = "nome@gmail.com";
      const pw = el("input", "campo"); pw.type = "password"; pw.placeholder = "Password"; pw.autocomplete = "off";
      const aiuto = el("small", "", "");
      const conOauth = bottone("Accedi con il tuo account", async () => {
        esito.textContent = "Si apre la pagina di accesso nel browser…";
        dici(await api("/api/impostazioni/posta", { indirizzo: indirizzo.value, oauth: true }).catch(e => ({ ok: false, messaggio: e.message })));
        if (esito.classList.contains("ok")) setTimeout(() => apriVista("impostazioni", "posta"), 1500);
      }, "bottone primo");
      conOauth.style.display = "none";
      indirizzo.onchange = async () => {
        const s = await api(`/api/impostazioni/posta/servizio?indirizzo=${encodeURIComponent(indirizzo.value)}`).catch(() => ({}));
        aiuto.textContent = (s.nome ? s.nome + ": " : "") + (s.aiuto || "");
        conOauth.style.display = s.oauth ? "" : "none";
      };
      carta(el("p", "nota", "Collega la tua casella: ti avviso delle mail importanti e trovo bollette e scadenze. " +
                            "La password resta nel portachiavi di questo computer."),
            riga("Indirizzo", null, indirizzo),
            riga("Password", aiuto, pw),
            riga("", null, conOauth, bottone("Collega", async () => {
              esito.textContent = "Provo ad accedere…";
              dici(await api("/api/impostazioni/posta", { indirizzo: indirizzo.value, password: pw.value }).catch(e => ({ ok: false, messaggio: e.message })));
              pw.value = "";
              if (esito.classList.contains("ok")) setTimeout(() => apriVista("impostazioni", "posta"), 1500);
            }, "bottone primo")));
      pan.append(esito);
    } else if (sezione === "account") {
      const vecchia = el("input", "campo"); vecchia.type = "password"; vecchia.placeholder = "Password attuale";
      const nuova = el("input", "campo"); nuova.type = "password"; nuova.placeholder = "Nuova password";
      const ripeti = el("input", "campo"); ripeti.type = "password"; ripeti.placeholder = "Ripeti la nuova password";
      carta(riga("Password attuale", null, vecchia), riga("Nuova password", "Almeno 6 caratteri", nuova), riga("Ripeti", null, ripeti),
            riga("", null, bottone("Cambia password", async () => {
              if (nuova.value !== ripeti.value) return dici({ ok: false, messaggio: "Le due password nuove non sono uguali." });
              esito.textContent = "Un momento…";
              dici(await api("/api/impostazioni/password", { vecchia: vecchia.value, nuova: nuova.value }).catch(e => ({ ok: false, messaggio: e.message })));
              vecchia.value = nuova.value = ripeti.value = "";
            }, "bottone primo")));
      pan.append(esito);
    } else if (sezione === "info") {
      const i = d.info || {};
      carta(riga("Versione di AIOS", null, el("b", "", i.versione || "—")),
            riga("Processore", null, el("span", "", i.processore || "—")),
            riga("Memoria", null, el("span", "", i.memoria_gb ? `${i.memoria_gb} GB` : "—")),
            riga("Spazio libero", null, el("span", "", i.disco_libero_gb != null ? `${i.disco_libero_gb} GB` : "—")),
            riga("Modello AI di Nova", "Tutto sul computer, niente cloud", el("span", "", i.modello || "—"),
                 bottone("Più potente?", () => { chiudiVista(); chiedi("quali modelli AI mi consigli?"); })));
    } else if (sezione === "aspetto") {
      const ora = await api("/api/aspetto").catch(() => ({ carattere: "Inter", scala: 1 }));
      const el_ = await api("/api/aspetto/caratteri").catch(() => ({ caratteri: [], minimo: 0.8, massimo: 1.6 }));
      const prova = el("p", "", "Ciao, sono Nova. Quanto è leggibile questo testo? 0123456789");
      prova.style.cssText = `font-family:"${ora.carattere}",sans-serif;font-size:${Math.round(18 * ora.scala)}px;margin:4px 0 0`;
      const salva = (cambio) => api("/api/aspetto", cambio).then(r => { dici({ ok: true, messaggio: r.messaggio }); window.novaAspetto && window.novaAspetto(); })
        .catch(e => dici({ ok: false, messaggio: e.message }));
      const cursore = el("input"); cursore.type = "range"; cursore.min = el_.minimo; cursore.max = el_.massimo; cursore.step = 0.05; cursore.value = ora.scala;
      const valore = el("b", "", `${Math.round(ora.scala * 100)}%`);
      cursore.oninput = () => { valore.textContent = `${Math.round(cursore.value * 100)}%`; prova.style.fontSize = `${Math.round(18 * cursore.value)}px`; };
      cursore.onchange = () => salva({ scala: Number(cursore.value) });
      carta(riga("Dimensione del testo", "Vale per tutto AIOS e per i programmi", cursore, valore),
            riga("", null, bottone("Normale", () => { cursore.value = 1; cursore.oninput(); salva({ scala: 1 }); }, "bottone")));
      const lista = el("div", "caratteri");
      for (const c of el_.caratteri) {
        const b = bottone("", () => { prova.style.fontFamily = `"${c.famiglia}", sans-serif`; salva({ carattere: c.famiglia });
                                      lista.querySelectorAll("button").forEach(x => x.classList.toggle("attiva", x === b)); }, "carattere-scelta");
        b.classList.toggle("attiva", c.famiglia === ora.carattere);
        const nome = el("b", "", c.famiglia); nome.style.fontFamily = `"${c.famiglia}", sans-serif`;
        b.replaceChildren(nome, el("small", "", c.descrizione));
        lista.append(b);
      }
      carta(riga("Carattere", "Puoi anche dirlo a Nova: «usa un carattere più leggibile»"), lista, prova);
      pan.append(esito);
    } else if (sezione === "privacy") {
      const di = d.diario || {};
      const ga = d.galleria || {};
      carta(riga("Riconoscimento delle foto", ga.attivo ? `Foto guardate: ${ga.descritte || 0} su ${ga.foto || 0}. Cosa c'è, scritte e persone, solo su questo computer.`
                   : "Nova guarda le foto a riposo e in carica: cosa c'è, scritte e persone. Niente esce dal computer.",
                 interruttore(ga.attivo, () => api("/api/impostazioni/galleria", { attivo: !ga.attivo }).then(r => { dici(r); apriVista("impostazioni", "privacy"); }))),
            riga("Dimentica le foto", "Cancella descrizioni, volti e nomi delle persone",
                 bottone("Cancella", async () => {
                   if (!await chiediConferma("Cancello tutto quello che Nova ha imparato dalle foto (le foto restano)?")) return;
                   dici(await api("/api/impostazioni/galleria", { cancella: true }).catch(e => ({ ok: false, messaggio: e.message })));
                 }, "bottone pericolo")));
      carta(riga("Diario delle attività", `Nova ricorda programmi, file, siti e conversazioni per rispondere a «dove mi ero fermato?». Resta solo su questo computer, per ${di.conserva || 90} giorni.`,
                 interruttore(di.attivo, () => api("/api/impostazioni/diario", { attivo: !di.attivo }).then(() => apriVista("impostazioni", "privacy")))),
            riga("Cancella il diario", di.giorni ? `${di.giorni} giorni annotati` : "Il diario è vuoto",
                 bottone("Cancella", async () => {
                   if (!await chiediConferma("Cancello tutto il diario? Nova non ricorderà più cosa hai fatto finora.")) return;
                   dici(await api("/api/impostazioni/diario", { cancella: true }).catch(e => ({ ok: false, messaggio: e.message })));
                   apriVista("impostazioni", "privacy");
                 }, "bottone pericolo")));
      pan.append(esito);
    } else if (sezione === "energia") {
      const az = (t, a, cls) => bottone(t, () => { (async () => { if (a === "spegni" || a === "riavvia" ? await chiediConferma(`${t}?`) : true) api("/api/impostazioni/energia", { azione: a }).catch(() => {}); })(); }, cls);
      carta(riga("Blocca lo schermo", null, az("Blocca", "blocca", "bottone")),
            riga("Sospendi", "Il computer dorme, riprendi da dove eri", az("Sospendi", "sospendi", "bottone")),
            riga("Riavvia", null, az("Riavvia", "riavvia", "bottone")),
            riga("Spegni", null, az("Spegni", "spegni", "bottone pericolo")));
    }
    if (d.errore) pan.append(el("p", "esito no", d.errore));
  },
};

// --- imparare la voce (benvenuto e Impostazioni) -----------------------------------------------------------
async function imparaVoce(box, { nome = "", fine } = {}) {
  const st = await api("/api/impronta").catch(() => ({ frasi: [], disponibile: false }));
  if (!st.disponibile) { box.append(el("p", "sotto", "Su questo computer manca il modello per riconoscere le voci.")); return; }
  let i = 0, fatte = 0;
  const frase = el("p", "", ""); frase.style.cssText = "font-size:24px;font-weight:700;margin:6px 0";
  const stato = el("p", "nota", "");
  const barra = el("div", "puntini");
  for (let k = 0; k < st.frasi.length; k++) barra.append(el("i"));
  const registra = bottone("🎙️ Registra", async () => {
    registra.disabled = true; stato.textContent = "Ti ascolto… leggi la frase ad alta voce";
    const r = await api("/api/impronta/campione", { da_capo: i === 0 && fatte === 0, secondi: 5 }).catch(e => ({ ok: false, messaggio: e.message }));
    registra.disabled = false;
    if (!r.ok) { stato.textContent = r.messaggio; return; }
    fatte = r.registrate; barra.children[i].classList.add("si"); i++;
    if (i < st.frasi.length) { frase.textContent = `«${st.frasi[i]}»`; stato.textContent = "Bene! La prossima."; return; }
    stato.textContent = "Un momento…";
    const f = await api("/api/impronta/fine", { nome }).catch(e => ({ ok: false, messaggio: e.message }));
    stato.textContent = f.messaggio; registra.hidden = true;
    api("/api/parla", { testo: f.ok ? "Ti ho riconosciuto. D'ora in poi rispondo alla tua voce." : f.messaggio }).catch(() => {});
    if (f.ok && fine) setTimeout(fine, 1500);
  }, "bottone primo");
  frase.textContent = `«${st.frasi[0]}»`;
  box.append(barra, frase, stato, registra);
}

// --- primi passi ------------------------------------------------------------------------------------------
function eseguiPasso(p) {
  if (p.azione.vista) return apriVista(p.azione.vista, p.azione.parte);
  chiudiVista(); chiedi(p.azione.chiedi);
}

// --- modificare un'immagine (screenshot, foto): penna, evidenziatore, freccia, riquadro, testo, oscura, ritaglia ---
VISTE.modifica = async function (box, percorso) {
  const nome = percorso.split("/").pop();
  const COLORI = ["#E5484D", "#FFD60A", "#0A84FF", "#30A46C", "#111111", "#FFFFFF"];
  const STRUMENTI = [["penna", "✏️ Penna"], ["evidenzia", "🖍️ Evidenzia"], ["freccia", "↗ Freccia"], ["riquadro", "▭ Riquadro"],
                     ["testo", "T Testo"], ["oscura", "▦ Oscura"], ["ritaglia", "✂ Ritaglia"]];
  let strumento = "freccia", colore = COLORI[0];
  const canvas = el("canvas", "tela"), ctx = canvas.getContext("2d");
  const indietro = [];  // per «annulla»: com'era prima di ogni modifica
  const salva = async copia => {
    const dati = canvas.toDataURL("image/png");
    try {
      const r = await api("/api/file/salva-immagine", { p: percorso, dati, copia });
      avviso(`Salvata: ${r.nome}`);
      if (copia) { percorso = r.percorso; h.textContent = r.nome; }
    } catch (e) { avviso(`Non sono riuscita a salvarla: ${e.message}`); }
  };
  const annulla = bottone("↶ Annulla", () => { const prima = indietro.pop(); if (prima) ripristina(prima); });
  const corpo = testa(box, `Modifica · ${nome}`, [annulla, bottone("Salva una copia", () => salva(true), "bottone primo"),
                                                 bottone("Sovrascrivi", async () => { if (await chiediConferma(`Sostituisco «${nome}» con la versione modificata?`)) salva(false); })]);
  const h = box.querySelector(".testa-vista h2");
  const barra = el("div", "strumenti-modifica");
  const bottoni = {};
  for (const [id, etichetta] of STRUMENTI) {
    const b = bottone(etichetta, () => { strumento = id; for (const x of Object.values(bottoni)) x.classList.toggle("attivo", x === b); });
    bottoni[id] = b; barra.append(b);
  }
  bottoni[strumento].classList.add("attivo");
  const tavolozza = el("div", "tavolozza");
  for (const c of COLORI) {
    const b = el("button", "colore"); b.style.background = c; b.title = c;
    b.onclick = () => { colore = c; tavolozza.querySelectorAll(".colore").forEach(x => x.classList.toggle("attivo", x === b)); };
    if (c === colore) b.classList.add("attivo");
    tavolozza.append(b);
  }
  barra.append(tavolozza);
  const area = el("div", "area-modifica"); area.append(canvas);
  corpo.append(barra, area);

  const img = new Image();
  img.src = fileUrl(percorso);
  try { await img.decode(); } catch { corpo.append(el("p", "vuoto", "Non riesco ad aprire l'immagine.")); return; }
  canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
  ctx.drawImage(img, 0, 0);

  const istantanea = () => ({ w: canvas.width, h: canvas.height, dati: ctx.getImageData(0, 0, canvas.width, canvas.height) });
  // la dimensione si tocca solo se cambia (un ritaglio annullato): reimpostarla cancella anche il tratto in corso
  const ripristina = s => { if (canvas.width !== s.w || canvas.height !== s.h) { canvas.width = s.w; canvas.height = s.h; } ctx.putImageData(s.dati, 0, 0); };
  const punto = e => { const r = canvas.getBoundingClientRect(); return [(e.clientX - r.left) * canvas.width / r.width, (e.clientY - r.top) * canvas.height / r.height]; };
  const spessore = () => Math.max(3, Math.round(canvas.width / 400));
  const freccia = (x0, y0, x1, y1) => {
    const w = spessore() * 1.4, a = Math.atan2(y1 - y0, x1 - x0), l = w * 5;
    ctx.strokeStyle = ctx.fillStyle = colore; ctx.lineWidth = w; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1 - Math.cos(a) * l * .6, y1 - Math.sin(a) * l * .6); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x1, y1);
    ctx.lineTo(x1 - l * Math.cos(a - .45), y1 - l * Math.sin(a - .45)); ctx.lineTo(x1 - l * Math.cos(a + .45), y1 - l * Math.sin(a + .45));
    ctx.closePath(); ctx.fill();
  };
  const oscura = (x, y, w, hh) => {
    [x, w] = w < 0 ? [x + w, -w] : [x, w]; [y, hh] = hh < 0 ? [y + hh, -hh] : [y, hh];
    if (w < 2 || hh < 2) return;
    const passo = Math.max(8, Math.round(canvas.width / 90));
    const d = ctx.getImageData(x, y, w, hh);
    for (let by = 0; by < hh; by += passo) for (let bx = 0; bx < w; bx += passo) {
      const i = (by * w + bx) * 4;
      ctx.fillStyle = `rgb(${d.data[i]},${d.data[i + 1]},${d.data[i + 2]})`;
      ctx.fillRect(x + bx, y + by, Math.min(passo, w - bx), Math.min(passo, hh - by));
    }
  };
  let inizio = null, prima = null;
  canvas.onpointerdown = async e => {
    const [x, y] = punto(e);
    indietro.push(istantanea()); if (indietro.length > 25) indietro.shift();
    if (strumento === "testo") {
      const t = await chiediTesto("Testo da scrivere");
      if (!t) { indietro.pop(); return; }
      const px = Math.max(18, Math.round(canvas.width / 45));
      ctx.font = `700 ${px}px system-ui, sans-serif`; ctx.textBaseline = "top"; ctx.lineJoin = "round";
      ctx.lineWidth = px / 5; ctx.strokeStyle = colore === "#FFFFFF" ? "#111" : "#fff"; ctx.strokeText(t, x, y);
      ctx.fillStyle = colore; ctx.fillText(t, x, y);
      return;
    }
    canvas.setPointerCapture(e.pointerId);
    inizio = [x, y]; prima = istantanea();
    if (strumento === "penna" || strumento === "evidenzia") {
      ctx.globalAlpha = strumento === "evidenzia" ? .35 : 1;
      ctx.strokeStyle = colore; ctx.lineWidth = strumento === "evidenzia" ? spessore() * 6 : spessore();
      ctx.lineCap = ctx.lineJoin = "round"; ctx.beginPath(); ctx.moveTo(x, y);
    }
  };
  canvas.onpointermove = e => {
    if (!inizio) return;
    const [x, y] = punto(e);
    if (strumento === "penna" || strumento === "evidenzia") {
      if (strumento === "evidenzia") { ripristina(prima); ctx.globalAlpha = .35; }
      ctx.lineTo(x, y); ctx.stroke(); return;
    }
    ripristina(prima);
    const [x0, y0] = inizio;
    if (strumento === "freccia") freccia(x0, y0, x, y);
    else {
      ctx.setLineDash(strumento === "riquadro" ? [] : [12, 8]);
      ctx.strokeStyle = strumento === "riquadro" ? colore : "#fff"; ctx.lineWidth = spessore();
      ctx.strokeRect(x0, y0, x - x0, y - y0); ctx.setLineDash([]);
    }
  };
  canvas.onpointerup = e => {
    if (!inizio) return;
    const [x, y] = punto(e), [x0, y0] = inizio;
    inizio = null; ctx.globalAlpha = 1;
    if (strumento === "oscura") { ripristina(prima); oscura(Math.round(x0), Math.round(y0), Math.round(x - x0), Math.round(y - y0)); }
    if (strumento === "ritaglia") {
      ripristina(prima);
      const rx = Math.round(Math.min(x, x0)), ry = Math.round(Math.min(y, y0)), rw = Math.round(Math.abs(x - x0)), rh = Math.round(Math.abs(y - y0));
      if (rw > 10 && rh > 10) { const d = ctx.getImageData(rx, ry, rw, rh); canvas.width = rw; canvas.height = rh; ctx.putImageData(d, 0, 0); }
      else indietro.pop();
    }
  };
  document.addEventListener("keydown", function k(e) {
    if (vistaAttuale !== "modifica") return document.removeEventListener("keydown", k);
    if ((e.ctrlKey || e.metaKey) && e.key === "z") { e.preventDefault(); annulla.click(); }
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); salva(true); }
  });
};

VISTE.benvenuto = async function (box, passo = 0) {
  const stato = await api("/api/primi-passi").catch(() => ({ passi: [], nome: "" }));
  const wrap = el("div", "benvenuto"); const s = el("div", "scheda"); wrap.append(s); box.append(wrap);
  const sfera = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  sfera.setAttribute("viewBox", "0 0 40 40"); sfera.classList.add("sfera-grande");
  sfera.innerHTML = '<use href="#orb"/>';
  const puntini = el("div", "puntini"); for (let i = 0; i < 5; i++) puntini.append(el("i", i <= passo ? "si" : ""));
  const avanti = (...b) => { const a = el("div", "avanti"); a.append(...b); return a; };
  const prossimo = () => apriVista("benvenuto", passo + 1);
  const fine = async (poi) => { await api("/api/profilo", { fatto: true }).catch(() => {}); chiudiVista(); casa(); if (poi) poi(); };
  const parla = t => api("/api/parla", { testo: t }).catch(() => {});

  if (passo === 0) {
    const nome = el("input", "campo"); nome.placeholder = "Il tuo nome"; nome.value = stato.nome || "";
    const ok = bottone("Avanti", async () => { await api("/api/profilo", { nome: nome.value }).catch(() => {}); prossimo(); }, "bottone primo");
    nome.onkeydown = e => { if (e.key === "Enter") ok.click(); };
    s.append(sfera, puntini, el("h1", "", "Ciao, sono Nova."),
             el("p", "sotto", "Sono l'assistente di questo computer: lavoro tutta qui dentro, senza mandare i tuoi dati a nessuno. Come ti chiami?"),
             nome, avanti(ok));
    setTimeout(() => nome.focus(), 50);
    parla("Ciao, sono Nova. Come ti chiami?");
  } else if (passo === 1) {
    const d = await api("/api/impostazioni?parte=voce").catch(() => ({ voce: { voci: [] } }));
    const lista = el("div", "proposte");
    for (const v of d.voce.voci) {
      const b = el("button", "proposta" + (v.id === d.voce.scelta ? "" : " fatta"));
      b.append(el("span", "s", "🗣️"), el("div", "")); b.lastChild.append(el("b", "", v.nome), v.id === d.voce.scelta ? "In uso · tocca per sentirla" : "Tocca per sentirla");
      b.onclick = async () => { await api("/api/impostazioni/voce", { voce: v.id }).catch(() => {}); apriVista("benvenuto", 1); };
      lista.append(b);
    }
    if (!d.voce.voci.length) lista.append(el("p", "sotto", "Su questo computer c'è una sola voce."));
    s.append(sfera, puntini, el("h1", "", `Piacere${stato.nome ? ", " + stato.nome : ""}!`),
             el("p", "sotto", "Che voce preferisci per me? Puoi cambiarla quando vuoi dalle Impostazioni."), lista,
             avanti(bottone("Avanti", prossimo, "bottone primo")));
  } else if (passo === 2) {
    s.append(sfera, puntini, el("h1", "", "Fammi imparare la tua voce"),
             el("p", "sotto", "Leggi ad alta voce le frasi che compaiono: così risponderò solo a te, e non alla TV o a un film. Resta tutto su questo computer, e puoi rifarlo dalle Impostazioni."));
    const zona = el("div", "carta"); s.append(zona);
    await imparaVoce(zona, { nome: stato.nome, fine: prossimo });
    s.append(avanti(bottone("Più tardi", prossimo), bottone("Avanti", prossimo, "bottone primo")));
  } else if (passo === 3) {
    const internet = stato.passi.find(p => p.id === "internet");
    s.append(sfera, puntini, el("h1", "", "Colleghiamoci a internet"));
    if (internet && internet.fatto) {
      s.append(el("p", "sotto", "Sei già collegato. ✓ Posso cercare aggiornamenti e, se vuoi, scaricare modelli AI più bravi."),
               avanti(bottone("Avanti", prossimo, "bottone primo")));
    } else {
      s.append(el("p", "sotto", "Anche senza internet funziono quasi del tutto. Con internet posso aggiornarmi e scaricare modelli più bravi."));
      const reti = el("div", "carta"); reti.append(el("p", "nota", "Cerco le reti Wi-Fi…")); s.append(reti);
      s.append(avanti(bottone("Più tardi", prossimo), bottone("Avanti", prossimo, "bottone primo")));
      const w = (await api("/api/impostazioni?parte=wifi").catch(() => ({ wifi: {} }))).wifi || {};
      reti.replaceChildren();
      if (w.scheda === false) reti.append(el("p", "nota", "Non trovo la scheda Wi-Fi. Puoi collegare il telefono con il cavo USB e attivare il «tethering»."));
      for (const n of (w.reti || []).slice(0, 6)) {
        const r = el("div", "riga-imp"); const c = el("div", "cosa"); c.append(n.protetta ? conIcona("privacy", n.nome) : n.nome);
        const esito = el("small", "", "");
        c.append(esito);
        r.append(c, bottone(n.attiva ? "Collegato" : "Collega", async () => {
          let password = "";
          if (n.protetta && !n.attiva) { password = await chiediPassword(`Password della rete «${n.nome}»`); if (password === null) return; }
          esito.textContent = "Mi collego…";
          const res = await api("/api/impostazioni/wifi", { azione: "collega", nome: n.nome, password }).catch(e => ({ messaggio: e.message }));
          esito.textContent = res.messaggio || "";
          if (res.ok) setTimeout(prossimo, 1200);
        }, n.attiva ? "bottone" : "bottone primo"));
        reti.append(r);
      }
    }
  } else {
    const lista = el("div", "proposte");
    for (const p of stato.passi.filter(p => p.id !== "internet")) {
      const b = el("button", "proposta" + (p.fatto ? " fatta" : ""));
      b.append(el("span", "s"), el("div", "")); b.firstChild.append(svgIcona(ICONA_PASSO[p.id] || "info")); b.lastChild.append(el("b", "", p.titolo + (p.fatto ? " ✓" : "")), p.testo);
      b.onclick = () => fine(() => eseguiPasso(p));
      lista.append(b);
    }
    s.append(sfera, puntini, el("h1", "", "Cosa facciamo insieme?"),
             el("p", "sotto", "Ecco cosa ti propongo per iniziare. Scegline uno, oppure inizia pure: te le ricordo nella schermata."),
             lista, avanti(bottone("Inizia", () => fine(), "bottone primo")));
    parla("Ecco cosa ti propongo per iniziare.");
  }
};

// nella schermata: la carta «Per iniziare» finché c'è qualcosa da fare
window.cartaPrimiPassi = async function () {
  const col = $("destra-col"); if (!col) return;
  const stato = await api("/api/primi-passi").catch(() => null);
  if (!stato) return;
  if (stato.benvenuto && !window._benvenutoMostrato) { window._benvenutoMostrato = true; apriVista("benvenuto"); return; }
  const da_fare = stato.passi.filter(p => !p.fatto);
  if (!da_fare.length) return;
  const c = el("div", "carta"); c.append(el("div", "tipo", "Per iniziare"));
  for (const p of da_fare.slice(0, 4)) {
    const r = el("div", "passo-casa");
    const via = el("button", "via", "✕"); via.title = "Non mi interessa";
    via.onclick = async () => { await api("/api/profilo", { nascondi: p.id }).catch(() => {}); r.remove(); };
    r.append(svgIcona(ICONA_PASSO[p.id] || "info"), el("span", "t", p.titolo), bottone("Fallo", () => eseguiPasso(p), ""), via);
    c.append(r);
  }
  col.prepend(c);
};
