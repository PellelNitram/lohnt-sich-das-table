// Interactive charts: histograms of gross/net income and net-vs-gross scatter.
// Plain SVG, no dependencies. Data comes from the JSON block the build embeds in
// the page (#chart-data). Every value is also in the table above, so tooltips
// only add convenience, they never gate information.
(() => {
  const dataEl = document.getElementById("chart-data");
  if (!dataEl) return;
  let entries;
  try {
    entries = JSON.parse(dataEl.textContent);
  } catch {
    return;
  }

  const NS = "http://www.w3.org/2000/svg";
  const fmt = n => Math.round(n).toLocaleString("de-DE");
  const eur = n => `${fmt(n)} €`;
  const axisEur = n => fmt(n); // full numbers on both axes: one style, and they fit

  // ---------------------------------------------------------------- helpers
  function el(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  function niceStep(raw) {
    if (!(raw > 0)) return 1;
    const exp = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / exp;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * exp;
  }

  function niceMax(value, ticks = 4) {
    const step = niceStep(value / ticks);
    return { max: Math.ceil(value / step) * step || step, step };
  }

  // ---------------------------------------------------------------- tooltip
  const tip = document.createElement("div");
  tip.className = "viz-tip";
  tip.setAttribute("role", "status");
  tip.hidden = true;
  document.body.appendChild(tip);

  function showTip(rows, x, y) {
    tip.replaceChildren();
    rows.forEach(([value, label], i) => {
      const row = document.createElement("div");
      const strong = document.createElement(i === 0 ? "strong" : "span");
      strong.textContent = value; // untrusted text -> textContent only
      row.appendChild(strong);
      if (label) {
        const span = document.createElement("span");
        span.className = "viz-tip-label";
        span.textContent = ` ${label}`;
        row.appendChild(span);
      }
      tip.appendChild(row);
    });
    tip.hidden = false;
    const pad = 12;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let left = x + pad, top = y - h - pad;
    if (left + w > window.innerWidth - 8) left = x - w - pad;
    if (top < 8) top = y + pad;
    tip.style.left = `${Math.max(8, left) + window.scrollX}px`;
    tip.style.top = `${top + window.scrollY}px`;
  }

  function hideTip() {
    tip.hidden = true;
  }

  // Hover + keyboard focus on one hit target.
  function interactive(target, rows, onEnter, onLeave) {
    target.setAttribute("tabindex", "0");
    target.setAttribute("role", "img");
    target.setAttribute("aria-label", rows.map(r => r.filter(Boolean).join(" ")).join(", "));
    target.addEventListener("pointermove", e => { onEnter(); showTip(rows, e.clientX, e.clientY); });
    target.addEventListener("pointerleave", () => { onLeave(); hideTip(); });
    target.addEventListener("focus", () => {
      const r = target.getBoundingClientRect();
      onEnter();
      showTip(rows, r.left + r.width / 2, r.top);
    });
    target.addEventListener("blur", () => { onLeave(); hideTip(); });
  }

  function emptyState(container, text) {
    container.replaceChildren();
    const p = document.createElement("p");
    p.className = "viz-empty";
    p.textContent = text;
    container.appendChild(p);
  }

  // ---------------------------------------------------------------- histogram
  function autoBinWidth(values) {
    const span = Math.max(...values) - Math.min(...values);
    return Math.max(100, niceStep(span / 8));
  }

  function renderHistogram(container, values, binSetting) {
    if (values.length < 2) {
      emptyState(container, "Noch zu wenige Einträge für eine Verteilung.");
      return;
    }
    const binW = binSetting === "auto" ? autoBinWidth(values) : Number(binSetting);
    const lo = Math.floor(Math.min(...values) / binW) * binW;
    const hi = (Math.floor(Math.max(...values) / binW) + 1) * binW;
    const nBins = Math.round((hi - lo) / binW);
    const counts = new Array(nBins).fill(0);
    values.forEach(v => { counts[Math.min(nBins - 1, Math.floor((v - lo) / binW))] += 1; });

    const width = Math.max(260, container.clientWidth);
    const height = 220;
    const m = { t: 12, r: 12, b: 40, l: 36 };
    const pw = width - m.l - m.r, ph = height - m.t - m.b;
    const x = v => m.l + ((v - lo) / (hi - lo)) * pw;
    const yMax = niceMax(Math.max(...counts), 4);
    const yStep = Math.max(1, Math.ceil(yMax.step));
    const yTop = Math.ceil(Math.max(...counts) / yStep) * yStep;
    const y = c => m.t + ph - (c / yTop) * ph;

    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, height, class: "viz" });

    for (let c = 0; c <= yTop; c += yStep) {
      el("line", { x1: m.l, x2: width - m.r, y1: y(c), y2: y(c), class: "viz-grid" }, svg);
      el("text", { x: m.l - 6, y: y(c) + 4, class: "viz-tick", "text-anchor": "end" }, svg).textContent = fmt(c);
    }

    const tickStep = Math.max(binW, niceStep((hi - lo) / Math.max(2, Math.floor(pw / 90))));
    for (let t = Math.ceil(lo / tickStep) * tickStep; t <= hi; t += tickStep) {
      el("text", { x: x(t), y: height - m.b + 16, class: "viz-tick", "text-anchor": "middle" }, svg)
        .textContent = axisEur(t, hi);
    }
    el("text", { x: width - m.r, y: height - 4, class: "viz-axis-title", "text-anchor": "end" }, svg)
      .textContent = "€ pro Monat";

    counts.forEach((count, i) => {
      if (count === 0) return;
      const x0 = x(lo + i * binW) + 1;
      const w = Math.max(1, x(lo + (i + 1) * binW) - x(lo + i * binW) - 2); // 2px surface gap
      const yt = y(count), yb = y(0);
      const r = Math.min(4, w / 2, yb - yt);
      const bar = el("path", {
        d: `M${x0},${yb}V${yt + r}Q${x0},${yt} ${x0 + r},${yt}H${x0 + w - r}Q${x0 + w},${yt} ${x0 + w},${yt + r}V${yb}Z`,
        class: "viz-mark",
      }, svg);
      const hit = el("rect", { x: x0 - 1, y: m.t, width: w + 2, height: ph, class: "viz-hit" }, svg);
      const from = lo + i * binW, to = from + binW;
      interactive(
        hit,
        [[`${count} ${count === 1 ? "Eintrag" : "Einträge"}`], [`${eur(from)} bis unter ${eur(to)}`]],
        () => bar.classList.add("is-active"),
        () => bar.classList.remove("is-active"),
      );
    });

    el("line", { x1: m.l, x2: width - m.r, y1: y(0), y2: y(0), class: "viz-axis" }, svg);
    container.replaceChildren(svg);
  }

  // ---------------------------------------------------------------- scatter
  function renderScatter(container, points) {
    if (points.length < 1) {
      emptyState(container, "Noch keine Einträge mit Brutto- und Nettoangabe.");
      return;
    }
    const width = Math.max(260, container.clientWidth);
    const height = Math.min(420, Math.max(260, Math.round(width * 0.6)));
    const m = { t: 24, r: 44, b: 40, l: 48 };
    const pw = width - m.l - m.r, ph = height - m.t - m.b;
    const xs = niceMax(Math.max(...points.map(p => p.g)) * 1.05, 5);
    const ys = niceMax(Math.max(...points.map(p => p.n)) * 1.05, 4);
    const x = v => m.l + (v / xs.max) * pw;
    const y = v => m.t + ph - (v / ys.max) * ph;

    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, height, class: "viz" });

    for (let v = 0; v <= ys.max + 1e-9; v += ys.step) {
      el("line", { x1: m.l, x2: width - m.r, y1: y(v), y2: y(v), class: "viz-grid" }, svg);
      el("text", { x: m.l - 6, y: y(v) + 4, class: "viz-tick", "text-anchor": "end" }, svg).textContent = axisEur(v, ys.max);
    }
    const xEvery = Math.max(1, Math.ceil((xs.max / xs.step) / Math.max(2, Math.floor(pw / 64))));
    for (let i = 0, v = 0; v <= xs.max + 1e-9; i++, v += xs.step) {
      el("line", { x1: x(v), x2: x(v), y1: m.t, y2: m.t + ph, class: "viz-grid" }, svg);
      if (i % xEvery === 0) {
        el("text", { x: x(v), y: height - m.b + 16, class: "viz-tick", "text-anchor": "middle" }, svg).textContent = axisEur(v, xs.max);
      }
    }

    // Reference rays: net = share × gross. Recessive, labelled at their end.
    [0.5, 0.6, 0.7, 0.8].forEach(share => {
      const gx = Math.min(xs.max, ys.max / share);
      el("line", { x1: x(0), y1: y(0), x2: x(gx), y2: y(gx * share), class: "viz-ref" }, svg);
      const atRight = gx >= xs.max - 1e-9;
      el("text", {
        x: x(gx) + (atRight ? 4 : 0),
        y: y(gx * share) + (atRight ? 4 : -4),
        class: "viz-ref-label",
        "text-anchor": atRight ? "start" : "middle",
      }, svg).textContent = `${Math.round(share * 100)} %`;
    });

    el("line", { x1: m.l, x2: width - m.r, y1: y(0), y2: y(0), class: "viz-axis" }, svg);
    el("line", { x1: m.l, x2: m.l, y1: m.t, y2: y(0), class: "viz-axis" }, svg);
    el("text", { x: width - m.r, y: height - 4, class: "viz-axis-title", "text-anchor": "end" }, svg).textContent = "Brutto pro Monat (€)";
    el("text", { x: m.l, y: m.t - 10, class: "viz-axis-title", "text-anchor": "start" }, svg).textContent = "Netto pro Monat (€)";

    // Draw dots, then larger transparent hit areas on top (>= 24px).
    const dots = points.map(p => el("circle", { cx: x(p.g), cy: y(p.n), r: 5, class: "viz-dot" }, svg));
    points.forEach((p, i) => {
      const hit = el("circle", { cx: x(p.g), cy: y(p.n), r: 12, class: "viz-hit" }, svg);
      interactive(
        hit,
        [[`${eur(p.n)} netto`, `von ${eur(p.g)} brutto`], [p.p], [`Netto-Quote ${Math.round((p.n / p.g) * 100)} %`]],
        () => { dots[i].classList.add("is-active"); svg.appendChild(dots[i]); svg.appendChild(hit); },
        () => dots[i].classList.remove("is-active"),
      );
    });

    container.replaceChildren(svg);
  }

  // ---------------------------------------------------------------- wiring
  const gross = entries.map(e => e.g).filter(v => typeof v === "number");
  const net = entries.map(e => e.n).filter(v => typeof v === "number");
  const pairs = entries.filter(e => typeof e.g === "number" && typeof e.n === "number");

  const histGross = document.getElementById("hist-gross");
  const histNet = document.getElementById("hist-net");
  const scatter = document.getElementById("scatter");
  const binChips = document.getElementById("bin-chips");
  let bin = "auto";

  function renderAll() {
    hideTip();
    if (histGross) renderHistogram(histGross, gross, bin);
    if (histNet) renderHistogram(histNet, net, bin);
    if (scatter) renderScatter(scatter, pairs);
  }

  if (binChips) {
    binChips.addEventListener("click", e => {
      const chip = e.target.closest("button[data-bin]");
      if (!chip) return;
      bin = chip.dataset.bin;
      binChips.querySelectorAll("button[data-bin]").forEach(b => b.setAttribute("aria-pressed", String(b === chip)));
      renderAll();
    });
  }

  let lastWidth = 0;
  const ro = new ResizeObserver(() => {
    const w = document.getElementById("charts")?.clientWidth || 0;
    if (w !== lastWidth) { lastWidth = w; renderAll(); }
  });
  const root = document.getElementById("charts");
  if (root) ro.observe(root);
  renderAll();
})();
