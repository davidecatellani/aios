"""Le schermate per l'adattatore «schermate» del nucleo: pagine vere di AIOS con guasti messi apposta.

    python addestramento/schermate.py --uscita dati-schermate --quante 600

Ogni foto è una pagina di AIOS (la schermata con i widget, le impostazioni, le app, il pannello) con i dati della
persona inventata di anteprima.py, disegnata con Chromium. Sopra ci mettiamo noi quello che il programmatore
può sbagliare, quindi la risposta giusta la sappiamo sempre, senza etichettare niente a mano:
- un orologio con le lancette (stili, colori, numeri e tacche a caso) che segna un'ora a caso: va letta;
- testi spostati sopra altri (sovrapposti), tagliati, quasi invisibili (contrasto) o mezzi fuori dallo schermo.
Escono schermate.jsonl (addestramento) e prova-schermate.jsonl (misura), con le immagini.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aios_copilot import anteprima  # noqa: E402
from aios_copilot.nucleo import PROMPT_SCHERMATA  # noqa: E402

PAGES = [("casa", 9), ("impostazioni/personalizzazioni", 1), ("impostazioni/schermo", 1), ("impostazioni/aspetto", 1),
         ("impostazioni/suono", 1), ("impostazioni/accessibilita", 1), ("impostazioni/mouse", 1),
         ("impostazioni/tastiera", 1), ("impostazioni/privacy", 1), ("impostazioni/info", 1), ("attivita", 2),
         ("calendario", 2), ("rubrica", 1), ("pannello/emoji", 1), ("pannello/appunti", 1), ("pannello/notifiche", 1)]
SIZES = [(1366, 768), (1366, 768), (1280, 800), (1440, 900)]
DEFECTS = ("sovrapposti", "tagliato", "contrasto", "fuori")

# Nella pagina: l'orologio con le lancette e i guasti. Torna la risposta giusta (stesso formato del nucleo).
INJECT_JS = r"""(p) => {
  let s = p.seme >>> 0;
  const rnd = () => { s = (s + 0x6D2B79F5) >>> 0; let t = s; t = Math.imul(t ^ t >>> 15, t | 1);
    t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  const pick = a => a[Math.floor(rnd() * a.length)];
  const W = innerWidth, H = innerHeight;
  const out = { orologio: '', problemi: [] };
  const words = el => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').split(' ').slice(0, 4).join(' ').slice(0, 32);
  const visible = el => { const st = getComputedStyle(el), r = el.getBoundingClientRect();
    return st.visibility !== 'hidden' && st.display !== 'none' && +st.opacity > 0.5 && r.width > 30 && r.height > 8 &&
      r.top > 0 && r.bottom < H && r.left >= 0 && r.right <= W; };
  const texts = () => [...document.querySelectorAll('body *')].filter(el => !el.closest('[data-prova]') &&
    [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length >= 4) && visible(el) &&
    !['SCRIPT', 'STYLE', 'OPTION', 'INPUT', 'TEXTAREA'].includes(el.tagName));
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = (getComputedStyle(e).backgroundColor.match(/[\d.]+/g) || []).map(Number);
      if (c.length >= 3 && (c.length < 4 || c[3] > 0.6)) return c; }
    return matchMedia('(prefers-color-scheme: dark)').matches ? [20, 24, 30] : [240, 244, 246]; };

  if (p.orologio) {
    const o = p.orologio, size = o.misura, r = size / 2, NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('width', size); svg.setAttribute('height', size); svg.setAttribute('viewBox', `0 0 ${size} ${size}`);
    svg.setAttribute('data-prova', '1');
    const add = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); svg.appendChild(e); return e; };
    add('circle', { cx: r, cy: r, r: r - o.bordo, fill: o.quadrante, stroke: o.cornice, 'stroke-width': o.bordo });
    for (let i = 0; i < 60; i++) {
      if (o.tacche === 0 || (o.tacche === 12 && i % 5)) continue;
      const a = i * 6 * Math.PI / 180, big = i % 5 === 0, r1 = r - o.bordo - 3, r2 = r1 - (big ? r * 0.12 : r * 0.05);
      add('line', { x1: r + r1 * Math.sin(a), y1: r - r1 * Math.cos(a), x2: r + r2 * Math.sin(a), y2: r - r2 * Math.cos(a),
                    stroke: o.segni, 'stroke-width': big ? 2.5 : 1 });
    }
    if (o.numeri) for (let n = 1; n <= 12; n++) {
      if (o.numeri === 4 && n % 3) continue;
      const a = n * 30 * Math.PI / 180, rr = r * 0.72;
      const t = add('text', { x: r + rr * Math.sin(a), y: r - rr * Math.cos(a) + r * 0.06, 'text-anchor': 'middle',
                              'font-size': r * 0.17, fill: o.segni, 'font-family': 'sans-serif' });
      t.textContent = o.romani ? ['I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII'][n - 1] : n;
    }
    const hand = (deg, len, w, color, tail) => add('line', { x1: r, y1: r + r * tail, x2: r, y2: r - r * len, stroke: color,
      'stroke-width': w, 'stroke-linecap': 'round', transform: `rotate(${deg} ${r} ${r})` });
    hand(o.ore % 12 * 30 + o.minuti * 0.5, o.lungOre, o.spessOre, o.lancette, 0.08);
    hand(o.minuti * 6, o.lungMin, o.spessMin, o.lancette, 0.1);
    if (o.secondi >= 0) hand(o.secondi * 6, 0.85, 1.2, o.secondiColore, 0.18);
    add('circle', { cx: r, cy: r, r: Math.max(3, o.spessOre * 0.8), fill: o.lancette });
    const card = [...document.querySelectorAll('*')].find(e => /^Ora\s*·/i.test((e.textContent || '').trim()) && e.children.length > 1
      && e.getBoundingClientRect().width < 500);
    if (o.dove === 'widget' && card) {
      const box = document.createElement('div'); box.setAttribute('data-prova', '1');
      box.style.cssText = 'display:flex;justify-content:center;padding:6px 0';
      box.appendChild(svg);
      if (o.togliCifre) for (const e of card.querySelectorAll('*')) if (/^\d{1,2}:\d{2}$/.test((e.textContent || '').trim())) e.style.display = 'none';
      card.appendChild(box);
    } else {
      svg.style.cssText = `position:fixed;left:${o.x * (W - size)}px;top:${60 + o.y * (H - size - 140)}px;z-index:50`;
      document.body.appendChild(svg);
    }
    const h12 = o.ore % 12 || 12;
    out.orologio = h12 + ':' + String(o.minuti).padStart(2, '0');
  }

  const used = new Set();
  for (const tipo of p.difetti) {
    let pool = texts().filter(e => !used.has(e) && ![...used].some(u => u.contains(e) || e.contains(u)));
    if (tipo === 'tagliato') pool = pool.filter(e => (e.textContent || '').trim().length >= 14 && e.getBoundingClientRect().width > 90);
    if (tipo === 'fuori') pool = pool.filter(e => e.getBoundingClientRect().left > W * 0.45);
    if (!pool.length) continue;
    const el = pick(pool), r = el.getBoundingClientRect(), saved = el.style.cssText;
    used.add(el);
    const label = words(el);
    if (tipo === 'sovrapposti') {
      const others = texts().filter(e => e !== el && !e.contains(el) && !el.contains(e) && !used.has(e));
      if (!others.length) continue;
      const b = pick(others).getBoundingClientRect();
      const dx = b.left - r.left + (rnd() - 0.5) * 20, dy = b.top - r.top + (rnd() - 0.5) * b.height * 0.6;
      el.style.position = 'relative'; el.style.zIndex = 40; el.style.transform = `translate(${dx}px, ${dy}px)`;
    } else if (tipo === 'tagliato') {
      el.style.display = 'inline-block'; el.style.whiteSpace = 'nowrap'; el.style.overflow = 'hidden';
      el.style.textOverflow = 'clip'; el.style.maxWidth = Math.round(r.width * (0.3 + rnd() * 0.25)) + 'px';
      el.style.verticalAlign = 'bottom';
    } else if (tipo === 'contrasto') {
      const bg = bgOf(el), k = 0.82 + rnd() * 0.1, fg = getComputedStyle(el).color.match(/[\d.]+/g).map(Number);
      el.style.color = `rgb(${bg.slice(0, 3).map((v, i) => Math.round(v * k + fg[i] * (1 - k))).join(',')})`;
    } else if (tipo === 'fuori') {
      el.style.position = 'relative'; el.style.transform = `translateX(${Math.round(W - r.left - r.width * (0.25 + rnd() * 0.3))}px)`;
    }
    // il guasto deve vedersi: il punto in alto a sinistra del testo spostato è davvero lui (non coperto o tagliato via)
    const nr = el.getBoundingClientRect();
    const cx = Math.min(W - 2, Math.max(1, nr.left + Math.min(nr.width / 2, 12))), cy = nr.top + Math.min(nr.height / 2, 10);
    const hit = cy > 0 && cy < H ? document.elementFromPoint(cx, cy) : null;
    if (!hit || !(hit === el || el.contains(hit))) { el.style.cssText = saved; continue; }
    out.problemi.push({ tipo, testo: label });
  }
  return out;
}"""

COLORS_LIGHT = ["#ffffff", "#f6f1e7", "#eef4f7", "#fdfdfd", "#e8f0ec"]
COLORS_DARK = ["#1b2128", "#10161c", "#2a2f36", "#0d3b45"]
INKS = ["#1d2a33", "#000000", "#0b6e78", "#5a3e2b", "#26324d"]
LIGHT_INKS = ["#f2f2f2", "#d9eef2", "#ffffff"]


def clock_params(rng: random.Random, on_home: bool) -> dict:
    dark = rng.random() < 0.35
    face = rng.choice(COLORS_DARK if dark else COLORS_LIGHT)
    ink = rng.choice(LIGHT_INKS if dark else INKS)
    return {"ore": rng.randrange(24), "minuti": rng.randrange(60), "secondi": rng.randrange(60) if rng.random() < 0.4 else -1,
            "misura": rng.choice([110, 140, 170, 200, 230]), "bordo": rng.choice([1, 2, 3, 5]), "quadrante": face,
            "cornice": rng.choice([ink, "#0b8a8f", "#999999", face]), "segni": ink, "lancette": ink,
            "secondiColore": rng.choice(["#d33", "#e07a00", ink]), "tacche": rng.choice([0, 12, 60, 60]),
            "numeri": rng.choice([0, 4, 12, 12]), "romani": rng.random() < 0.15,
            "lungOre": rng.uniform(0.42, 0.55), "lungMin": rng.uniform(0.7, 0.85), "spessOre": rng.uniform(3.5, 8),
            "spessMin": rng.uniform(2, 4.5), "dove": "widget" if on_home and rng.random() < 0.7 else "libero",
            "togliCifre": rng.random() < 0.6, "x": rng.random(), "y": rng.random()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uscita", default="dati-schermate")
    ap.add_argument("--quante", type=int, default=600)
    ap.add_argument("--prova", type=float, default=0.1, help="parte tenuta da parte per la misura")
    ap.add_argument("--seme", type=int, default=7)
    args = ap.parse_args()

    out = Path(args.uscita)
    (out / "img").mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="aios-persona-"))
    os.environ.update(anteprima.fake_env(home))
    anteprima.seed_demo(home)
    anteprima.fake_services()
    base = anteprima._serve()

    from playwright.sync_api import sync_playwright

    rng = random.Random(args.seme)
    names, weights = zip(*PAGES)
    rows = []
    started = time.monotonic()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("AIOS_CHROMIUM") or None)
        for i in range(args.quante):
            page_name = rng.choices(names, weights)[0]
            w, h = rng.choice(SIZES)
            ctx = browser.new_context(viewport={"width": w, "height": h},
                                      color_scheme="dark" if rng.random() < 0.35 else "light")
            page = ctx.new_page()
            url, js = anteprima.page_url(base, page_name)
            try:
                page.goto(url)
                page.wait_for_timeout(1300)
                if js:
                    page.evaluate(js)
                    page.wait_for_timeout(1300)
                on_home = page_name == "casa"
                n = rng.choices([0, 1, 2], [35, 45, 20])[0]
                params = {"seme": rng.randrange(2**31),
                          "orologio": clock_params(rng, on_home) if rng.random() < (0.85 if on_home else 0.2) else None,
                          "difetti": rng.sample(DEFECTS, n)}
                answer = page.evaluate(INJECT_JS, params)
                page.wait_for_timeout(150)
                name = f"img/s{i:05d}.png"
                page.screenshot(path=str(out / name))
            except Exception as exc:  # una pagina che non si apre: si salta
                print(f"{i}: {page_name}: {exc}", flush=True)
                ctx.close()
                continue
            ctx.close()
            rows.append({"immagine": name, "pagina": page_name, "prompt": PROMPT_SCHERMATA,
                         "risposta": json.dumps({"orologio": answer["orologio"], "problemi": answer["problemi"]}, ensure_ascii=False)})
            if i % 25 == 0:
                print(f"{i + 1}/{args.quante}  {(time.monotonic() - started) / 60:.1f} min", flush=True)
        browser.close()
    rng.shuffle(rows)
    k = max(1, int(len(rows) * args.prova))
    (out / "prova-schermate.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[:k]))
    (out / "schermate.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[k:]))
    print(f"{len(rows)} schermate ({k} per la misura) in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
