/* House drawing: the two bays as they look from the street, every pixel shown.
 *
 * Used three times: the live view on the Play tab, the clickable wiring view
 * on the Test tab, and the effect preview on the Effects tab. Geometry comes
 * from /api/geometry in millimetres; frames arrive as base64 RGB bytes in the
 * same canonical pixel order the Pi uses for the LEDs.
 */
(function () {
  "use strict";

  const SIDE_LETTER = { top: "T", right: "R", bottom: "B", left: "L" };

  function decode(b64, n) {
    if (!b64) return null;
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.length >= n * 3 ? out : null;
  }

  class HouseView {
    constructor(canvas, opts) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.opts = Object.assign({ interactive: false, onPick: null, maxHeight: 0.72 }, opts || {});
      this.geo = null;
      this.frame = null;
      this.hover = null;
      this.winner = null;
      this.dirty = true;
      this.visible = true;
      if (this.opts.interactive) {
        canvas.addEventListener("mousemove", (e) => this._hover(e));
        canvas.addEventListener("mouseleave", () => { this.hover = null; this.dirty = true; });
        canvas.addEventListener("click", (e) => {
          const hit = this.hitTest(e);
          if (hit && this.opts.onPick) this.opts.onPick(hit, e);
        });
        canvas.style.cursor = "pointer";
      }
      window.addEventListener("resize", () => { this.dirty = true; this.layout(); });
      const loop = () => {
        if (this.dirty && this.visible && this.geo) this.draw();
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    }

    setGeometry(geo) {
      this.geo = geo;
      this.stripOf = new Int32Array(geo.pixels);
      geo.strips.forEach((s, i) => {
        for (let p = s.start; p < s.end; p++) this.stripOf[p] = i;
      });
      this.layout();
    }

    setFrame(b64) {
      if (!this.geo) return;
      const data = decode(b64, this.geo.pixels);
      if (data) { this.frame = data; this.dirty = true; }
    }

    setWinner(sid) {
      if (sid !== this.winner) { this.winner = sid; this.dirty = true; }
    }

    layout() {
      if (!this.geo) return;
      const parent = this.canvas.parentElement;
      const cssW = Math.max(160, parent.clientWidth);
      const maxH = Math.max(280, window.innerHeight * this.opts.maxHeight);
      const m = 18;
      const g = this.geo;
      const scale = Math.min((cssW - 2 * m) / g.width_mm, (maxH - 2 * m) / g.height_mm);
      const cssH = g.height_mm * scale + 2 * m;
      this.scale = scale;
      this.ox = (cssW - g.width_mm * scale) / 2;
      this.oy = m;
      const dpr = window.devicePixelRatio || 1;
      this.canvas.style.width = cssW + "px";
      this.canvas.style.height = cssH + "px";
      this.canvas.width = Math.round(cssW * dpr);
      this.canvas.height = Math.round(cssH * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.cssW = cssW;
      this.cssH = cssH;
      this.dirty = true;
    }

    // -- hit testing ---------------------------------------------------

    _toMM(e) {
      const r = this.canvas.getBoundingClientRect();
      return [(e.clientX - r.left - this.ox) / this.scale, (e.clientY - r.top - this.oy) / this.scale];
    }

    hitTest(e) {
      if (!this.geo) return null;
      const [mx, my] = this._toMM(e);
      const g = this.geo;
      let best = -1, bestD = Infinity;
      for (let i = 0; i < g.pixels; i++) {
        const dx = g.x[i] - mx, dy = g.y[i] - my;
        const d = dx * dx + dy * dy;
        if (d < bestD) { bestD = d; best = i; }
      }
      const reach = Math.max(60, 14 / this.scale);
      if (best >= 0 && bestD < reach * reach) {
        const s = g.strips[this.stripOf[best]];
        return { type: "strip", key: s.key, section: s.section, side: s.side };
      }
      for (const sec of g.sections) {
        const [x, y, w, h] = sec.rect;
        if (mx >= x && mx <= x + w && my >= y && my <= y + h) {
          return { type: "section", section: sec.id };
        }
      }
      return null;
    }

    _hover(e) {
      const hit = this.hitTest(e);
      const key = hit ? (hit.type === "strip" ? hit.key : "sec:" + hit.section) : null;
      if (key !== this.hover) { this.hover = key; this.dirty = true; }
    }

    // -- drawing -------------------------------------------------------

    draw() {
      this.dirty = false;
      const ctx = this.ctx, g = this.geo, s = this.scale;
      const X = (mm) => this.ox + mm * s;
      const Y = (mm) => this.oy + mm * s;

      ctx.clearRect(0, 0, this.cssW, this.cssH);
      const sky = ctx.createLinearGradient(0, 0, 0, this.cssH);
      sky.addColorStop(0, "#0b0714");
      sky.addColorStop(1, "#140b0b");
      ctx.fillStyle = sky;
      ctx.fillRect(0, 0, this.cssW, this.cssH);

      // Facade and stone surrounds, so it reads as the house.
      Object.values(g.floors).forEach((floor) => {
        const [x, y, w, h] = floor.rect;
        const pad = 110;
        ctx.fillStyle = "#2a1712";
        ctx.fillRect(X(x - pad), Y(y - pad), (w + pad * 2) * s, (h + pad * 2) * s);
        ctx.fillStyle = "#3b3441";
        ctx.fillRect(X(x - pad), Y(y + h + pad * 0.4), (w + pad * 2) * s, pad * 0.6 * s);
      });
      const cols = {};
      g.sections.forEach((sec) => {
        const [x, y, w, h] = sec.rect;
        const key = sec.floor + ":" + x;
        cols[key] = cols[key] || { x, y, w, bottom: y + h };
        cols[key].y = Math.min(cols[key].y, y);
        cols[key].bottom = Math.max(cols[key].bottom, y + h);
      });
      ctx.fillStyle = "#3b3441";
      Object.values(cols).forEach((c) => {
        const f = 38;
        ctx.fillRect(X(c.x - f), Y(c.y - f), (c.w + 2 * f) * s, (c.bottom - c.y + 2 * f) * s);
      });

      const frame = this.frame;
      // Glass, tinted by the average colour of that sash's pixels: roughly
      // what the silhouette paper will look like lit from its edges.
      g.sections.forEach((sec) => {
        const [x, y, w, h] = sec.rect;
        let r = 0, gg = 0, b = 0;
        if (frame) {
          for (let p = sec.start; p < sec.end; p++) {
            r += frame[p * 3]; gg += frame[p * 3 + 1]; b += frame[p * 3 + 2];
          }
          const n = Math.max(1, sec.end - sec.start);
          r /= n; gg /= n; b /= n;
        }
        ctx.fillStyle = "#06050a";
        ctx.fillRect(X(x), Y(y), w * s, h * s);
        const grad = ctx.createRadialGradient(X(x + w / 2), Y(y + h / 2), 2,
          X(x + w / 2), Y(y + h / 2), Math.max(w, h) * s * 0.75);
        grad.addColorStop(0, `rgba(${r | 0},${gg | 0},${b | 0},0.10)`);
        grad.addColorStop(1, `rgba(${r | 0},${gg | 0},${b | 0},0.42)`);
        ctx.fillStyle = grad;
        ctx.fillRect(X(x), Y(y), w * s, h * s);
        if (this.winner === sec.id) {
          ctx.strokeStyle = "#39ff14";
          ctx.lineWidth = 2;
          ctx.strokeRect(X(x) - 3, Y(y) - 3, w * s + 6, h * s + 6);
        }
        if (this.hover === "sec:" + sec.id) {
          ctx.strokeStyle = "rgba(255,255,255,0.55)";
          ctx.setLineDash([4, 4]);
          ctx.lineWidth = 1.5;
          ctx.strokeRect(X(x) + 4, Y(y) + 4, w * s - 8, h * s - 8);
          ctx.setLineDash([]);
        }
        ctx.fillStyle = "rgba(236,230,242,0.28)";
        ctx.font = `600 ${Math.max(10, Math.min(15, w * s * 0.1))}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(sec.id, X(x + w / 2), Y(y + h / 2));
      });

      // Hovered strip outline.
      if (this.hover && !this.hover.startsWith("sec:")) {
        const st = g.strips.find((t) => t.key === this.hover);
        if (st) {
          ctx.strokeStyle = "rgba(255,255,255,0.8)";
          ctx.lineWidth = Math.max(6, 70 * s);
          ctx.lineCap = "round";
          ctx.globalAlpha = 0.25;
          ctx.beginPath();
          ctx.moveTo(X(g.x[st.start]), Y(g.y[st.start]));
          ctx.lineTo(X(g.x[st.end - 1]), Y(g.y[st.end - 1]));
          ctx.stroke();
          ctx.globalAlpha = 1;
          ctx.fillStyle = "#fff";
          ctx.font = "600 11px system-ui, sans-serif";
          ctx.fillText(`${st.section} ${st.side}`, X((g.x[st.start] + g.x[st.end - 1]) / 2),
            Y((g.y[st.start] + g.y[st.end - 1]) / 2) - 12);
        }
      }

      // Pixels: a soft halo plus a bright core, dark pixels as faint dots.
      const spacing = 57 * s;
      const core = Math.max(1.3, Math.min(3.6, spacing * 0.36));
      ctx.globalCompositeOperation = "lighter";
      for (let i = 0; i < g.pixels; i++) {
        const px = X(g.x[i]), py = Y(g.y[i]);
        const r = frame ? frame[i * 3] : 0, gg = frame ? frame[i * 3 + 1] : 0, b = frame ? frame[i * 3 + 2] : 0;
        const lum = Math.max(r, gg, b);
        if (lum < 6) {
          ctx.fillStyle = "rgba(90,82,100,0.35)";
          ctx.beginPath(); ctx.arc(px, py, core * 0.55, 0, 6.283); ctx.fill();
          continue;
        }
        ctx.fillStyle = `rgba(${r},${gg},${b},${Math.min(0.35, lum / 600)})`;
        ctx.beginPath(); ctx.arc(px, py, core * 2.6, 0, 6.283); ctx.fill();
        ctx.fillStyle = `rgb(${r},${gg},${b})`;
        ctx.beginPath(); ctx.arc(px, py, core, 0, 6.283); ctx.fill();
      }
      ctx.globalCompositeOperation = "source-over";

      // Mark the data start of each strip so wiring direction is visible.
      if (this.opts.interactive) {
        ctx.fillStyle = "rgba(255,255,255,0.55)";
        g.strips.forEach((st) => {
          const i = st.start;
          ctx.beginPath();
          ctx.arc(X(g.x[i]), Y(g.y[i]), 1.4, 0, 6.283);
          ctx.fill();
        });
      }
    }
  }

  window.HouseView = HouseView;
  window.SIDE_LETTER = SIDE_LETTER;
})();
