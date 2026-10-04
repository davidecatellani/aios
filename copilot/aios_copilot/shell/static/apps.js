"use strict";
// Le app di AIOS dentro la shell (File, Foto, Musica, Video, Note, documenti, Impostazioni).
// Usa api(), el(), $(), TOKEN e chiedi() di home.html.

const SIMBOLI = { cartella: "📁", immagine: "🖼️", audio: "🎵", video: "🎬", testo: "📝", documento: "📄", altro: "📦" };
const fileUrl = p => `/file/${encodeURIComponent(p)}?t=${encodeURIComponent(TOKEN)}`;
let vistaAttuale = null;
const audio = new Audio();

function apriVista(nome, ...args) {
  const v = VISTE[nome];
  if (!v) return;
  if (nome !== "musica" && vistaAttuale === "musica" && !audio.paused) { /* la musica continua in sottofondo */ }
  document.body.classList.add("in-vista");
  $("tutte").hidden = true;
  const box = $("vista"); box.hidden = false; box.replaceChildren();
  vistaAttuale = nome;
  api("/api/casa-vai", { chiudi_viste: false }).catch(() => {});  // i programmi a finestra si fanno da parte
  v(box, ...args);
}
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
  const back = el("button", "torna", "←"); back.title = "Torna alla schermata (Esc)"; back.onclick = chiudiVista;
  const h = el("h2"); h.append(titolo);
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
  box.append(img, did, chiudi);
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
    const corpo = testa(box, "📁 File", [briciole, nuova]);
    if (!data.voci.length) { corpo.append(el("p", "vuoto", "Questa cartella è vuota.")); return; }
    const griglia = el("div", "elenco-file");
    for (const f of data.voci) {
      const b = el("button", "voce-file"); b.title = f.nome;
      const ic = el("div", "icona-file");
      if (f.tipo === "immagine") { const i = el("img"); i.loading = "lazy"; i.src = fileUrl(f.percorso); i.alt = ""; ic.append(i); }
      else ic.textContent = SIMBOLI[f.tipo] || "📦";
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
    const corpo = testa(box, "🖼️ Foto");
    const { voci, cartella } = await api("/api/raccolta/foto").catch(() => ({ voci: [], cartella: "Immagini" }));
    if (!voci.length) { corpo.append(el("p", "vuoto", `Nessuna foto in «${cartella}».\nCollega il telefono e chiedi a Nova di copiare le foto.`)); return; }
    const g = el("div", "griglia-foto");
    voci.forEach((f, i) => {
      const b = el("button"); const img = el("img"); img.loading = "lazy"; img.alt = f.nome; img.src = fileUrl(f.percorso);
      b.append(img); b.onclick = () => lampada(voci, i); g.append(b);
    });
    corpo.append(g);
  },

  async musica(box, daSuonare) {
    const corpo = testa(box, "🎵 Musica");
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
    const corpo = testa(box, "🎬 Video");
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
    const corpo = testa(box, "📝 Note", nuova);
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
    testa(box, "📄 " + f.nome, [chiediNova, conProgramma]).remove();
    const fr = el("iframe", "riquadro-doc");
    fr.setAttribute("sandbox", "");  // il documento non esegue nulla
    fr.src = `/doc/vedi?p=${encodeURIComponent(f.percorso)}&t=${encodeURIComponent(TOKEN)}`;
    box.append(fr);
  },

  async impostazioni(box, sezione = "wifi") {
    const corpo = testa(box, "⚙️ Impostazioni");
    const wrap = el("div", "impostazioni"); const nav = el("div", "sezioni"); const pan = el("div", "pannello-imp");
    wrap.append(nav, pan); corpo.append(wrap);
    const SEZ = [["wifi", "📶 Wi-Fi"], ["bluetooth", "🔵 Bluetooth"], ["suono", "🔊 Suono e schermo"], ["voce", "🗣️ Voce di Nova"], ["tastiera", "⌨️ Tastiera"],
                 ["aggiornamenti", "⬇️ Aggiornamenti"], ["account", "🔑 Password"], ["privacy", "🔒 Privacy e memoria"], ["info", "ℹ️ Questo computer"], ["energia", "⏻ Spegni"]];
    for (const [id, t] of SEZ) {
      const b = bottone(t, () => apriVista("impostazioni", id), ""); b.classList.toggle("attiva", id === sezione); nav.append(b);
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
                    tastiera: "tastiera" }[sezione];
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
          c.append(riga(`${n.protetta ? "🔒 " : ""}${n.nome}`, null, el("span", "segnale", `${n.segnale}%`), collega));
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
      carta(riga("🔊 Volume", d.volume?.livello == null ? "Audio non disponibile" : null, cursore(d.volume?.livello, "volume")),
            riga("☀️ Luminosità", d.luminosita?.livello == null ? "Questo schermo non la regola da qui" : null, cursore(d.luminosita?.livello, "luminosita")));
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
            el("p", "nota", "Per scaricare le nuove versioni dal repository privato: crea un token con il solo permesso «Contents: read» e incollalo qui. Resta nel portachiavi del computer."),
            riga("Token", null, tok, bottone("Collega", async () => {
              dici(await api("/api/impostazioni/github", { token: tok.value }).catch(e => ({ ok: false, messaggio: e.message }))); tok.value = "";
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
    } else if (sezione === "privacy") {
      const di = d.diario || {};
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
        const r = el("div", "riga-imp"); const c = el("div", "cosa"); c.append(`${n.protetta ? "🔒 " : ""}${n.nome}`);
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
      b.append(el("span", "s", p.simbolo), el("div", "")); b.lastChild.append(el("b", "", p.titolo + (p.fatto ? " ✓" : "")), p.testo);
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
    r.append(el("span", "", p.simbolo), el("span", "t", p.titolo), bottone("Fallo", () => eseguiPasso(p), ""), via);
    c.append(r);
  }
  col.prepend(c);
};
