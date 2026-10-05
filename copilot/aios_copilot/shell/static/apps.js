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
const VISTE_VIVE = { impostazioni: { aggiornamenti: 3000, info: 10000, wifi: 8000, bluetooth: 6000, suono: 8000, schermo: 30000, personalizzazioni: 2500 }, attivita: 2000 };
function ogniQuanto() {
  const regole = VISTE_VIVE[vistaAttuale];
  if (!regole) return 0;
  if (typeof regole === "number") return regole;
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
  if (/\.aios$/i.test(f.percorso)) { anteprimaAios = f.percorso; return apriVista("impostazioni", "personalizzazioni"); }
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
  const sfondo = bottone("✂️ Togli sfondo", async () => {
    const f = foto[i]; sfondo.disabled = true; sfondo.textContent = "Ritaglio…";
    const r = await api("/api/file/togli-sfondo", { p: f.percorso }).catch(e => ({ ok: false, messaggio: e.message }));
    sfondo.disabled = false; sfondo.textContent = "✂️ Togli sfondo";
    if (!r.ok) return avviso(r.messaggio || "Non ci sono riuscito.");
    foto.splice(i + 1, 0, { percorso: r.percorso, nome: r.nome }); i++; mostra(); box.classList.add("scacchi");
  }, "modifica togli-sfondo");
  sfondo.title = "Lascia solo il soggetto, su sfondo trasparente (si salva accanto all'originale)";
  box.append(img, did, chiudi, modifica, sfondo);
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
    const SEZ = [["personalizzazioni", "Personalizzazioni"], ["wifi", "Wi-Fi"], ["bluetooth", "Bluetooth"], ["suono", "Suono"], ["schermo", "Schermo"], ["voce", "Voce di Nova"], ["mouse", "Mouse e touchpad"], ["tastiera", "Tastiera"],
                 ["aspetto", "Testo e carattere"], ["accessibilita", "Accessibilità"], ["aggiornamenti", "Aggiornamenti"], ["posta", "Posta"], ["account", "Password"], ["privacy", "Privacy e memoria"],
                 ["cloud", "AI in cloud"], ["info", "Questo computer"], ["energia", "Spegni"]];
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
    const parte = { suono: "suono", schermo: "suono", wifi: "wifi", bluetooth: "bluetooth", voce: "voce", info: "info", aggiornamenti: "info",
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
      pan.append(esito);
    } else if (sezione === "schermo") {
      const cursore = (valore, rotta) => {
        const r = el("input"); r.type = "range"; r.min = 0; r.max = 100; r.value = valore ?? 50; r.disabled = valore == null;
        r.onchange = () => api(`/api/impostazioni/${rotta}`, { livello: +r.value }).catch(() => {}); return r;
      };
      await disegnaMonitor(carta, riga, dici);
      carta(riga(conIcona("luminosita", "Luminosità"), d.luminosita?.livello == null ? "Su un monitor esterno si regola dai tasti del monitor" : null, cursore(d.luminosita?.livello, "luminosita")));
      const ln = await api("/api/luce-notturna").catch(() => null);
      if (ln) {
        const salva = cambio => api("/api/luce-notturna", cambio).then(() => apriVista("impostazioni", "schermo")).catch(e => dici({ ok: false, messaggio: e.message }));
        const c = carta(el("h3", "", "Luce notturna"),
          el("p", "nota", "La sera lo schermo diventa più caldo, con meno luce blu: stanca meno gli occhi e aiuta a prendere sonno."),
          riga("Accendi in automatico", ln.attiva ? (ln.modo === "sole" ? `Dal tramonto all'alba: stasera dalle ${ln.da} alle ${ln.a}` : `Dalle ${ln.inizio} alle ${ln.fine}`) : "Spenta",
               interruttore(ln.attiva, () => salva({ attiva: !ln.attiva }))));
        const modo = el("select", "campo");
        for (const [v, t] of [["sole", "Dal tramonto all'alba"], ["orari", "Orari scelti da me"]]) { const o = el("option", "", t); o.value = v; o.selected = v === ln.modo; modo.append(o); }
        modo.onchange = () => salva({ modo: modo.value });
        c.append(riga("Quando", null, modo));
        if (ln.modo === "orari") {
          const ora = (v, k) => { const i = el("input", "campo"); i.type = "time"; i.value = v; i.style.width = "130px"; i.onchange = () => salva({ [k]: i.value }); return i; };
          c.append(riga("Dalle", null, ora(ln.inizio, "inizio")), riga("Alle", null, ora(ln.fine, "fine")));
        }
        const caldo = el("input"); caldo.type = "range"; caldo.min = 2500; caldo.max = 5500; caldo.step = 100; caldo.value = ln.temperatura;
        caldo.style.direction = "rtl"; caldo.className = "cursore-caldo";
        const kv = el("b", "", `${ln.temperatura} K`);
        caldo.oninput = () => { kv.textContent = `${caldo.value} K`; };
        caldo.onchange = () => salva({ temperatura: +caldo.value });
        c.append(riga("Quanto calda", "Più a destra, più calda", caldo, kv));
        const aMano = !!ln.fino_a && ln.accesa_ora;
        const ctrl = aMano ? [bottone("Spegni", () => salva({ adesso: false }))]
                   : ln.accesa_ora ? [] : [bottone("Accendi fino a domattina", () => salva({ adesso: true }), "bottone primo")];
        c.append(riga("Adesso", ln.accesa_ora ? (aMano ? `Accesa fino alle ${ln.fino_a.slice(11, 16)}` : "Accesa: è sera") : "Spenta in questo momento", ...ctrl));
      }
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
      const lingua = el("select", "campo");
      for (const [id, nome] of Object.entries(k.lingue)) { const o = el("option", "", nome); o.value = id; o.selected = id === k.scelta; lingua.append(o); }
      lingua.onchange = async () => { dici(await api("/api/impostazioni/tastiera", { lingua: lingua.value }).catch(e => ({ ok: false, messaggio: e.message }))); };
      const dv = await api("/api/dispositivi").catch(() => null);
      carta(riga("Lingua della tastiera", "Cambia subito. Puoi anche dire a Nova «metti la tastiera inglese».", lingua));
      if (dv) {
        const salva = cambio => api("/api/dispositivi", cambio).then(() => dici({ ok: true, messaggio: "Fatto." })).catch(e => dici({ ok: false, messaggio: e.message }));
        const cur = (v, min, max, passo, fn) => { const r = el("input"); r.type = "range"; r.min = min; r.max = max; r.step = passo; r.value = v; r.onchange = () => fn(+r.value); return r; };
        carta(el("h3", "", "Tasti"),
              riga("Ritardo prima di ripetere", "Quanto tenere premuto un tasto prima che si ripeta", cur(dv.ripetizione_ritardo, 150, 1000, 50, v => salva({ ripetizione_ritardo: v }))),
              riga("Velocità di ripetizione", null, cur(dv.ripetizione_velocita, 5, 60, 1, v => salva({ ripetizione_velocita: v }))),
              riga("Bloc Num acceso all'avvio", null, interruttore(dv.bloc_num, () => salva({ bloc_num: !dv.bloc_num }).then(() => apriVista("impostazioni", "tastiera")))),
              riga("Prova qui", null, Object.assign(el("input", "campo"), { placeholder: "Scrivi per provare" })));
        const mie = carta(el("h3", "", "Le tue scorciatoie"),
              el("p", "nota", "Una combinazione di tasti apre un programma o fa una richiesta a Nova (es. Super+M → «metti la musica rilassante»)."));
        for (const sc of dv.scorciatoie) mie.append(riga(el("b", "", sc.nome), sc.app ? `Apre ${sc.app}` : `Chiede a Nova «${sc.chiedi}»`,
          bottone("Togli", async () => { await api("/api/scorciatoie", { togli: sc.tasti }); apriVista("impostazioni", "tastiera"); })));
        const tasti = el("input", "campo"); tasti.placeholder = "Premi i tasti (es. Super+M)"; tasti.readOnly = true; tasti.style.width = "200px";
        tasti.onkeydown = e => {
          e.preventDefault(); e.stopPropagation();
          if (["Control", "Alt", "Shift", "Meta", "OS", "Super"].includes(e.key)) return;
          const parti = [e.metaKey && "Super", e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift"].filter(Boolean);
          const nome = e.code.startsWith("Key") ? e.code.slice(3) : e.code.startsWith("Digit") ? e.code.slice(5) : /^F\d+$/.test(e.key) ? e.key : e.key.length === 1 ? e.key.toUpperCase() : e.key;
          tasti.value = [...parti, nome].join("+");
        };
        const cosa = el("input", "campo"); cosa.placeholder = "Cosa fare: «apri Firefox» o una richiesta a Nova"; cosa.style.width = "380px";
        mie.append(riga("Nuova", null, tasti), riga("Cosa fa", null, cosa),
          riga("", null, bottone("Aggiungi", async () => {
            const m = cosa.value.trim().match(/^apri\s+(.+)$/i);
            const corpo = { tasti: tasti.value };
            if (m) { const app = await api("/api/app").then(r => r.app.find(a => a.name.toLowerCase() === m[1].toLowerCase() || a.id.toLowerCase().includes(m[1].toLowerCase()))).catch(() => null);
                     if (app) corpo.app = app.id; else corpo.chiedi = cosa.value; } else corpo.chiedi = cosa.value;
            const r = await api("/api/scorciatoie", corpo).catch(e => ({ ok: false, messaggio: e.message }));
            dici(r); if (r.ok) apriVista("impostazioni", "tastiera");
          }, "bottone primo")));
        const aios = carta(el("h3", "", "Scorciatoie di AIOS"));
        const griglia = el("div", "scorciatoie");
        for (const [t, cosa] of dv.di_aios) griglia.append(el("kbd", "", t), el("span", "", cosa));
        aios.append(griglia);
      }
      pan.append(esito);
    } else if (sezione === "mouse") {
      const dv = await api("/api/dispositivi").catch(() => null);
      if (!dv) { pan.append(el("p", "esito no", "Non riesco a leggere le impostazioni.")); return; }
      const salva = cambio => api("/api/dispositivi", cambio).then(() => apriVista("impostazioni", "mouse")).catch(e => dici({ ok: false, messaggio: e.message }));
      const sw = k => interruttore(dv[k], () => salva({ [k]: !dv[k] }));
      const cur = (v, min, max, passo, fn) => { const r = el("input"); r.type = "range"; r.min = min; r.max = max; r.step = passo; r.value = v; r.onchange = () => fn(+r.value); return r; };
      carta(el("h3", "", "Puntatore"),
            riga("Velocità del puntatore", "Lento a sinistra, veloce a destra", cur(dv.velocita, -1, 1, 0.05, v => salva({ velocita: v }))),
            riga("Accelerazione", "Più veloce quando muovi il mouse in fretta (spenta: precisione costante, meglio per i giochi)", sw("accelerazione")),
            riga("Mano sinistra", "Scambia il tasto destro e sinistro", sw("mano_sinistra")));
      carta(el("h3", "", "Scorrimento"),
            riga("Velocità di scorrimento", null, cur(dv.velocita_scorrimento, 0.2, 3, 0.1, v => salva({ velocita_scorrimento: v }))),
            riga("Scorrimento naturale con il mouse", "La pagina segue la rotellina come sul telefono", sw("scorrimento_naturale_mouse")));
      const tp = carta(el("h3", "", "Touchpad"));
      if (!dv.touchpad) tp.append(el("p", "nota", "Nessun touchpad in questo computer: le scelte valgono se ne colleghi uno."));
      tp.append(riga("Tocca per fare clic", null, sw("tocco_clic")),
                riga("Scorrimento naturale", "Due dita su: la pagina sale, come sul telefono", sw("scorrimento_naturale")),
                riga("Spegni mentre scrivi", "Niente clic per sbaglio col palmo", sw("disattiva_scrivendo")));
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
            riga("Scheda video", i.scheda_video_nota || null, el("span", i.scheda_video_nota ? "avviso-testo" : "", i.scheda_video || "integrata")),
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
      const ap = await api("/api/appunti").catch(() => null);
      if (ap) carta(riga("Cronologia degli appunti", `Super+V mostra le ultime cose copiate (${ap.voci.length} adesso), Super+. le emoji. Le password dei gestori di password non entrano mai.`,
                         interruttore(ap.attivo, () => api("/api/appunti/attivo", { attivo: !ap.attivo }).then(() => apriVista("impostazioni", "privacy")))),
                    riga("Svuota la cronologia degli appunti", "Anche le voci fissate",
                         bottone("Svuota", async () => { dici(await api("/api/appunti/svuota", { tutto: true }).catch(e => ({ ok: false, messaggio: e.message }))); }, "bottone pericolo")));
      pan.append(esito);
    } else if (sezione === "accessibilita") {
      const a = await api("/api/accessibilita").catch(() => null);
      if (!a) { pan.append(el("p", "esito no", "Non riesco a leggere le impostazioni.")); return; }
      const salva = cambio => api("/api/accessibilita", cambio).then(r => { dici(r); window.novaAspetto && window.novaAspetto(); apriVista("impostazioni", "accessibilita"); })
        .catch(e => dici({ ok: false, messaggio: e.message }));
      const sw = k => interruttore(a[k], () => salva({ [k]: !a[k] }));
      carta(el("h3", "", "Vista"),
            riga("Contrasto alto", "Colori pieni e bordi netti, in AIOS e nelle app", sw("contrasto")),
            riga("Puntatore più grande", null, sw("cursore_grande")),
            riga("Meno animazioni", "Niente movimenti e dissolvenze", sw("meno_animazioni")),
            riga("Zoom", "Super e + per ingrandire attorno al puntatore, Super e - per tornare indietro, Super e 0 per normale", el("span", "nota", "")),
            riga("Testo più grande", "In Testo e carattere", bottone("Apri", () => apriVista("impostazioni", "aspetto"))));
      const filtro = el("select", "campo");
      for (const [v, t] of Object.entries(a.filtri)) { const o = el("option", "", t); o.value = v; o.selected = v === a.filtro; filtro.append(o); }
      filtro.onchange = () => salva({ filtro: filtro.value });
      carta(el("h3", "", "Filtri colore"),
            riga("Per chi vede i colori in modo diverso", "Lo schermo corregge i colori che si confondono", filtro));
      carta(el("h3", "", "Udito e lettura"),
            riga("Sottotitoli in tempo reale", "Scrive in basso quello che il PC fa sentire (video, chiamate, giochi). Tutto sul computer, anche in inglese.", sw("sottotitoli")),
            riga("Lettore dello schermo", a.orca ? "Legge ad alta voce quello che c'è sullo schermo (Orca). Super+Alt+S lo accende e lo spegne." : "Orca non è installato in questa versione di AIOS.", sw("lettore")),
            riga("Nova guarda per te", "Chiedi «cosa c'è sullo schermo?» o «cosa dice questo errore?»", el("span", "nota", "")));
      pan.append(esito);
    } else if (sezione === "personalizzazioni") {
      await disegnaPersonalizzazioni(carta, riga, dici, esito, pan);
    } else if (sezione === "cloud") {
      const c = await api("/api/cloud").catch(() => null);
      if (!c) { pan.append(el("p", "esito no", "Non riesco a leggere le impostazioni.")); return; }
      const salva = (cambio) => api("/api/cloud", cambio).then(() => apriVista("impostazioni", "cloud")).catch(e => dici({ ok: false, messaggio: e.message }));
      const num = (v, passo, fn) => { const i = el("input", "campo"); i.type = "number"; i.min = 0; i.step = passo; i.value = v; i.style.width = "90px"; i.onchange = () => fn(+i.value); return i; };
      carta(el("p", "nota", "Un modello grande su internet (OpenRouter: DeepSeek, Claude, Gemini, GPT, Mistral…) per le richieste difficili. "
                          + "Decide Laya, sul PC: comandi, posta, agenda e domande semplici restano qui. Se il modello del PC non risponde bene entro il tempo scelto, la richiesta passa al cloud."),
            riga("Usa l'AI in cloud", c.chiave ? (c.attivo ? "Accesa" : "Spenta") : "Prima serve la chiave di OpenRouter",
                 interruttore(c.attivo, () => c.chiave ? salva({ attivo: !c.attivo }) : dici({ ok: false, messaggio: "Incolla prima la chiave qui sotto." }))));
      const chiave = el("input", "campo"); chiave.type = "password"; chiave.placeholder = c.chiave ? "Chiave impostata ✓ (incolla per cambiarla)" : "sk-or-…";
      carta(el("h3", "", "Chiave di OpenRouter"),
            el("p", "nota", "Si crea su openrouter.ai › Keys (puoi mettere anche lì un limite di spesa). Resta nel portachiavi del PC."),
            riga("Chiave", null, chiave, bottone("Salva", () => salva({ chiave: chiave.value }), "bottone primo")));
      const lista = el("select", "campo"); lista.style.maxWidth = "420px";
      const filtro = el("input", "campo"); filtro.placeholder = "Cerca: claude, gemini, deepseek…"; filtro.style.width = "200px";
      const gratis = el("input"); gratis.type = "checkbox";
      const etichettaGratis = el("label", "nota"); etichettaGratis.append(gratis, " solo gratuiti");
      const modCarta = carta(el("h3", "", "Modello"), el("p", "nota", `In uso: ${c.modello}`), riga("Scegli", "Prezzo per milione di parole (token), in dollari", filtro, etichettaGratis),
            riga("", null, lista, bottone("Usa questo", () => lista.value && salva({ modello: lista.value }), "bottone primo")));
      api("/api/cloud/modelli").then(r => {
        const disegna = () => {
          const f = filtro.value.toLowerCase();
          lista.replaceChildren();
          for (const m of r.modelli.filter(m => (!gratis.checked || m.gratis) && (!f || (m.id + " " + m.nome).toLowerCase().includes(f))).sort((a, b) => b.creato - a.creato).slice(0, 150)) {
            const o = el("option", "", `${m.nome} — ${m.gratis ? "gratis" : `${m.ingresso} / ${m.uscita} $`}`); o.value = m.id;
            if (m.id === c.modello) o.selected = true; lista.append(o);
          }
        };
        filtro.oninput = disegna; gratis.onchange = disegna; disegna();
      }).catch(() => modCarta.append(el("p", "nota", "Non riesco a leggere l'elenco dei modelli (serve internet).")));
      const priv = el("select", "campo");
      for (const [v, t] of [["chiedi", "Chiedimi ogni volta"], ["mai", "Mai: restano sul PC"], ["sempre", "Sempre, senza chiedere"]]) {
        const o = el("option", "", t); o.value = v; if (v === c.privacy) o.selected = true; priv.append(o);
      }
      priv.onchange = () => salva({ privacy: priv.value });
      carta(el("h3", "", "Limiti e privacy"),
            riga("Spesa massima al giorno ($)", `Oggi: ${c.oggi.toFixed(2)} $ in ${c.richieste} richieste`, num(c.limite_giorno, 0.5, v => salva({ limite_giorno: v }))),
            riga("Spesa massima al mese ($)", `Questo mese: ${c.mese.toFixed(2)} $`, num(c.limite_mese, 1, v => salva({ limite_mese: v }))),
            riga("Secondi al modello del PC", "Poi passa al cloud (0 = mai)", num(c.attesa_locale, 1, v => salva({ attesa_locale: v }))),
            riga("Dati privati (mail, documenti)", "Se una richiesta li contiene", priv));
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

// --- Gestione attività: chi consuma, lo stato della macchina e dell'AI -------------------------------------
const STORIA_ATTIVITA = { cpu: [], gpu: [], mem: [] };  // l'ultimo minuto e mezzo, per i grafici
let filtroAttivita = "";
const mb = n => n >= 1024 ? `${(n / 1024).toFixed(1).replace(".", ",")} GB` : `${n} MB`;
const gradi = g => g == null ? "" : `${Math.round(g)} °C`;
const caldo = (g, soglia) => g == null ? "" : g >= soglia ? " rosso" : g >= soglia - 12 ? " giallo" : "";
function grafico(valori, colore) {  // una linea con l'area sotto, da 0 a 100
  const ns = "http://www.w3.org/2000/svg", w = 240, h = 56;
  const s = document.createElementNS(ns, "svg"); s.setAttribute("viewBox", `0 0 ${w} ${h}`); s.setAttribute("class", "grafico");
  s.setAttribute("preserveAspectRatio", "none");
  // allineata a destra: il punto più nuovo sul bordo, la storia scorre verso sinistra
  const x0 = w - ((valori.length - 1) / 44) * w;
  const pts = valori.map((v, i) => `${x0 + (i / 44) * w},${h - (Math.min(100, v) / 100) * (h - 4) - 2}`);
  if (pts.length > 1) {
    const area = document.createElementNS(ns, "path");
    area.setAttribute("d", `M${x0},${h} L${pts.join(" L")} L${w},${h} Z`);
    area.setAttribute("fill", colore); area.setAttribute("fill-opacity", ".16");
    const line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", pts.join(" ")); line.setAttribute("fill", "none"); line.setAttribute("stroke", colore);
    line.setAttribute("stroke-width", "2"); line.setAttribute("vector-effect", "non-scaling-stroke"); line.setAttribute("stroke-linejoin", "round");
    s.append(area, line);
  }
  return s;
}
function barra(valore, cls = "") { const b = el("div", "barra-uso" + cls); const f = el("i"); f.style.width = `${Math.max(0, Math.min(100, valore))}%`; b.append(f); return b; }

VISTE.attivita = async function (box) {
  const corpo = testa(box, "Gestione attività");
  let d;
  try { d = await api("/api/attivita"); }
  catch (e) { corpo.append(el("p", "vuoto", `Non riesco a leggere lo stato del computer (${e.message}).`)); return; }
  const g0 = (d.schede_video || [])[0];
  for (const [k, v] of [["cpu", d.processore.uso], ["gpu", g0?.uso ?? 0], ["mem", 100 * d.memoria.usata_mb / Math.max(1, d.memoria.totale_mb)]]) {
    STORIA_ATTIVITA[k].push(v); if (STORIA_ATTIVITA[k].length > 45) STORIA_ATTIVITA[k].shift();
  }
  const esito = el("div", "esito");
  const dici = r => { esito.textContent = r.messaggio || (r.ok ? "Fatto." : "Non è riuscito."); esito.className = "esito " + (r.ok ? "ok" : "no"); };

  // in alto: processore, memoria, scheda video, rete
  const quadri = el("div", "quadri-attivita");
  const quadro = (icona, titolo, grande, sotto, storia, colore, extra) => {
    const q = el("div", "carta quadro");
    const t = el("div", "quadro-testa"); t.append(svgIcona(icona), el("span", "", titolo));
    q.append(t, el("div", "quadro-valore", grande), el("small", "quadro-sotto", sotto));
    if (storia) q.append(grafico(storia, colore));
    if (extra) q.append(extra);
    quadri.append(q); return q;
  };
  const p = d.processore;
  const core = el("div", "core");
  for (const c of p.core || []) { const i = el("i"); i.style.height = `${Math.max(4, c)}%`; i.title = `${Math.round(c)}%`; core.append(i); }
  quadro("chip", "Processore", `${Math.round(p.uso)}%`, [p.gradi != null ? gradi(p.gradi) : "", `${(p.core || []).length} core`].filter(Boolean).join(" · "),
         STORIA_ATTIVITA.cpu, "#2EC4B6", core).classList.add(...caldo(p.gradi, 90).trim().split(" ").filter(Boolean));
  const m = d.memoria;
  quadro("pacco", "Memoria", mb(m.usata_mb), `su ${mb(m.totale_mb)}` + (m.scambio_mb > 100 ? ` · ${mb(m.scambio_mb)} sul disco` : ""),
         STORIA_ATTIVITA.mem, "#7B6CF6");
  for (const g of d.schede_video || []) {
    const sotto = !g.driver ? "Driver non caricato" : [g.gradi != null ? gradi(g.gradi) : "", g.memoria_totale ? `${mb(g.memoria_usata)} / ${mb(g.memoria_totale)}` : "",
                  g.ventola != null ? `ventola ${g.ventola}%` : "", g.watt ? `${Math.round(g.watt)} W` : ""].filter(Boolean).join(" · ");
    const q = quadro("monitor", g.nome, g.uso != null ? `${g.uso}%` : (g.driver ? "—" : "!"), sotto || "uso non leggibile", g === g0 && g.uso != null ? STORIA_ATTIVITA.gpu : null, "#F2A65A");
    if (!g.driver) q.classList.add("rosso"); else if (caldo(g.gradi, 87)) q.classList.add(caldo(g.gradi, 87).trim());
  }
  quadro("internet", "Rete", `↓ ${d.rete.giu_kbs >= 1024 ? (d.rete.giu_kbs / 1024).toFixed(1) + " MB/s" : d.rete.giu_kbs + " KB/s"}`,
         `↑ ${d.rete.su_kbs >= 1024 ? (d.rete.su_kbs / 1024).toFixed(1) + " MB/s" : d.rete.su_kbs + " KB/s"} · acceso da ${durata(d.acceso_da)}`);
  corpo.append(quadri);

  for (const c of d.consigli || []) { const a = el("div", "carta consiglio"); a.append(svgIcona("avviso"), el("span", "", c)); corpo.append(a); }

  // l'AI: i modelli caricati e dove stanno
  const due = el("div", "due-colonne"); corpo.append(due);
  const ai = el("div", "carta"); due.append(ai);
  ai.append(el("h3", "", "Intelligenza artificiale"));
  const mod = d.ai.modelli;
  if (mod === null) ai.append(el("p", "nota", "Il motore dei modelli (Ollama) non risponde: Nova usa solo i comandi veloci o il cloud."));
  else if (!mod.length) ai.append(el("p", "nota", "Nessun modello caricato adesso: si carica da solo alla prossima domanda a Nova."));
  for (const x of mod || []) {
    const r = el("div", "modello-ai");
    const testo = el("div", "cosa"); testo.append(el("b", "", x.nome), el("small", "", `${mb(x.memoria_mb)} · ${x.in_gpu === 100 ? "tutto nella scheda video" : x.in_gpu === 0 ? "tutto nel processore" : `${x.in_gpu}% nella scheda video, il resto nel processore`}`));
    const split = el("div", "split-ai"); const gpu = el("i", "gpu"); gpu.style.width = `${x.in_gpu}%`; split.append(gpu); split.title = "Turchese: scheda video · Grigio: memoria normale";
    r.append(testo, split, bottone("Togli dalla memoria", async () => { dici(await api("/api/attivita/modello", { nome: x.nome }).catch(e => ({ ok: false, messaggio: e.message }))); }));
    ai.append(r);
  }
  ai.append(el("p", "nota", `Nova e i servizi di AIOS adesso: ${Math.round(d.ai.nova_cpu)}% del processore, ${mb(d.ai.nova_mb)} di memoria.`));
  const azioniAi = el("div", "azioni");
  azioniAi.append(bottone("Modelli consigliati per questo PC", () => { chiudiVista(); chiedi("quali modelli AI mi consigli?"); }),
                  bottone("AI in cloud", () => apriVista("impostazioni", "cloud")));
  ai.append(azioniAi);

  // temperature e ventole
  const sen = el("div", "carta"); due.append(sen);
  sen.append(el("h3", "", "Temperature e ventole"));
  const s = d.sensori;
  const riga = (icona, nome, valore, cls = "") => { const r = el("div", "riga-sensore" + cls); r.append(svgIcona(icona), el("span", "", nome), el("b", "", valore)); sen.append(r); };
  if (s.cpu != null) riga("termometro", "Processore", gradi(s.cpu), caldo(s.cpu, 90));
  for (const g of d.schede_video || []) if (g.gradi != null) riga("termometro", g.nome, gradi(g.gradi), caldo(g.gradi, 87));
  if (s.disco != null) riga("termometro", "Disco", gradi(s.disco), caldo(s.disco, 70));
  if (s.scheda_madre != null) riga("termometro", "Scheda madre", gradi(s.scheda_madre), caldo(s.scheda_madre, 80));
  for (const f of s.ventole) riga("ventola", f.nome, f.giri ? `${f.giri} giri/min` : "ferma");
  for (const g of d.schede_video || []) if (g.ventola != null) riga("ventola", `Ventola di ${g.nome}`, `${g.ventola}%`);
  if (!s.ventole.length) sen.append(el("p", "nota", "Le ventole della scheda madre non sono leggibili su questo PC (manca il sensore nel sistema)."));

  // i programmi
  const prog = el("div", "carta"); corpo.append(prog);
  const cerca = el("input", "campo"); cerca.placeholder = "Cerca un programma"; cerca.value = filtroAttivita;
  const t = el("div", "testa-tabella"); t.append(el("h3", "", "Programmi"), cerca); prog.append(t);
  const tab = el("div", "tabella-attivita"); prog.append(tab);
  const intest = el("div", "riga-attivita intestazione"); intest.append(el("span", "", "Nome"), el("span", "", "Processore"), el("span", "", "Memoria"), el("span", "", ""));
  const disegna = () => {
    const f = filtroAttivita.toLowerCase();
    tab.replaceChildren(intest);
    for (const x of d.programmi.filter(x => !f || x.nome.toLowerCase().includes(f)).slice(0, 40)) {
      const r = el("div", "riga-attivita" + (x.ai ? " ai" : ""));
      const nome = el("span", "nome"); nome.append(el("b", "", x.nome));
      if (x.ai) nome.append(el("em", "etichetta-ai", "AI"));
      if (x.processi > 1) nome.append(el("small", "", `${x.processi} processi`));
      const cpu = el("span", "num"); cpu.append(el("b", "", `${x.cpu.toFixed(1).replace(".", ",")}%`), barra(x.cpu * 1, x.cpu > 60 ? " alto" : ""));
      const mem = el("span", "num"); mem.append(el("b", "", mb(x.memoria_mb)), barra(100 * x.memoria_mb / Math.max(1, d.memoria.totale_mb) * 4));
      const az = el("span", "");
      if (x.chiudibile) az.append(bottone("Chiudi", async () => {
        if (!await chiediConferma(`Chiudo a forza ${x.nome}? Il lavoro non salvato in quel programma si perde.`)) return;
        dici(await api("/api/attivita/chiudi", { nome: x.nome, pid: x.pid }).catch(e => ({ ok: false, messaggio: e.message })));
        apriVista("attivita");
      }, "bottone pericolo piccolo"));
      r.append(nome, cpu, mem, az); tab.append(r);
    }
  };
  cerca.oninput = () => { filtroAttivita = cerca.value; disegna(); };
  disegna();
  prog.append(el("p", "nota", "I programmi di sistema e quelli di AIOS non si chiudono da qui. Puoi anche dire a Nova: «cosa rallenta il PC?» o «chiudi a forza Steam»."));
  corpo.append(esito);
};
function durata(sec) {
  const g = Math.floor(sec / 86400), o = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  return g ? `${g} g ${o} h` : o ? `${o} h ${m} min` : `${m} min`;
}

VISTE.personalizzazioni = box => VISTE.impostazioni(box, "personalizzazioni");

// --- Calendario: il mese, la giornata scelta, le cose da fare --------------------------------------------
const SETTIMANA = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"];  // MESI e GIORNI sono in home.html
const isoGiorno = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
let meseCal = null, giornoCal = null;
VISTE.calendario = async function (box) {
  const oggi = new Date();
  meseCal = meseCal || new Date(oggi.getFullYear(), oggi.getMonth(), 1);
  giornoCal = giornoCal || isoGiorno(oggi);
  const inizio = new Date(meseCal); inizio.setDate(1 - ((meseCal.getDay() + 6) % 7));  // dal lunedì
  const fine = new Date(inizio); fine.setDate(inizio.getDate() + 42);
  const vai = delta => { meseCal = new Date(meseCal.getFullYear(), meseCal.getMonth() + delta, 1); apriVista("calendario"); };
  const nav = el("span", "nav-mese");
  nav.append(bottone("‹", () => vai(-1), "torna"), el("b", "", `${MESI[meseCal.getMonth()]} ${meseCal.getFullYear()}`), bottone("›", () => vai(1), "torna"),
             bottone("Oggi", () => { meseCal = null; giornoCal = null; apriVista("calendario"); }));
  const corpo = testa(box, "Calendario", [nav, bottone("＋ Nuovo", () => nuovoEvento(giornoCal), "bottone primo")]);
  let d;
  try { d = await api(`/api/calendario?da=${isoGiorno(inizio)}&a=${isoGiorno(fine)}`); }
  catch (e) { corpo.append(el("p", "vuoto", `Non riesco a leggere l'agenda (${e.message}).`)); return; }
  const perGiorno = {};
  for (const v of d.voci) (perGiorno[v.quando.slice(0, 10)] ||= []).push(v);
  const wrap = el("div", "calendario"); corpo.append(wrap);
  const griglia = el("div", "mese");
  for (const g of SETTIMANA) griglia.append(el("div", "nome-giorno", g));
  for (let i = 0; i < 42; i++) {
    const g = new Date(inizio); g.setDate(inizio.getDate() + i);
    const iso = isoGiorno(g), voci = perGiorno[iso] || [];
    const c = el("button", "giorno" + (g.getMonth() !== meseCal.getMonth() ? " fuori" : "") + (iso === isoGiorno(oggi) ? " oggi" : "") + (iso === giornoCal ? " scelto" : ""));
    c.append(el("span", "num-giorno", String(g.getDate())));
    for (const v of voci.slice(0, 3)) {
      const e = el("span", "evento-mini " + v.tipo, (v.tutto_il_giorno || !v.quando ? "" : v.quando.slice(11, 16) + " ") + v.titolo);
      c.append(e);
    }
    if (voci.length > 3) c.append(el("small", "altri", `+${voci.length - 3}`));
    c.onclick = () => { giornoCal = iso; apriVista("calendario"); };
    c.ondblclick = () => nuovoEvento(iso);
    griglia.append(c);
  }
  wrap.append(griglia);
  // a destra: la giornata scelta e le cose da fare
  const lato = el("div", "lato-calendario"); wrap.append(lato);
  const gs = new Date(giornoCal + "T12:00");
  const carta = el("div", "carta"); lato.append(carta);
  const titoloGiorno = `${GIORNI[gs.getDay()]} ${gs.getDate()} ${MESI[gs.getMonth()]}`;
  carta.append(el("h3", "", titoloGiorno[0].toUpperCase() + titoloGiorno.slice(1)));
  const delGiorno = perGiorno[giornoCal] || [];
  if (!delGiorno.length) carta.append(el("p", "nota", "Niente in programma. Doppio clic su un giorno per aggiungere, o chiedi a Nova."));
  for (const v of delGiorno) carta.append(vocePlan(v));
  const af = el("div", "azioni"); af.append(bottone("＋ Aggiungi in questo giorno", () => nuovoEvento(giornoCal))); carta.append(af);
  if (d.scaduti.length || d.da_fare.length) {
    const fare = el("div", "carta"); lato.append(fare);
    fare.append(el("h3", "", "Da fare"));
    for (const v of [...d.scaduti, ...d.da_fare]) fare.append(vocePlan(v, true));
  }
  const nova = el("div", "carta suggerimento"); lato.append(nova);
  nova.append(el("p", "nota", "Puoi anche dire a Nova: «cosa ho domani?», «sposta il dentista a giovedì», «metti in agenda la riunione che mi ha proposto Giulia»."));
};
function vocePlan(v, daFare = false) {
  const r = el("div", "voce-plan " + v.tipo + (daFare && v.quando ? " scaduta" : ""));
  const ora = v.tutto_il_giorno ? "tutto il giorno" : v.quando ? (daFare ? new Date(v.quando).toLocaleDateString("it-IT", { day: "numeric", month: "short" }) + " " : "") + v.quando.slice(11, 16) : "";
  const testo = el("div", "cosa"); testo.append(el("b", "", v.titolo), el("small", "", [ora, v.luogo, { daily: "ogni giorno", weekly: "ogni settimana", monthly: "ogni mese", yearly: "ogni anno" }[v.ripeti] || ""].filter(Boolean).join(" · ")));
  const azioni = el("span", "azioni-voce");
  if (v.tipo === "reminder") azioni.append(bottone("✓", async () => { await api("/api/calendario/fatto", { id: v.id }); apriVista("calendario"); }, "tondo piccolo"));
  azioni.append(bottone("✕", async () => {
    if (!await chiediConferma(`Tolgo «${v.titolo}»${v.ripeti ? " (tutte le ripetizioni)" : ""}?`)) return;
    await api("/api/calendario/togli", { tipo: v.tipo, id: v.id }); apriVista("calendario");
  }, "tondo piccolo"));
  r.append(el("i", "segno"), testo, azioni);
  return r;
}
function nuovoEvento(giorno) {
  const fondo = el("div"); fondo.style.cssText = "position:fixed;inset:0;z-index:50;background:rgba(3,12,18,.55);display:grid;place-items:center";
  const c = el("div", "carta modulo"); c.style.cssText = "width:min(480px,92vw)";
  const campo = (etichetta, input) => { const l = el("label", "campo-modulo"); l.append(el("span", "", etichetta), input); c.append(l); return input; };
  c.append(el("h3", "", "Nuovo in agenda"));
  const titolo = campo("Cosa", Object.assign(el("input", "campo"), { placeholder: "Dentista, compleanno di Sara…" }));
  const g = campo("Giorno", Object.assign(el("input", "campo"), { type: "date", value: giorno }));
  const ora = campo("Ora (vuota = tutto il giorno)", Object.assign(el("input", "campo"), { type: "time" }));
  const luogo = campo("Dove", Object.assign(el("input", "campo"), { placeholder: "facoltativo" }));
  const rip = el("select", "campo");
  for (const [v, t] of [["", "Una volta"], ["daily", "Ogni giorno"], ["weekly", "Ogni settimana"], ["monthly", "Ogni mese"], ["yearly", "Ogni anno"]]) { const o = el("option", "", t); o.value = v; rip.append(o); }
  campo("Ripeti", rip);
  const prom = el("input"); prom.type = "checkbox"; const lp = el("label", "nota"); lp.append(prom, " È un promemoria (te lo ricordo con un avviso)"); c.append(lp);
  const esito = el("div", "esito");
  const a = el("div", "piede-modulo");
  a.append(bottone("Annulla", () => fondo.remove()), bottone("Aggiungi", async () => {
    const r = await api("/api/calendario/nuovo", { titolo: titolo.value, giorno: g.value, ora: ora.value, luogo: luogo.value, ripeti: rip.value, promemoria: prom.checked })
      .catch(e => ({ ok: false, messaggio: e.message }));
    if (r.ok) { fondo.remove(); giornoCal = g.value; const nd = new Date(g.value + "T12:00"); meseCal = new Date(nd.getFullYear(), nd.getMonth(), 1); apriVista("calendario"); }
    else { esito.textContent = r.messaggio || r.error || "Non è riuscito."; esito.className = "esito no"; }
  }, "bottone primo"));
  c.append(esito, a); fondo.append(c); document.body.append(fondo);
  fondo.onkeydown = e => { e.stopPropagation(); if (e.key === "Escape") fondo.remove(); };
  setTimeout(() => titolo.focus(), 30);
}

// --- Rubrica: telefono, posta e contatti di AIOS insieme ------------------------------------------------------
let filtroRubrica = "", sceltoRubrica = "";
VISTE.rubrica = async function (box) {
  const cerca = el("input", "campo"); cerca.placeholder = "Cerca nome, numero o email"; cerca.value = filtroRubrica; cerca.style.maxWidth = "320px";
  const corpo = testa(box, "Rubrica", [cerca, bottone("＋ Nuovo contatto", () => modificaContatto(), "bottone primo")]);
  let d;
  try { d = await api("/api/rubrica"); }
  catch (e) { corpo.append(el("p", "vuoto", `Non riesco a leggere la rubrica (${e.message}).`)); return; }
  if (!d.contatti.length) {
    corpo.append(el("p", "vuoto", "La rubrica è vuota. Si riempie da sola collegando il telefono e la posta, oppure aggiungi un contatto qui o dicendo a Nova: «aggiungi Mario Rossi alla rubrica, 333 1234567»."));
    return;
  }
  const wrap = el("div", "rubrica"); corpo.append(wrap);
  const elenco = el("div", "elenco-contatti"); const scheda = el("div", "scheda-contatto");
  wrap.append(elenco, scheda);
  const iniziali = n => n.split(/\s+/).filter(Boolean).slice(0, 2).map(p => p[0].toUpperCase()).join("");
  const tinta = n => { let h = 0; for (const ch of n) h = (h * 31 + ch.charCodeAt(0)) % 360; return `hsl(${h} 55% 48%)`; };
  const avatar = (n, cls = "avatar") => { const a = el("span", cls, iniziali(n)); a.style.background = tinta(n); return a; };
  const mostra = c => {
    sceltoRubrica = c.nome;
    elenco.querySelectorAll(".contatto").forEach(b => b.classList.toggle("attivo", b.dataset.nome === c.nome));
    scheda.replaceChildren();
    const t = el("div", "carta");
    const testaC = el("div", "testa-contatto"); testaC.append(avatar(c.nome, "avatar grande"), el("h3", "", c.nome));
    t.append(testaC);
    const fonti = { telefono: "dal telefono", posta: "dalla posta", aios: "aggiunto in AIOS" };
    t.append(el("p", "nota", c.fonti.map(f => fonti[f] || f).join(" · ") + (c.mail_scambiate ? ` · ${c.mail_scambiate} mail scambiate` : "")));
    const riga = (icona, valore, ...btn) => { const r = el("div", "riga-imp"); const v = el("div", "cosa"); v.append(conIcona(icona, valore)); r.append(v, ...btn); t.append(r); };
    for (const n of c.telefoni) riga("telefono", n, bottone("Chiama", () => { chiudiVista(); chiedi(`chiama ${c.nome}`); }), bottone("SMS", () => { chiudiVista(); chiedi(`scrivi un sms a ${c.nome}`); }));
    for (const e of c.email) riga("posta", e, bottone("Scrivi", () => { chiudiVista(); chiedi(`scrivi una mail a ${e}`); }));
    if (c.compleanno) riga("calendario", "Compleanno: " + new Date((c.compleanno.startsWith("--") ? "2000" + c.compleanno.slice(1) : c.compleanno) + "T12:00").toLocaleDateString("it-IT", { day: "numeric", month: "long" }));
    if (c.note) t.append(el("p", "", c.note));
    const az = el("div", "azioni");
    if (c.email.length) az.append(bottone("Le ultime mail", () => { chiudiVista(); chiedi(`mostrami le ultime mail di ${c.nome}`); }));
    az.append(bottone("Modifica", () => modificaContatto(c)));
    if (c.fonti.includes("aios")) az.append(bottone("Togli", async () => {
      if (!await chiediConferma(`Tolgo ${c.nome} dalla rubrica di AIOS?`)) return;
      await api("/api/rubrica/togli", { nome: c.nome }); sceltoRubrica = ""; apriVista("rubrica");
    }, "bottone pericolo"));
    t.append(az); scheda.append(t);
  };
  const disegna = () => {
    const f = filtroRubrica.toLowerCase().trim();
    elenco.replaceChildren();
    let lettera = "";
    const trovati = d.contatti.filter(c => !f || c.nome.toLowerCase().includes(f) || c.email.some(e => e.includes(f)) || c.telefoni.some(t => t.replace(/\D/g, "").includes(f.replace(/\D/g, "") || "§")));
    for (const c of trovati) {
      const l = (c.nome[0] || "#").toUpperCase();
      if (l !== lettera) { lettera = l; elenco.append(el("div", "lettera", l)); }
      const b = el("button", "contatto"); b.dataset.nome = c.nome;
      const tx = el("span", "cosa"); tx.append(el("b", "", c.nome), el("small", "", c.telefoni[0] || c.email[0] || ""));
      b.append(avatar(c.nome), tx); b.onclick = () => mostra(c); elenco.append(b);
    }
    if (!trovati.length) elenco.append(el("p", "nota", "Nessun contatto trovato."));
    const scelto = trovati.find(c => c.nome === sceltoRubrica) || trovati[0];
    if (scelto) mostra(scelto); else scheda.replaceChildren();
  };
  cerca.oninput = () => { filtroRubrica = cerca.value; disegna(); };
  disegna();
};
function modificaContatto(c = { nome: "", telefoni: [], email: [], compleanno: "", note: "" }) {
  const fondo = el("div"); fondo.style.cssText = "position:fixed;inset:0;z-index:50;background:rgba(3,12,18,.55);display:grid;place-items:center";
  const k = el("div", "carta modulo"); k.style.cssText = "width:min(460px,92vw)";
  const campo = (etichetta, input) => { const l = el("label", "campo-modulo"); l.append(el("span", "", etichetta), input); k.append(l); return input; };
  k.append(el("h3", "", c.nome ? `Modifica ${c.nome}` : "Nuovo contatto"));
  const nome = campo("Nome e cognome", Object.assign(el("input", "campo"), { value: c.nome }));
  const tel = campo("Telefono", Object.assign(el("input", "campo"), { type: "tel", placeholder: c.telefoni.join(", ") || "333 1234567" }));
  const mail = campo("Email", Object.assign(el("input", "campo"), { type: "email", placeholder: c.email.join(", ") || "nome@esempio.it" }));
  const comp = campo("Compleanno", Object.assign(el("input", "campo"), { type: "date", value: /^\d{4}-/.test(c.compleanno) ? c.compleanno : "" }));
  const note = campo("Note", Object.assign(el("input", "campo"), { value: c.note }));
  const esito = el("div", "esito");
  const a = el("div", "piede-modulo");
  a.append(bottone("Annulla", () => fondo.remove()), bottone("Salva", async () => {
    const r = await api("/api/rubrica", { nome: nome.value, telefono: tel.value, email: mail.value, compleanno: comp.value, note: note.value }).catch(e => ({ ok: false, messaggio: e.message }));
    if (r.ok) { fondo.remove(); sceltoRubrica = nome.value.trim(); apriVista("rubrica"); }
    else { esito.textContent = r.messaggio; esito.className = "esito no"; }
  }, "bottone primo"));
  k.append(esito, a); fondo.append(k); document.body.append(fondo);
  fondo.onkeydown = e => { e.stopPropagation(); if (e.key === "Escape") fondo.remove(); };
  setTimeout(() => nome.focus(), 30);
}

// --- Personalizzazioni: AIOS che si riprogramma su richiesta (codice.py, programmatore.py) -------------------
let anteprimaAios = "";  // un file .aios aperto da File: si mostra subito l'anteprima
async function disegnaPersonalizzazioni(carta, riga, dici, esito, pan) {
  const d = await api("/api/personalizzazioni").catch(e => ({ errore: e.message, modifiche: [], ricevute: [] }));
  const fai = (corpo, ricarica = true) => api("/api/personalizzazioni", corpo).then(r => { dici(r); if (ricarica) apriVista("impostazioni", "personalizzazioni"); return r; })
    .catch(e => dici({ ok: false, messaggio: e.message }));
  if (!d.git) { carta(el("p", "nota", "Per le personalizzazioni serve git, che manca in questa versione di AIOS.")); return; }
  // stato straordinario: modalità sicura o conflitto con un aggiornamento
  if (d.guasto) carta(riga(conIcona("avviso", "AIOS è ripartito originale"), "Una personalizzazione impediva alla schermata di partire. Puoi riprovare o togliere l'ultima.",
                           bottone("Riprova", () => fai({ azione: "riprova" })), bottone("Togli l'ultima", () => fai({ azione: "annulla", id: "ultima" }), "bottone pericolo")));
  if (d.conflitto) carta(riga(conIcona("avviso", "Personalizzazioni da rifare"), "La nuova versione di AIOS cambia le stesse parti: per ora uso AIOS originale. Chiedi a Nova di rifarle."));
  // chiedere una modifica
  const testo = el("textarea", "campo richiesta-aios"); testo.rows = 3;
  testo.placeholder = "Cosa vuoi cambiare di AIOS? Es. «voglio l'orologio rotondo», «la barra in basso», «nella Gestione attività mostrami anche i dischi»";
  const lav = d.lavoro || {};
  const chiedi = bottone(lav.in_corso ? "Nova sta lavorando…" : "Chiedi a Nova", async () => {
    if (!testo.value.trim()) return;
    const r = await api("/api/personalizzazioni/chiedi", { richiesta: testo.value }).catch(e => ({ ok: false, messaggio: e.message }));
    if (r.ok === false) dici(r); else apriVista("impostazioni", "personalizzazioni");
  }, "bottone primo");
  chiedi.disabled = !!lav.in_corso;
  const esempi = el("div", "esempi-aios");
  for (const e of ["Voglio l'orologio rotondo", "Metti la barra in basso", "Icone del dock più grandi", "Un widget con il conto alla rovescia per le vacanze"]) {
    const b = el("button", "chip-aios", e); b.onclick = () => { testo.value = e; testo.focus(); }; esempi.append(b);
  }
  const az = el("div", "piede-modulo"); az.append(chiedi);
  carta(el("h3", "", "AIOS come lo vuoi tu"),
        el("p", "nota", "Chiedi qualsiasi cambiamento: Nova modifica il codice di AIOS, controlla che funzioni e lo applica. Ogni modifica si può togliere o dare a un altro utente; il sistema originale resta sempre intatto. Le modifiche complesse vanno meglio con l'AI in cloud accesa."),
        testo, esempi, az);
  if (lav.in_corso || lav.esito) {
    const c = carta(el("h3", "", lav.in_corso ? "Nova sta modificando AIOS…" : (lav.esito.ok ? "Fatto" : "Non è riuscito")));
    if (lav.in_corso) c.append(el("div", "barra-lavoro"));
    const passi = el("ol", "passi-aios");
    for (const p of (lav.passi || []).slice(-8)) passi.append(el("li", "", p.replace(/^cerca/, "🔎 cerco").replace(/^leggi/, "📖 leggo").replace(/^modifica_file/, "✏️ modifico")
      .replace(/^scrivi_file/, "📝 creo").replace(/^controlla/, "✅ controllo").replace(/^elenca/, "📂 guardo").replace(/^fatto/, "🏁 finito")));
    c.append(passi);
    if (lav.esito) c.append(el("p", lav.esito.ok ? "esito ok" : "esito no", lav.esito.messaggio + (lav.esito.ok ? " La schermata si ricarica tra pochi secondi." : "")));
  }
  if (d.in_attesa) carta(riga(conIcona("avviso", "Una modifica aspetta il tuo sì"), `«${d.in_attesa.richiesta}»: usa internet, comandi di sistema o cancella file.`,
                              bottone("Applica", () => fai({ azione: "applica" }), "bottone primo"), bottone("Scarta", () => fai({ azione: "scarta" }))));
  // le personalizzazioni fatte
  const lista = carta(el("h3", "", `Le tue personalizzazioni${d.modifiche.length ? ` (${d.modifiche.length})` : ""}`));
  if (!d.modifiche.length) lista.append(el("p", "nota", "Nessuna: stai usando AIOS originale."));
  for (const m of d.modifiche) {
    const quando = new Date(m.quando * 1000).toLocaleDateString("it-IT", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
    const r = riga(el("b", "", m.richiesta), `${quando}${m.dettagli ? " · " + m.dettagli.split("\n")[0].slice(0, 160) : ""}`,
      bottone("Condividi", () => fai({ azione: "esporta", id: m.id }, false)),
      bottone("Togli", async () => { if (await chiediConferma(`Tolgo «${m.richiesta}»?`)) fai({ azione: "annulla", id: m.id }); }, "bottone pericolo"));
    lista.append(r);
  }
  // ricevute da altri
  const ric = carta(el("h3", "", "Ricevute da altri"),
    el("p", "nota", "Un file .aios che ti hanno dato (da chiavetta, mail, Schermo AIOS…): mettilo in Scaricati o in Personalizzazioni, oppure aprilo da File."));
  if (!d.ricevute.length) ric.append(el("p", "nota", "Nessun file .aios trovato."));
  const prev = el("div", "anteprima-aios");
  const mostra = async p => {
    const a = await api("/api/personalizzazioni", { azione: "anteprima", p }).catch(e => ({ ok: false, messaggio: e.message }));
    prev.replaceChildren();
    if (!a.ok) return prev.append(el("p", "esito no", a.messaggio));
    prev.append(el("h3", "", a.richiesta), el("p", "nota", `${a.riassunto || ""} · cambia: ${a.file.join(", ")} · fatta su AIOS ${a.versione_aios || "?"}`));
    if (a.rischi.length) prev.append(el("p", "esito no", "Attenzione, usa internet, comandi o cancellazioni: " + a.rischi.slice(0, 3).join(" · ")));
    const pre = el("pre", "diff-aios");
    for (const l of a.righe.slice(0, 80)) pre.append(el("span", l[0] === "+" ? "piu" : "meno", l + "\n"));
    prev.append(pre);
    const b = el("div", "piede-modulo");
    b.append(bottone("Applica alla mia AIOS", () => fai({ azione: "importa", p }), "bottone primo"));
    prev.append(b);
  };
  for (const f of d.ricevute) ric.append(riga(f.nome, f.percorso, bottone("Guarda", () => mostra(f.percorso))));
  ric.append(prev);
  if (anteprimaAios) { const p = anteprimaAios; anteprimaAios = ""; mostra(p); }
  if (d.modifiche.length) carta(el("h3", "", "Tornare indietro"),
    riga("Usa AIOS originale per ora", d.in_uso ? "Le personalizzazioni restano, le riattivi quando vuoi" : "Le personalizzazioni sono spente",
         d.in_uso ? bottone("Usa originale", () => fai({ azione: "originale" })) : bottone("Riattiva", () => fai({ azione: "riprova" }), "bottone primo")),
    riga("Torna allo stato iniziale", "Toglie tutte le personalizzazioni (restano recuperabili per sicurezza)",
         bottone("Azzera", async () => { if (await chiediConferma("Tolgo tutte le personalizzazioni e torno ad AIOS originale?")) fai({ azione: "azzera" }); }, "bottone pericolo")));
  pan.append(esito);
}

// I monitor collegati (Impostazioni › Schermo): risoluzione, frequenza, scala, rotazione, posizione
async function disegnaMonitor(carta, riga, dici) {
  const d = await api("/api/monitor").catch(() => null);
  if (!d || !d.schermi.length) return;
  const cambia = async (nome, cambio) => {
    const r = await api("/api/monitor", { nome, ...cambio }).catch(e => ({ ok: false, messaggio: e.message }));
    if (!r.ok) return dici(r);
    confermaSchermo(r.messaggio, r.secondi);
  };
  const scegli = (valori, attuale, fn) => {
    const s = el("select", "campo"); s.style.maxWidth = "240px";
    for (const [v, t] of valori) { const o = el("option", "", t); o.value = v; o.selected = String(v) === String(attuale); s.append(o); }
    s.onchange = () => fn(s.value); return s;
  };
  const accesi = d.schermi.filter(m => !m.spento);
  if (d.schermi.length > 1) {  // la disposizione, in piccolo
    const c = carta(el("h3", "", "Disposizione"));
    const mappa = el("div", "mappa-schermi"); c.append(mappa);
    const dim = m => { const w = m.larghezza / m.scala, h = m.altezza / m.scala; return m.rotazione % 2 ? [h, w] : [w, h]; };
    const minX = Math.min(...accesi.map(m => m.x)), minY = Math.min(...accesi.map(m => m.y));
    const maxX = Math.max(...accesi.map(m => m.x + dim(m)[0])), maxY = Math.max(...accesi.map(m => m.y + dim(m)[1]));
    const k = Math.min(520 / (maxX - minX), 170 / (maxY - minY));
    mappa.style.width = `${(maxX - minX) * k}px`; mappa.style.height = `${(maxY - minY) * k}px`;
    accesi.forEach((m, i) => {
      const b = el("div", "schermo-mini"); const [w, h] = dim(m);
      Object.assign(b.style, { left: `${(m.x - minX) * k}px`, top: `${(m.y - minY) * k}px`, width: `${w * k - 4}px`, height: `${h * k - 4}px` });
      b.append(el("b", "", String(i + 1)), el("small", "", m.descrizione)); mappa.append(b);
    });
  }
  d.schermi.forEach((m, i) => {
    const titolo = el("h3", ""); titolo.append(svgIcona("monitor"), d.schermi.length > 1 ? `${i + 1}. ${m.descrizione}` : m.descrizione);
    const c = carta(titolo);
    c.append(el("p", "nota", `${m.nome}${m.interno ? " · schermo del portatile" : ""}`));
    if (!m.spento) {
      const ris = m.risoluzioni, chiave = `${m.larghezza}x${m.altezza}`;
      const prima = Object.keys(ris)[0];
      c.append(riga("Risoluzione", chiave === prima ? "Consigliata" : `Consigliata: ${prima.replace("x", "×")}`,
                    scegli(Object.keys(ris).map(r => [r, r.replace("x", "×") + (r === prima ? " (consigliata)" : "")]), chiave, v => cambia(m.nome, { risoluzione: v }))));
      const fr = ris[chiave] || [m.frequenza];
      c.append(riga("Frequenza", fr.length > 1 ? "Più alta, movimenti più fluidi (giochi, mouse)" : null,
                    scegli(fr.map(f => [f, `${Math.round(f)} Hz`]), fr.find(f => Math.abs(f - m.frequenza) < 0.5), v => cambia(m.nome, { frequenza: +v }))));
      if (Math.max(...fr) > m.frequenza + 5) c.append(riga(conIcona("avviso", `Questo schermo può andare a ${Math.round(Math.max(...fr))} Hz`), "Adesso va più lento di quello che può",
                    bottone(`Usa ${Math.round(Math.max(...fr))} Hz`, () => cambia(m.nome, { frequenza: Math.max(...fr) }), "bottone primo")));
      c.append(riga("Dimensione di testo e app", "Per schermi grandi ad alta risoluzione", scegli([1, 1.25, 1.5, 1.75, 2].map(x => [x, `${Math.round(x * 100)}%`]), m.scala, v => cambia(m.nome, { scala: +v }))));
      c.append(riga("Rotazione", null, scegli([[0, "Normale"], [1, "Verticale (90°)"], [3, "Verticale (270°)"], [2, "Capovolto"]], m.rotazione, v => cambia(m.nome, { rotazione: +v }))));
      const vrr = el("button", "interruttore" + (m.vrr ? " acceso" : "")); vrr.onclick = () => cambia(m.nome, { vrr: !m.vrr });
      c.append(riga("Sincronizzazione adattiva", "FreeSync / G-Sync: niente strappi nei giochi, se lo schermo la supporta", vrr));
      const altri = d.schermi.filter(o => o.nome !== m.nome && !o.spento);
      if (altri.length) {
        const pos = [];
        for (const o of altri) for (const [k, t] of [["destra", "a destra di"], ["sinistra", "a sinistra di"], ["sopra", "sopra"], ["sotto", "sotto"]]) pos.push([`${k}|${o.nome}`, `${t} ${o.descrizione}`]);
        for (const o of altri) pos.push([`duplica|${o.nome}`, `uguale a ${o.descrizione} (duplica)`]);
        pos.unshift(["", "—"]);
        c.append(riga("Posizione", null, scegli(pos, "", v => { if (!v) return; const [k, o] = v.split("|"); cambia(m.nome, k === "duplica" ? { duplica_di: o } : { accanto: k, di: o }); })));
      }
    }
    if (d.schermi.length > 1) {
      const sp = el("button", "interruttore" + (!m.spento ? " acceso" : "")); sp.onclick = () => cambia(m.nome, { spento: !m.spento });
      c.append(riga("Acceso", null, sp));
    }
  });
  const az = el("div", "azioni"); az.append(bottone("Torna alle scelte automatiche", async () => { dici(await api("/api/monitor/automatico", {})); apriVista("impostazioni", "schermo"); }));
  carta(el("p", "nota", "Puoi anche dire a Nova: «metti lo schermo a 144 Hz», «ingrandisci tutto al 125%»."), az);
}
// Come su Windows: «Tieni queste impostazioni?» con il conto alla rovescia; senza risposta si torna indietro
function confermaSchermo(testo, secondi = 15) {
  document.getElementById("conferma-schermo")?.remove();
  const fondo = el("div"); fondo.id = "conferma-schermo"; fondo.style.cssText = "position:fixed;inset:0;z-index:50;background:rgba(3,12,18,.55);display:grid;place-items:center";
  const c = el("div", "carta"); c.style.cssText = "width:min(460px,90vw);display:flex;flex-direction:column;gap:12px";
  const conto = el("p", "nota");
  c.append(el("h3", "", "Tieni queste impostazioni?"), el("p", "", testo), conto);
  const fine = async tieni => { clearInterval(t); fondo.remove(); await api(tieni ? "/api/monitor/conferma" : "/api/monitor/annulla", {}).catch(() => {}); apriVista("impostazioni", "schermo"); };
  const a = el("div", "piede-modulo"); a.append(bottone("Torna indietro", () => fine(false)), bottone("Tieni", () => fine(true), "bottone primo"));
  c.append(a); fondo.append(c); document.body.append(fondo);
  let n = secondi; const scrivi = () => { conto.textContent = `Se non rispondi torno come prima tra ${n} secondi.`; };
  scrivi();
  const t = setInterval(() => { n--; scrivi(); if (n <= 0) { clearInterval(t); fondo.remove(); apriVista("impostazioni", "schermo"); } }, 1000);
}

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
