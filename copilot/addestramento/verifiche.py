"""Gli esempi per l'adattatore «verifica» del nucleo: il controllo di qualità delle personalizzazioni.

    python addestramento/verifiche.py --uscita dati-verifica --quante 900

Ogni esempio è una richiesta dell'utente («colora il pulsante «Fallo» di verde», «sposta il riquadro del meteo più
in basso», «metti il tema scuro», «fai l'orologio rotondo con le lancette»…) eseguita su una pagina vera di SoIA
(persona inventata di anteprima.py, Chromium): a volte bene, a volte male (il colore sbagliato, la direzione
sbagliata, un altro elemento sparito, il testo diverso…), a volte con un danno in più (testi sovrapposti, tagliati,
poco leggibili, fuori schermo, un elemento duplicato, la pagina rotta). Siccome la modifica la facciamo noi, la
risposta giusta si sa sempre. Il modello vede quello che vedrà in SoIA: la richiesta e la parte della pagina che è
cambiata, prima e dopo (anteprima.change_crops). Escono verifica.jsonl e prova-verifica.jsonl con le immagini.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aios_copilot import anteprima  # noqa: E402
from aios_copilot.nucleo import VERIFY_PROBLEMS, prompt_verifica  # noqa: E402

PAGES = [("casa", 8), ("impostazioni/personalizzazioni", 1), ("impostazioni/schermo", 1), ("impostazioni/aspetto", 1),
         ("impostazioni/suono", 1), ("impostazioni/accessibilita", 1), ("impostazioni/mouse", 1),
         ("impostazioni/privacy", 1), ("impostazioni/azioni", 1), ("attivita", 2), ("calendario", 2), ("rubrica", 1),
         ("pannello/emoji", 1), ("pannello/appunti", 1), ("pannello/notifiche", 1)]
CHANGES = [("colore", 4), ("dimensione", 3), ("posizione", 3), ("testo", 3), ("togli", 2), ("aggiungi", 3),
           ("angoli", 1), ("bordo", 2), ("tema", 1), ("orologio", 2)]
EFFECTS = ["sovrapposti", "tagliato", "contrasto", "fuori", "duplicato", "rotta"]
COLORS = {"rosso": "#d62828", "verde": "#2a9d43", "blu": "#1d4ed8", "giallo": "#f4c430", "arancione": "#f77f00",
          "viola": "#7b2cbf", "rosa": "#ff5d8f", "nero": "#111111", "grigio": "#8a8f98", "azzurro": "#4cc9f0",
          "marrone": "#7f4f24", "bianco": "#ffffff"}
WORDS = ["Benvenuta", "Le mie cose", "Lavoro", "Famiglia", "Da fare", "Musica preferita", "Ricette", "Viaggi",
         "Promemoria", "Scuola", "Casa nuova", "Spesa", "Palestra", "Bollette", "Ciao Giulia", "Buona giornata"]
SIZES = [(1366, 768), (1366, 768), (1280, 800), (1440, 900)]

APPLY_JS = r"""(p) => {
  let s = p.seme >>> 0;
  const rnd = () => { s = (s + 0x6D2B79F5) >>> 0; let t = s; t = Math.imul(t ^ t >>> 15, t | 1);
    t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  const pick = a => a[Math.floor(rnd() * a.length)];
  const W = innerWidth, H = innerHeight;
  const words = el => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').split(' ').slice(0, 4).join(' ').slice(0, 30);
  const visible = el => { const st = getComputedStyle(el), r = el.getBoundingClientRect();
    return st.visibility !== 'hidden' && st.display !== 'none' && +st.opacity > 0.5 && r.width > 24 && r.height > 10 &&
      r.top > 0 && r.bottom < H && r.left >= 0 && r.right <= W; };
  const texts = () => [...document.querySelectorAll('body *')].filter(el => !el.closest('[data-prova]') &&
    [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length >= 3) && visible(el) &&
    !['SCRIPT', 'STYLE', 'OPTION', 'INPUT', 'TEXTAREA', 'B', 'SMALL'].includes(el.tagName));
  const isCard = el => { const st = getComputedStyle(el), r = el.getBoundingClientRect();
    const bg = (st.backgroundColor.match(/[\d.]+/g) || []).map(Number);
    return bg.length >= 3 && (bg.length < 4 || bg[3] > 0.3) && parseFloat(st.borderTopLeftRadius) >= 6 &&
      r.width >= 100 && r.height >= 36 && r.width <= W * 0.7 && r.height <= H * 0.8; };
  const cardOf = el => { for (let e = el; e && e !== document.body; e = e.parentElement) if (isCard(e)) return e; return null; };
  const cards = () => [...new Set(texts().map(cardOf).filter(Boolean))].filter(visible);
  const nameOf = el => {
    const w = words(el);
    if (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button') return `il pulsante «${w}»`;
    if (/^H[1-3]$/.test(el.tagName)) return `il titolo «${w}»`;
    if (isCard(el)) { const t = texts().find(x => el.contains(x)); return `il riquadro «${t ? words(t) : w}»`; }
    return `la scritta «${w}»`;
  };
  const prep = (name, kind) => name.replace(/^(il|la) /, (_, a) => ({ di: { il: 'del ', la: 'della ' },
    a: { il: 'al ', la: 'alla ' } })[kind][a]);  // «di il pulsante» → «del pulsante»
  const hits = el => { const r = el.getBoundingClientRect();
    const cx = Math.min(W - 2, Math.max(1, r.left + Math.min(r.width / 2, 12))), cy = r.top + Math.min(r.height / 2, 10);
    const h = cy > 0 && cy < H ? document.elementFromPoint(cx, cy) : null; return h && (h === el || el.contains(h) || h.contains(el)); };
  const out = { richiesta: '', problemi: [] };
  const anyTarget = () => { const c = cards(); return c.length && rnd() < 0.55 ? pick(c) : pick(texts()); };
  const C = p.colori;

  // --- la modifica chiesta -----------------------------------------------------------------------------
  const t = p.tipo;
  if (t === 'colore') {
    const el = anyTarget(); if (!el) return null;
    const asText = !isCard(el) && el.tagName !== 'BUTTON';
    out.richiesta = pick([`colora ${nameOf(el)} di ${p.colore}`, `fai ${nameOf(el)} ${p.colore}`, `voglio ${nameOf(el)} ${p.colore}`]);
    const col = C[p.giusto ? p.colore : p.colore_sbagliato];
    if (asText) el.style.color = col; else el.style.background = col;
    if (!p.giusto) out.problemi.push('colore diverso da quello chiesto');
  } else if (t === 'dimensione') {
    const el = anyTarget(); if (!el) return null;
    const bigger = rnd() < 0.6;
    out.richiesta = `fai ${nameOf(el)} più ${bigger ? 'grande' : 'piccolo'}`;
    const k = (bigger === p.giusto) ? 1.55 : 0.65;
    if (isCard(el)) { el.style.transform = `scale(${k})`; el.style.transformOrigin = 'top left'; el.style.zIndex = 30; }
    else el.style.fontSize = (parseFloat(getComputedStyle(el).fontSize) * k) + 'px';
    if (!p.giusto) out.problemi.push('dimensione sbagliata');
  } else if (t === 'posizione') {
    const el = anyTarget(); if (!el) return null;
    const dirs = { 'più in basso': [0, 1], 'più in alto': [0, -1], 'più a destra': [1, 0], 'più a sinistra': [-1, 0] };
    const d = pick(Object.keys(dirs));
    out.richiesta = `sposta ${nameOf(el)} ${d}`;
    const go = p.giusto ? d : pick(Object.keys(dirs).filter(x => x !== d));
    const n = 70 + rnd() * 90;
    el.style.position = 'relative'; el.style.zIndex = 30;
    el.style.transform = `translate(${dirs[go][0] * n}px, ${dirs[go][1] * n}px)`;
    if (!p.giusto) out.problemi.push('posizione sbagliata');
  } else if (t === 'testo') {
    const el = pick(texts().filter(x => words(x).length >= 4 && x.children.length === 0)); if (!el) return null;
    out.richiesta = `cambia la scritta «${words(el)}» in «${p.testo}»`;
    el.textContent = p.giusto ? p.testo : p.testo_sbagliato;
    if (!p.giusto) out.problemi.push('testo diverso da quello chiesto');
  } else if (t === 'togli') {
    const pool = rnd() < 0.6 ? cards() : texts();
    if (pool.length < 2) return null;
    const el = pick(pool), other = pick(pool.filter(x => x !== el && !x.contains(el) && !el.contains(x)));
    if (!other) return null;
    out.richiesta = pick([`togli ${nameOf(el)}`, `nascondi ${nameOf(el)}`, `non voglio più vedere ${nameOf(el)}`]);
    (p.giusto ? el : other).style.visibility = 'hidden';
    if (!p.giusto) out.problemi.push('manca quello che è stato chiesto', 'è sparito un elemento che doveva restare');
  } else if (t === 'aggiungi') {
    const el = anyTarget(); if (!el) return null;
    const where = pick(['sotto', 'sopra']);
    out.richiesta = pick([`aggiungi una scritta «${p.testo}» ${where} ${nameOf(el)}`, `metti «${p.testo}» ${where} ${nameOf(el)}`]);
    const make = text => { const d = document.createElement('div'); d.setAttribute('data-prova', '1'); d.textContent = text;
      d.style.cssText = 'font-weight:600;padding:4px 0;font-size:15px'; return d; };
    const bad = !p.giusto ? pick(['testo', 'doppio']) : '';
    const n = make(bad === 'testo' ? p.testo_sbagliato : p.testo);
    el.insertAdjacentElement(where === 'sotto' ? 'afterend' : 'beforebegin', n);
    if (bad === 'doppio') { n.insertAdjacentElement('afterend', make(p.testo)); out.problemi.push('elemento duplicato'); }
    if (bad === 'testo') out.problemi.push('testo diverso da quello chiesto');
  } else if (t === 'angoli') {
    const el = pick(cards()); if (!el) return null;
    out.richiesta = pick([`arrotonda gli angoli ${prep(nameOf(el), 'di')}`, `fai ${nameOf(el)} con gli angoli più tondi`]);
    el.style.borderRadius = p.giusto ? '34px' : '0px';
    if (!p.giusto) out.problemi.push('manca quello che è stato chiesto');
  } else if (t === 'bordo') {
    const el = anyTarget(); if (!el) return null;
    out.richiesta = `metti un bordo ${p.colore} attorno ${prep(nameOf(el), 'a')}`;
    el.style.outline = `4px solid ${C[p.giusto ? p.colore : p.colore_sbagliato]}`; el.style.outlineOffset = '2px';
    if (!p.giusto) out.problemi.push('colore diverso da quello chiesto');
  } else if (t === 'tema') {
    out.richiesta = pick(['metti il tema scuro', 'voglio SoIA scuro', 'passa al tema scuro']);
    // il tema lo cambia Python (prefers-color-scheme); qui solo l'errore: testi rimasti scuri sul fondo scuro
    if (!p.giusto) {
      const st = document.createElement('style'); st.setAttribute('data-prova', '1');
      st.textContent = 'body, body * { color: #1d2430 !important; }'; document.head.append(st);
      out.problemi.push('testo poco leggibile');
    }
  } else if (t === 'orologio') {
    const card = [...document.querySelectorAll('*')].find(e => /^Ora\s*·/i.test((e.textContent || '').trim()) &&
      e.children.length > 1 && e.getBoundingClientRect().width < 500);
    if (!card) return null;
    out.richiesta = pick(["fai l'orologio rotondo con le lancette", "voglio l'orologio con le lancette",
                          "trasforma l'orologio in un orologio analogico"]);
    const o = p.orologio, size = o.misura, r = size / 2, NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg'); svg.setAttribute('width', size); svg.setAttribute('height', size);
    const add = (tag, a) => { const e = document.createElementNS(NS, tag); for (const k in a) e.setAttribute(k, a[k]); svg.appendChild(e); return e; };
    add('circle', { cx: r, cy: r, r: r - 3, fill: o.quadrante, stroke: o.segni, 'stroke-width': 3 });
    for (let i = 0; i < 12; i++) { const a = i * 30 * Math.PI / 180, r1 = r - 8, r2 = r1 - r * 0.12;
      add('line', { x1: r + r1 * Math.sin(a), y1: r - r1 * Math.cos(a), x2: r + r2 * Math.sin(a), y2: r - r2 * Math.cos(a), stroke: o.segni, 'stroke-width': 2.5 }); }
    const [hh, mm] = p.giusto ? [p.ora.h, p.ora.m] : [p.ora_sbagliata.h, p.ora_sbagliata.m];
    const hand = (deg, len, w) => add('line', { x1: r, y1: r, x2: r, y2: r - r * len, stroke: o.segni, 'stroke-width': w,
      'stroke-linecap': 'round', transform: `rotate(${deg} ${r} ${r})` });
    hand(hh % 12 * 30 + mm * 0.5, 0.5, 6); hand(mm * 6, 0.78, 3.5);
    for (const e of card.querySelectorAll('*')) if (/^\d{1,2}:\d{2}$/.test((e.textContent || '').trim())) e.style.display = 'none';
    const box = document.createElement('div'); box.setAttribute('data-prova', '1'); box.style.cssText = 'display:flex;justify-content:center';
    box.append(svg); card.append(box);
    if (!p.giusto) out.problemi.push("lancette che non segnano l'ora giusta");
  }

  // --- a volte un danno in più ------------------------------------------------------------------------------
  const e = p.effetto;
  if (e) {
    const pool = texts().filter(x => !x.closest('[data-prova]'));
    const el = pick(pool); if (!el) return out;
    const r = el.getBoundingClientRect(), saved = el.style.cssText;
    let label = '';
    if (e === 'sovrapposti') {
      const other = pick(pool.filter(x => x !== el && !x.contains(el) && !el.contains(x))); if (!other) return out;
      const b = other.getBoundingClientRect();
      el.style.position = 'relative'; el.style.zIndex = 40;
      el.style.transform = `translate(${b.left - r.left + (rnd() - .5) * 16}px, ${b.top - r.top + (rnd() - .5) * b.height * .5}px)`;
      label = 'testi sovrapposti';
    } else if (e === 'tagliato' && (el.textContent || '').trim().length >= 14 && r.width > 90) {
      el.style.display = 'inline-block'; el.style.whiteSpace = 'nowrap'; el.style.overflow = 'hidden';
      el.style.maxWidth = Math.round(r.width * (0.3 + rnd() * 0.2)) + 'px'; el.style.verticalAlign = 'bottom';
      label = 'testo tagliato';
    } else if (e === 'contrasto') {
      const bg = getComputedStyle(document.body).backgroundColor;
      el.style.color = matchMedia('(prefers-color-scheme: dark)').matches ? '#2b3a44' : '#d8e2e6';
      label = 'testo poco leggibile';
    } else if (e === 'fuori' && r.left > W * 0.45) {
      el.style.position = 'relative'; el.style.transform = `translateX(${Math.round(W - r.left - r.width * 0.35)}px)`;
      label = 'elemento fuori dallo schermo';
    } else if (e === 'duplicato') {
      const c = cardOf(el) || el; const copy = c.cloneNode(true); copy.setAttribute('data-prova', '1');
      c.insertAdjacentElement('afterend', copy); out.problemi.push('elemento duplicato'); return out;
    } else if (e === 'rotta') {
      for (const x of document.querySelectorAll('body > *')) if (!/barra/.test(x.id)) x.style.visibility = 'hidden';
      out.problemi.push('pagina vuota o rotta'); return out;
    }
    if (label) { if (hits(el)) out.problemi.push(label); else el.style.cssText = saved; }
  }
  return out;
}"""


def params(rng: random.Random, kind: str, now: datetime) -> dict:
    colore = rng.choice(list(COLORS))
    testo = rng.choice(WORDS)
    wrong_time = now + timedelta(minutes=rng.choice([-1, 1]) * rng.randrange(40, 330))
    return {"seme": rng.randrange(2**31), "tipo": kind, "giusto": rng.random() < 0.45, "colori": COLORS,
            "colore": colore, "colore_sbagliato": rng.choice([c for c in COLORS if c != colore]),
            "testo": testo, "testo_sbagliato": rng.choice([w for w in WORDS if w != testo]),
            "ora": {"h": now.hour, "m": now.minute}, "ora_sbagliata": {"h": wrong_time.hour, "m": wrong_time.minute},
            "orologio": {"misura": rng.choice([120, 150, 180]), "quadrante": rng.choice(["#ffffff", "#f6f1e7", "#1b2128"]),
                         "segni": rng.choice(["#1d2a33", "#0b6e78", "#5a3e2b"])},
            "effetto": rng.choice(EFFECTS) if rng.random() < 0.3 else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uscita", default="dati-verifica")
    ap.add_argument("--quante", type=int, default=900)
    ap.add_argument("--prova", type=float, default=0.1)
    ap.add_argument("--seme", type=int, default=11)
    args = ap.parse_args()

    out = Path(args.uscita)
    (out / "img").mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="aios-persona-"))
    anteprima.keep_browsers()
    os.environ.update(anteprima.fake_env(home))
    anteprima.seed_demo(home)
    anteprima.fake_services()
    base = anteprima._serve()

    from playwright.sync_api import sync_playwright

    rng = random.Random(args.seme)
    pages, pw_ = zip(*PAGES)
    kinds, kw = zip(*CHANGES)
    rows, skipped = [], 0
    started = time.monotonic()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("AIOS_CHROMIUM") or None)
        i = 0
        while len(rows) < args.quante and i < args.quante * 3:
            i += 1
            kind = rng.choices(kinds, kw)[0]
            page_name = "casa" if kind == "orologio" else rng.choices(pages, pw_)[0]
            w, h = rng.choice(SIZES)
            dark = kind != "tema" and rng.random() < 0.3
            now = datetime(2026, rng.randrange(1, 13), rng.randrange(1, 28), rng.randrange(24), rng.randrange(60))
            ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme="dark" if dark else "light")
            page = ctx.new_page()
            try:
                page.clock.set_fixed_time(now)
                page.add_init_script(anteprima.CATCH_ERRORS_JS)
                url, js = anteprima.page_url(base, page_name)
                page.goto(url)
                page.wait_for_timeout(1200)
                if js:
                    page.evaluate(js)
                    page.wait_for_timeout(1200)
                before = page.screenshot()
                p = params(rng, kind, now)
                if kind == "tema":
                    page.emulate_media(color_scheme="dark")
                    page.wait_for_timeout(300)
                answer = page.evaluate(APPLY_JS, p)
                page.wait_for_timeout(200)
                after = page.screenshot()
            except Exception as exc:
                print(f"{i}: {page_name}/{kind}: {exc}", flush=True)
                ctx.close()
                continue
            ctx.close()
            crops = anteprima.change_crops(before, after) if answer else None
            if crops is None:
                skipped += 1
                continue
            problems = [x for x in dict.fromkeys(answer["problemi"]) if x in VERIFY_PROBLEMS]
            n = len(rows)
            names = [f"img/v{n:05d}-prima.png", f"img/v{n:05d}-dopo.png"]
            (out / names[0]).write_bytes(crops[0])
            (out / names[1]).write_bytes(crops[1])
            rows.append({"immagini": names, "pagina": page_name, "modifica": kind, "richiesta": answer["richiesta"],
                         "prompt": prompt_verifica(answer["richiesta"], now),
                         "risposta": json.dumps({"fatto": not problems, "problemi": problems}, ensure_ascii=False)})
            if n % 25 == 0:
                print(f"{n + 1}/{args.quante}  {(time.monotonic() - started) / 60:.1f} min  (saltati {skipped})", flush=True)
        browser.close()
    rng.shuffle(rows)
    k = max(1, int(len(rows) * args.prova))
    (out / "prova-verifica.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[:k]))
    (out / "verifica.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[k:]))
    ok = sum(json.loads(r["risposta"])["fatto"] for r in rows)
    print(f"{len(rows)} esempi ({ok} riusciti, {len(rows) - ok} con problemi; {k} per la misura) in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
