/* Halloween window display - admin interface */
(function () {
  "use strict";

  let cfg = null;
  let palette = {};
  let sounds = [];
  let dirty = false;
  let geo = null;
  let catalogue = [];
  let fxByKey = {};
  let testInfo = { patterns: {}, sequences: {} };
  let testState = { active: false, lit: {} };
  let lastData = null;
  let activeTab = "play";

  let socket = null;
  let reconnectTimer = null;
  let pollTimer = null;
  let socketWorked = false;

  // Effects tab state
  let fxSel = null;             // { key, params }
  let editing = null;           // index of the playlist item being edited
  let plIndex = 0;              // playlist shown in the editor
  let fxPreviewKind = null;     // "effect" | "playlist" | null
  let fxKeepalive = null;
  let previewTimer = null;
  let lastPresses = null;
  let lastPlaying = "";

  const views = {};

  const SEQ_HELP = {
    walk: "One pixel at a time in data order. Checks direction and pixel count.",
    strips: "Each strip in turn. Checks which strip is really which.",
    sections: "Each sash in turn. Checks the controller outputs.",
    rgbw: "Red, green, blue, then white. Checks colour order.",
    ramp: "Fades 0 to 100%. Checks dimming is smooth with no flicker.",
    sync: "Everything flashes once a second. Checks both floors keep in step.",
    load: "Full white everywhere for a minute. Checks the power supplies.",
  };
  const TEST_SWATCHES = ["#FFFFFF", "#FF0000", "#00FF00", "#0000FF"];

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  // ---------------------------------------------------------------- utils

  function esc(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function getPath(object, path) {
    return path.split(".").reduce((acc, key) => (acc == null ? acc : acc[key]), object);
  }

  function setPath(object, path, value) {
    const keys = path.split(".");
    const last = keys.pop();
    let node = object;
    keys.forEach((key) => {
      if (node[key] == null || typeof node[key] !== "object") node[key] = {};
      node = node[key];
    });
    node[last] = value;
  }

  function uid() {
    return Math.random().toString(36).slice(2, 10);
  }

  function toast(message, bad) {
    const el = $("#toast");
    el.textContent = message;
    el.style.borderColor = bad ? "var(--bad)" : "var(--accent)";
    el.classList.add("show");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  function markDirty() {
    dirty = true;
    const save = $("#save");
    save.disabled = false;
    save.textContent = "Save changes";
  }

  function markClean() {
    dirty = false;
    const save = $("#save");
    save.disabled = true;
    save.textContent = "Saved";
  }

  async function api(path, options) {
    const response = await fetch(path, options);
    let body = null;
    try { body = await response.json(); } catch (err) { body = null; }
    if (!response.ok) throw new Error((body && body.error) || response.statusText);
    return body;
  }

  function post(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }

  function pct(v) { return Math.round((v || 0) * 100) + "%"; }

  function fmtDuration(seconds) {
    const s = Math.round(seconds);
    const m = Math.floor(s / 60);
    return m ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
  }

  // -------------------------------------------------------------- binding

  function bindInputs(root) {
    $$("[data-path]", root).forEach((el) => {
      if (el._bound) return;
      el._bound = true;
      const path = el.dataset.path;
      const event = (el.type === "checkbox" || el.tagName === "SELECT" || el.type === "color")
        ? "change" : "input";
      el.addEventListener(event, () => {
        let value;
        if (el.type === "checkbox") value = el.checked;
        else if (el.type === "number" || el.type === "range") value = el.value === "" ? 0 : parseFloat(el.value);
        else value = el.value;
        setPath(cfg, path, value);
        markDirty();
        if (path.startsWith("timing.")) updateCurveNote();
        if (path === "idle.master") $("#idle-master-v").textContent = pct(value);
      });
    });
  }

  function refreshInputs(root) {
    $$("[data-path]", root).forEach((el) => {
      const value = getPath(cfg, el.dataset.path);
      if (value === undefined) return;
      if (el.type === "checkbox") el.checked = !!value;
      else el.value = value === null ? (el.type === "color" ? "#000000" : "") : value;
    });
    if (cfg) $("#idle-master-v").textContent = pct(cfg.idle.master);
  }

  function makeSwatches(holder, colours, onPick) {
    holder.innerHTML = "";
    colours.forEach(([name, hex]) => {
      const dot = document.createElement("button");
      dot.type = "button";
      dot.className = "swatch";
      dot.style.background = hex;
      dot.title = name;
      dot.addEventListener("click", () => onPick(hex));
      holder.appendChild(dot);
    });
  }

  function buildPathSwatches(root) {
    $$(".swatches[data-for]", root).forEach((holder) => {
      if (holder._built) return;
      holder._built = true;
      const path = holder.dataset.for;
      makeSwatches(holder, Object.entries(palette).filter(([n]) => n !== "off"), (hex) => {
        setPath(cfg, path, hex);
        const input = $(`[data-path="${path}"]`);
        if (input) input.value = hex;
        markDirty();
      });
    });
  }

  // ------------------------------------------------------------- geometry

  async function loadGeometry() {
    geo = await api("/api/geometry");
    Object.values(views).forEach((v) => v.setGeometry(geo));
    renderStripGrid();
    renderAbTargets();
  }

  function setupViews() {
    views.play = new HouseView($("#house-play"), {
      interactive: true,
      onPick: (hit) => {
        post(`/api/test/section/${hit.section}`, { seconds: 5 })
          .then(() => toast(`${hit.section} on for 5s`))
          .catch((e) => toast(e.message, true));
      },
    });
    views.test = new HouseView($("#house-test"), {
      interactive: true,
      onPick: (hit) => {
        const target = hit.type === "strip" ? hit.key : hit.section;
        testSet([target], "toggle");
      },
    });
    views.effects = new HouseView($("#house-fx"), { interactive: false });
    Object.entries(views).forEach(([name, v]) => { v.visible = name === activeTab; });
  }

  // ------------------------------------------------------------ test mode

  function brush() {
    return {
      colour: $("#brush-colour").value,
      level: parseFloat($("#brush-level").value),
      pattern: $("#brush-pattern").value,
    };
  }

  function testSet(targets, on) {
    return post("/api/testmode/set", Object.assign({ targets, on }, brush()))
      .catch((e) => toast(e.message, true));
  }

  function renderStripGrid() {
    const host = $("#strip-grid");
    host.innerHTML = "";
    // Laid out like the house: upper sashes above lower, left window first.
    const ordered = geo.sections.slice().sort((p, q) => (p.rect[1] - q.rect[1]) || (p.rect[0] - q.rect[0]));
    ordered.forEach((sec) => {
      const row = document.createElement("div");
      row.className = "strip-row";
      const strips = geo.strips.filter((s) => s.section === sec.id);
      row.innerHTML = `<button class="chip sec" data-target="${esc(sec.id)}">${esc(sec.id)}</button>` +
        strips.map((s) => `<button class="chip st" data-target="${esc(s.key)}"
          title="${esc(sec.id)} ${esc(s.side)} strip, ${s.pixels} pixels">${SIDE_LETTER[s.side] || "?"}</button>`).join("");
      host.appendChild(row);
    });
    wireChips(host);
    updateChips();
  }

  function wireChips(root) {
    $$(".chip[data-target]", root).forEach((chip) => {
      if (chip._wired) return;
      chip._wired = true;
      chip.addEventListener("click", () => testSet([chip.dataset.target], "toggle"));
    });
  }

  function targetKeys(target) {
    if (!geo) return [];
    if (target === "all") return geo.strips.map((s) => s.key);
    const [kind, rest] = target.includes(":") && !/^[A-Z]{2}\d:\d+$/.test(target)
      ? [target.split(":")[0], target.split(":").slice(1).join(":")] : ["", target];
    let sids = [];
    if (kind === "floor") sids = (geo.floors[rest] || {}).sections || [];
    else if (kind === "window") sids = geo.windows[rest] || [];
    else if (geo.strips.some((s) => s.key === target)) return [target];
    else sids = [target];
    return geo.strips.filter((s) => sids.includes(s.section)).map((s) => s.key);
  }

  function updateChips() {
    const lit = testState.lit || {};
    $$(".chip[data-target]").forEach((chip) => {
      const keys = targetKeys(chip.dataset.target);
      const on = keys.filter((k) => lit[k]).length;
      chip.classList.toggle("on", keys.length > 0 && on === keys.length);
      chip.classList.toggle("part", on > 0 && on < keys.length);
      const first = keys.find((k) => lit[k]);
      chip.style.boxShadow = first && keys.length === 1 ? `inset 0 -3px 0 ${lit[first].colour}` : "";
    });
  }

  function renderAbTargets() {
    const opts = [];
    geo.sections.forEach((s) => opts.push([s.id, `${s.id} sash`]));
    Object.keys(geo.windows).forEach((w) => opts.push([`window:${w}`, `${w} window`]));
    opts.push(["floor:upstairs", "Upstairs bay"], ["floor:ground", "Ground bay"]);
    $$(".ab-target").forEach((sel) => {
      const current = sel.value || (sel.id === "ab-a-target" ? "UL1" : "UL2");
      sel.innerHTML = opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
      sel.value = opts.some(([v]) => v === current) ? current : opts[0][0];
    });
  }

  function abSide(prefix) {
    return {
      target: $(`#ab-${prefix}-target`).value,
      colour: $(`#ab-${prefix}-colour`).value,
      level: parseFloat($(`#ab-${prefix}-level`).value),
      pattern: $(`#ab-${prefix}-pattern`).value,
    };
  }

  function setAbSide(prefix, v) {
    $(`#ab-${prefix}-target`).value = v.target;
    $(`#ab-${prefix}-colour`).value = v.colour;
    $(`#ab-${prefix}-level`).value = v.level;
    $(`#ab-${prefix}-level-v`).textContent = pct(v.level);
    $(`#ab-${prefix}-pattern`).value = v.pattern;
  }

  function renderSequences() {
    const host = $("#seq-grid");
    host.innerHTML = Object.entries(testInfo.sequences).map(([key, label]) =>
      `<button class="seq" data-seq="${esc(key)}"><b>${esc(label)}</b><span>${esc(SEQ_HELP[key] || "")}</span></button>`
    ).join("");
    $$(".seq", host).forEach((btn) => btn.addEventListener("click", () => {
      const scope = $("#seq-scope").checked ? Object.keys(testState.lit || {}) : null;
      if ($("#seq-scope").checked && (!scope || !scope.length)) {
        toast("Nothing is lit to limit the sequence to", true);
        return;
      }
      post("/api/testmode/sequence", {
        kind: btn.dataset.seq, scope,
        colour: $("#seq-colour").value,
        dwell: parseFloat($("#seq-dwell").value),
        speed: parseFloat($("#seq-speed").value),
      }).then(() => toast(`${testInfo.sequences[btn.dataset.seq]} running`))
        .catch((e) => toast(e.message, true));
    }));
  }

  function updateTestUi(test) {
    testState = test || { active: false, lit: {} };
    $("#test-banner").hidden = !testState.active;
    document.body.classList.toggle("testing", !!testState.active);
    if (testState.active && testState.timeout_in != null) {
      $("#test-timeout").textContent = `Exits by itself in ${fmtDuration(testState.timeout_in)} if left alone.`;
    }
    const info = $("#test-info");
    info.hidden = !testState.info;
    info.textContent = testState.info || "";
    const running = testState.sequence ? testState.sequence.kind : null;
    $$(".seq").forEach((b) => b.classList.toggle("on", b.dataset.seq === running));
    updateChips();
    const lamp = testState.lamp_override;
    $("#lamp-level-v").textContent = lamp == null ? "normal" : pct(lamp);
  }

  function renderPower(power) {
    const host = $("#power-list");
    if (!power) return;
    host.innerHTML = power.map((p) => {
      const cls = p.percent > 90 ? "bad" : p.percent > 75 ? "warn" : "";
      return `<div class="prow"><b>${esc(p.name)}</b>
        <span class="muted">${p.watts} W, ${p.amps} A of a ${p.psu_watts} W supply (${p.percent}%). Everything at full white would be ${p.max_watts} W.</span>
        <div class="pbar"><div class="pfill ${cls}" style="width:${Math.min(100, p.percent)}%"></div></div></div>`;
    }).join("");
  }

  async function runDiagnostics(out) {
    out.innerHTML = `<p class="muted">Asking the controllers...</p>`;
    try {
      const data = await api("/api/diagnostics/controllers");
      out.innerHTML = `<table><thead><tr><th>Controller</th><th>Status</th><th>Details</th></tr></thead><tbody>` +
        data.controllers.map((c) => {
          if (!c.reachable) {
            return `<tr><td>${esc(c.name)}<br><span class="muted">${esc(c.host)}</span></td>
              <td class="no">Not reachable</td><td class="note">${esc(c.error || "")}</td></tr>`;
          }
          const notes = (c.notes || []).map((n) => `<div class="note">${esc(n)}</div>`).join("");
          return `<tr><td>${esc(c.name)}<br><span class="muted">${esc(c.host)}</span></td>
            <td class="ok">OK, ${c.latency_ms} ms</td>
            <td>WLED ${esc(c.version)}, ${esc(c.led_count)} LEDs (Pi sends ${c.expected_pixels}),
              Wi-Fi ${esc(c.signal)}% (${esc(c.rssi)} dBm), realtime ${c.realtime ? "on" : "off"}${notes}</td></tr>`;
        }).join("") + `</tbody></table>`;
    } catch (e) {
      out.innerHTML = `<p class="no">${esc(e.message)}</p>`;
    }
  }

  // --------------------------------------------------------------- effects

  function renderFxGrid() {
    const host = $("#fx-grid");
    host.innerHTML = catalogue.filter((f) => f.key !== "off").map((f) =>
      `<button class="fx-card" data-fx="${esc(f.key)}"><b>${esc(f.label)}</b><span>${esc(f.description)}</span></button>`
    ).join("");
    $$(".fx-card", host).forEach((card) => card.addEventListener("click", () => {
      editing = null;
      selectEffect(card.dataset.fx, null);
    }));
  }

  function defaults(key) {
    const out = {};
    (fxByKey[key] ? fxByKey[key].params : []).forEach((p) => { out[p.key] = p.default; });
    return out;
  }

  function selectEffect(key, params, item) {
    const fx = fxByKey[key];
    if (!fx) return;
    fxSel = { key, params: Object.assign(defaults(key), params || {}) };
    $$(".fx-card").forEach((c) => c.classList.toggle("on", c.dataset.fx === key));
    $("#fx-editor").hidden = false;
    $("#fx-editor-title").textContent = fx.label;
    $("#fx-editor-desc").textContent = fx.description;
    if (item) {
      $("#fx-duration").value = item.duration;
      $("#fx-fade").value = item.fade;
    }
    $("#fx-add").hidden = editing !== null;
    $("#fx-update").hidden = editing === null;
    $("#fx-cancel-edit").hidden = editing === null;
    renderParams();
    previewCurrent();
    renderPlaylist();
  }

  function renderParams() {
    const host = $("#fx-params");
    const fx = fxByKey[fxSel.key];
    host.innerHTML = "";
    fx.params.forEach((p) => {
      const value = fxSel.params[p.key];
      const label = document.createElement("label");
      if (p.kind === "colour") {
        label.innerHTML = `${esc(p.label)}<span class="colour-row"><input type="color" value="${esc(value)}"><span class="swatches"></span></span>`;
        const input = $("input", label);
        input.addEventListener("input", () => setParam(p.key, input.value));
        makeSwatches($(".swatches", label), Object.entries(palette), (hex) => {
          input.value = hex;
          setParam(p.key, hex);
        });
      } else if (p.kind === "choice") {
        label.innerHTML = `${esc(p.label)}<select>${p.options.map(([v, l]) =>
          `<option value="${esc(v)}"${v === value ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
        const sel = $("select", label);
        sel.addEventListener("change", () => setParam(p.key, sel.value));
      } else if (p.kind === "bool") {
        label.className = "check";
        label.innerHTML = `<input type="checkbox"${value ? " checked" : ""}> ${esc(p.label)}`;
        const cb = $("input", label);
        cb.addEventListener("change", () => setParam(p.key, cb.checked));
      } else {
        const show = (v) => (p.kind === "int" ? String(v) : (+v).toFixed(p.step < 0.1 ? 2 : 1));
        label.innerHTML = `${esc(p.label)} <span class="muted">${show(value)}</span>
          <input type="range" min="${p.min}" max="${p.max}" step="${p.step}" value="${value}">`;
        const range = $("input", label);
        const out = $("span", label);
        range.addEventListener("input", () => {
          const v = p.kind === "int" ? parseInt(range.value, 10) : parseFloat(range.value);
          out.textContent = show(v);
          setParam(p.key, v);
        });
      }
      host.appendChild(label);
    });
  }

  function setParam(key, value) {
    fxSel.params[key] = value;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(previewCurrent, 120);
  }

  function previewCurrent() {
    if (!fxSel) return;
    post("/api/effects/preview", { effect: fxSel.key, params: fxSel.params })
      .then(() => { fxPreviewKind = "effect"; startKeepalive(); updateFxCaption(); })
      .catch((e) => toast(e.message, true));
  }

  function startKeepalive() {
    clearInterval(fxKeepalive);
    fxKeepalive = setInterval(() => {
      if (activeTab === "effects" && fxPreviewKind) {
        post("/api/effects/preview/keepalive").catch(() => {});
      }
    }, 5000);
  }

  function updateFxCaption() {
    const cap = $("#fx-caption");
    if (fxPreviewKind === "playlist") {
      const pl = cfg.playlists[plIndex];
      cap.textContent = `Previewing the playlist "${pl ? pl.name : ""}". The house itself is not changed.`;
    } else if (fxPreviewKind === "effect" && fxSel) {
      cap.textContent = `Previewing ${fxByKey[fxSel.key].label}. The house itself is not changed.`;
    } else {
      cap.textContent = "Showing the house live. Choose an effect to preview it here.";
    }
  }

  // ------------------------------------------------------------- playlists

  function currentPlaylist() {
    return cfg.playlists[plIndex] || null;
  }

  function renderPlaylistSelect() {
    if (plIndex >= cfg.playlists.length) plIndex = Math.max(0, cfg.playlists.length - 1);
    $("#pl-select").innerHTML = cfg.playlists.map((p, i) =>
      `<option value="${i}"${i === plIndex ? " selected" : ""}>${esc(p.name)}${p.id === cfg.idle.playlist ? " (between rounds)" : ""}</option>`
    ).join("");
  }

  function renderPlaylist() {
    if (!cfg) return;
    renderPlaylistSelect();
    const pl = currentPlaylist();
    const body = $("#pl-items");
    if (!pl) { body.innerHTML = ""; return; }
    $("#pl-name").value = pl.name;
    $("#pl-shuffle").checked = !!pl.shuffle;
    const active = pl.id === cfg.idle.playlist;
    $("#pl-active-badge").hidden = !active;
    $("#pl-activate").hidden = active;
    const playingId = lastData && lastData.idle && !lastData.idle.override &&
      lastData.idle.playlist === pl.id ? lastData.idle.item : null;

    body.innerHTML = pl.items.map((item, i) => {
      const fx = fxByKey[item.effect];
      return `<tr data-i="${i}" class="${i === editing ? "editing" : ""} ${item.id === playingId ? "playing" : ""}">
        <td>${i + 1}</td>
        <td><span class="fx-name" data-edit="${i}">${esc(fx ? fx.label : item.effect)}</span></td>
        <td><input type="number" min="5" max="7200" step="5" value="${item.duration}" data-field="duration"></td>
        <td><input type="number" min="0" max="60" step="0.5" value="${item.fade}" data-field="fade"></td>
        <td><input type="checkbox"${item.enabled ? " checked" : ""} data-field="enabled"></td>
        <td class="acts">
          <button class="btn tiny" data-move="-1" title="Move up">&#9650;</button>
          <button class="btn tiny" data-move="1" title="Move down">&#9660;</button>
          <button class="btn tiny danger" data-remove title="Remove">&#10005;</button>
        </td></tr>`;
    }).join("") || `<tr><td colspan="6" class="muted">Empty. Choose an effect above and press <em>Add to playlist</em>.</td></tr>`;

    $$("tr[data-i]", body).forEach((row) => {
      const i = parseInt(row.dataset.i, 10);
      $$("[data-field]", row).forEach((input) => input.addEventListener("change", () => {
        const field = input.dataset.field;
        pl.items[i][field] = field === "enabled" ? input.checked : parseFloat(input.value);
        markDirty();
        renderPlaylistTotal();
      }));
      $("[data-edit]", row).addEventListener("click", () => {
        editing = i;
        const item = pl.items[i];
        selectEffect(item.effect, item.params, item);
      });
      $$("[data-move]", row).forEach((btn) => btn.addEventListener("click", () => {
        const j = i + parseInt(btn.dataset.move, 10);
        if (j < 0 || j >= pl.items.length) return;
        [pl.items[i], pl.items[j]] = [pl.items[j], pl.items[i]];
        if (editing === i) editing = j; else if (editing === j) editing = i;
        markDirty();
        renderPlaylist();
      }));
      $("[data-remove]", row).addEventListener("click", () => {
        pl.items.splice(i, 1);
        if (editing === i) stopEditing(); else if (editing !== null && editing > i) editing -= 1;
        markDirty();
        renderPlaylist();
      });
    });
    renderPlaylistTotal();
  }

  function renderPlaylistTotal() {
    const pl = currentPlaylist();
    if (!pl) return;
    const on = pl.items.filter((i) => i.enabled);
    const total = on.reduce((t, i) => t + (+i.duration || 0), 0);
    $("#pl-total").textContent = `${on.length} of ${pl.items.length} items on, ${fmtDuration(total)} before it repeats.`;
  }

  function stopEditing() {
    editing = null;
    $("#fx-add").hidden = false;
    $("#fx-update").hidden = true;
    $("#fx-cancel-edit").hidden = true;
  }

  function wirePlaylistControls() {
    $("#pl-select").addEventListener("change", () => {
      plIndex = parseInt($("#pl-select").value, 10);
      stopEditing();
      renderPlaylist();
    });
    $("#pl-name").addEventListener("input", () => {
      const pl = currentPlaylist();
      if (!pl) return;
      pl.name = $("#pl-name").value;
      markDirty();
      renderPlaylistSelect();
    });
    $("#pl-shuffle").addEventListener("change", () => {
      currentPlaylist().shuffle = $("#pl-shuffle").checked;
      markDirty();
    });
    $("#pl-activate").addEventListener("click", () => {
      cfg.idle.playlist = currentPlaylist().id;
      markDirty();
      renderPlaylist();
      toast("Will play between rounds once saved");
    });
    $("#pl-new").addEventListener("click", () => {
      cfg.playlists.push({ id: "pl" + uid(), name: "New playlist", shuffle: false, items: [] });
      plIndex = cfg.playlists.length - 1;
      stopEditing();
      markDirty();
      renderPlaylist();
      $("#pl-name").focus();
    });
    $("#pl-dup").addEventListener("click", () => {
      const src = currentPlaylist();
      const copy = JSON.parse(JSON.stringify(src));
      copy.id = "pl" + uid();
      copy.name = src.name + " copy";
      copy.items.forEach((i) => { i.id = uid(); });
      cfg.playlists.push(copy);
      plIndex = cfg.playlists.length - 1;
      markDirty();
      renderPlaylist();
    });
    $("#pl-del").addEventListener("click", () => {
      if (cfg.playlists.length <= 1) { toast("Keep at least one playlist", true); return; }
      const pl = currentPlaylist();
      if (!confirm(`Delete the playlist "${pl.name}"?`)) return;
      cfg.playlists.splice(plIndex, 1);
      if (cfg.idle.playlist === pl.id) cfg.idle.playlist = cfg.playlists[0].id;
      plIndex = 0;
      stopEditing();
      markDirty();
      renderPlaylist();
    });
    $("#pl-preview").addEventListener("click", () => {
      const pl = currentPlaylist();
      if (!pl.items.some((i) => i.enabled)) { toast("Nothing in this playlist is switched on", true); return; }
      post("/api/effects/preview", { playlist: pl })
        .then(() => { fxPreviewKind = "playlist"; startKeepalive(); updateFxCaption(); })
        .catch((e) => toast(e.message, true));
    });
    $("#fx-add").addEventListener("click", () => {
      if (!fxSel) return;
      const pl = currentPlaylist();
      pl.items.push({
        id: uid(), effect: fxSel.key, params: JSON.parse(JSON.stringify(fxSel.params)),
        duration: parseFloat($("#fx-duration").value) || 60,
        fade: parseFloat($("#fx-fade").value) || 0, enabled: true,
      });
      markDirty();
      renderPlaylist();
      toast(`Added to "${pl.name}"`);
    });
    $("#fx-update").addEventListener("click", () => {
      const pl = currentPlaylist();
      if (editing === null || !pl.items[editing]) return;
      Object.assign(pl.items[editing], {
        effect: fxSel.key, params: JSON.parse(JSON.stringify(fxSel.params)),
        duration: parseFloat($("#fx-duration").value) || 60,
        fade: parseFloat($("#fx-fade").value) || 0,
      });
      markDirty();
      stopEditing();
      renderPlaylist();
      toast("Item updated");
    });
    $("#fx-cancel-edit").addEventListener("click", () => { stopEditing(); renderPlaylist(); });
    $("#fx-reset").addEventListener("click", () => {
      if (!fxSel) return;
      fxSel.params = defaults(fxSel.key);
      renderParams();
      previewCurrent();
    });
    $("#fx-show").addEventListener("click", () => {
      if (!fxSel) { toast("Choose an effect first", true); return; }
      post("/api/effects/show", {
        effect: fxSel.key, params: fxSel.params,
        minutes: parseFloat($("#fx-show-minutes").value),
      }).then(() => toast(`${fxByKey[fxSel.key].label} is on the house`))
        .catch((e) => toast(e.message, true));
    });
    $("#fx-resume").addEventListener("click", () =>
      post("/api/effects/resume").then(() => toast("Back to the playlist")));
  }

  function updateNowPlaying(idle) {
    if (!idle) return;
    const label = idle.label || "-";
    let detail = "";
    if (idle.override) {
      detail = idle.remaining != null ? `Shown by hand, ${fmtDuration(idle.remaining)} left` : "Shown by hand until you go back to the playlist";
    } else if (idle.count) {
      detail = `${idle.name}, ${idle.position} of ${idle.count}, ${fmtDuration(idle.remaining || 0)} left`;
    }
    ["", "2"].forEach((n) => {
      $(`#np${n}-label`).textContent = label;
      $(`#np${n}-detail`).textContent = detail;
    });
    $("#np-resume").hidden = !idle.override;
  }

  // -------------------------------------------------------------- sections

  function renderSectionCards() {
    const list = $("#section-list");
    list.innerHTML = "";
    Object.keys(cfg.sections).forEach((sid) => {
      const section = cfg.sections[sid];
      const card = document.createElement("div");
      card.className = "section-card";
      card.style.borderLeftColor = section.colour;
      card.innerHTML = `
        <header>
          <span class="sid">${esc(sid)}</span>
          <label class="check"><input type="checkbox" data-path="sections.${sid}.enabled"> In play</label>
        </header>
        <label>Name <input type="text" data-path="sections.${sid}.label"></label>
        <label>Colour
          <span class="colour-row">
            <input type="color" data-path="sections.${sid}.colour">
            <span class="swatches" data-for="sections.${sid}.colour"></span>
          </span></label>
        <label>Winning flash colour <span class="muted">(Same uses the colour above)</span>
          <span class="colour-row">
            <input type="color" data-path="sections.${sid}.flash_colour">
            <button class="btn tiny" data-clear="sections.${sid}.flash_colour">Same</button>
          </span></label>
        <label>Winner sound
          <span class="sound-row">
            <select data-path="sections.${sid}.sound" data-sound></select>
            <button class="btn tiny" data-play="sections.${sid}.sound">Play</button>
          </span></label>
        <div class="grid3">
          <label>Odds weight <input type="number" min="0" step="0.1" data-path="sections.${sid}.weight"></label>
          <label>Width (mm) <input type="number" min="100" step="10" data-path="sections.${sid}.width_mm"></label>
          <label>Height (mm) <input type="number" min="100" step="10" data-path="sections.${sid}.height_mm"></label>
        </div>
        <div class="muted" style="font-size:12px;margin-bottom:4px">Strips in data order, ${section.pixels} pixels in total</div>
        <table class="strip-table"><thead><tr><th>#</th><th>Side</th><th>Pixels</th><th>Reversed</th><th></th></tr></thead>
          <tbody>${section.strips.map((st, k) => `
            <tr><td>${k + 1}</td>
              <td><select data-strip="${k}" data-field="side">${["top", "right", "bottom", "left"].map((s) =>
                `<option value="${s}"${s === st.side ? " selected" : ""}>${s}</option>`).join("")}</select></td>
              <td><input type="number" min="1" max="600" step="1" value="${st.pixels}" data-strip="${k}" data-field="pixels"></td>
              <td><input type="checkbox"${st.reverse ? " checked" : ""} data-strip="${k}" data-field="reverse"></td>
              <td><button class="btn tiny danger" data-strip-remove="${k}">&#10005;</button></td></tr>`).join("")}
          </tbody></table>
        <div class="row">
          <button class="btn tiny" data-strip-add>Add strip</button>
          <button class="btn tiny" data-test="${esc(sid)}">Light it</button>
        </div>`;
      list.appendChild(card);

      $$("[data-strip][data-field]", card).forEach((input) => input.addEventListener("change", () => {
        const k = parseInt(input.dataset.strip, 10);
        const f = input.dataset.field;
        section.strips[k][f] = f === "reverse" ? input.checked : f === "pixels" ? parseInt(input.value, 10) || 1 : input.value;
        section.pixels = section.strips.reduce((t, s) => t + (s.pixels || 0), 0);
        markDirty();
      }));
      $$("[data-strip-remove]", card).forEach((btn) => btn.addEventListener("click", () => {
        if (section.strips.length <= 1) return;
        section.strips.splice(parseInt(btn.dataset.stripRemove, 10), 1);
        markDirty();
        renderSectionCards();
      }));
      $("[data-strip-add]", card).addEventListener("click", () => {
        const sides = ["top", "right", "bottom", "left"];
        section.strips.push({ side: sides[section.strips.length % 4], pixels: 13, reverse: false });
        markDirty();
        renderSectionCards();
      });
    });

    $$("[data-clear]", list).forEach((btn) => btn.addEventListener("click", () => {
      setPath(cfg, btn.dataset.clear, null);
      const input = $(`[data-path="${btn.dataset.clear}"]`);
      if (input) input.value = "#000000";
      markDirty();
      toast("Flash colour follows the section colour");
    }));
    $$("[data-test]", list).forEach((btn) => btn.addEventListener("click", () => {
      post(`/api/test/section/${btn.dataset.test}`, { seconds: 5 })
        .then(() => toast(`${btn.dataset.test} on for 5s`))
        .catch((e) => toast(e.message, true));
    }));

    fillSoundSelects(list);
    buildPathSwatches(list);
    bindInputs(list);
    refreshInputs(list);
  }

  // ------------------------------------------------------------ controllers

  function renderControllers() {
    const host = $("#controller-list");
    host.innerHTML = "";
    (cfg.controllers || []).forEach((ctrl, index) => {
      const box = document.createElement("div");
      box.className = "controller";
      box.innerHTML = `
        <div class="grid3">
          <label>Name <input type="text" data-path="controllers.${index}.name"></label>
          <label>IP address <input type="text" data-path="controllers.${index}.host"></label>
          <label>DDP port <input type="number" step="1" data-path="controllers.${index}.port"></label>
          <label>Colour order
            <select data-path="controllers.${index}.colour_order">
              ${["RGB", "GRB", "BRG", "RBG", "GBR", "BGR"].map((o) => `<option value="${o}">${o}</option>`).join("")}
            </select></label>
          <label>White channel
            <select data-path="controllers.${index}.white_mode">
              <option value="none">RGB strip</option>
              <option value="auto">RGBW strip</option>
            </select></label>
          <label>Power supply (W) <input type="number" step="10" min="1" data-path="controllers.${index}.psu_watts"></label>
          <label class="check"><input type="checkbox" data-path="controllers.${index}.enabled"> Enabled</label>
        </div>
        <label>Sections, in LED output order
          <input type="text" data-list="controllers.${index}.sections" value="${esc((ctrl.sections || []).join(", "))}"></label>
        <p class="hint">${(ctrl.sections || []).map((s) => `${esc(s)}: ${(cfg.sections[s] || {}).pixels || 0}px`).join(" &middot; ")}
          &nbsp;=&nbsp; ${(ctrl.sections || []).reduce((t, s) => t + ((cfg.sections[s] || {}).pixels || 0), 0)} pixels total</p>`;
      host.appendChild(box);
    });
    $$("[data-list]", host).forEach((input) => input.addEventListener("input", () => {
      setPath(cfg, input.dataset.list, input.value.split(",").map((s) => s.trim()).filter(Boolean));
      markDirty();
    }));
    bindInputs(host);
    refreshInputs(host);
  }

  // ----------------------------------------------------------------- sounds

  function fillSoundSelects(root) {
    $$("select[data-sound]", root || document).forEach((select) => {
      const current = getPath(cfg, select.dataset.path) || "";
      select.innerHTML = `<option value="">None</option>` +
        sounds.map((name) => `<option value="${esc(name)}"${name === current ? " selected" : ""}>${esc(name)}</option>`).join("");
      if (current && !sounds.includes(current)) {
        const orphan = document.createElement("option");
        orphan.value = current;
        orphan.textContent = current + " (missing)";
        orphan.selected = true;
        select.appendChild(orphan);
      }
    });
  }

  function renderSoundList() {
    const list = $("#sound-list");
    list.innerHTML = "";
    if (!sounds.length) {
      list.innerHTML = `<li><span class="muted">No sounds yet. Run tools/make_sounds.py on the Pi for a starter set.</span></li>`;
      return;
    }
    sounds.forEach((name) => {
      const row = document.createElement("li");
      row.innerHTML = `<span>${esc(name)}</span>
        <button class="btn tiny" data-preview="${esc(name)}">Play</button>
        <button class="btn tiny danger" data-delete="${esc(name)}">Delete</button>`;
      list.appendChild(row);
    });
    $$("[data-preview]", list).forEach((btn) => btn.addEventListener("click", () => {
      post("/api/sounds/play", { name: btn.dataset.preview })
        .then((r) => toast(r.ok ? "Playing on the Pi" : r.status, !r.ok))
        .catch((e) => toast(e.message, true));
    }));
    $$("[data-delete]", list).forEach((btn) => btn.addEventListener("click", () => {
      api("/api/sounds/" + encodeURIComponent(btn.dataset.delete), { method: "DELETE" })
        .then((r) => { sounds = r.sounds; renderSoundList(); fillSoundSelects(); })
        .catch((e) => toast(e.message, true));
    }));
  }

  async function loadSounds() {
    const data = await api("/api/sounds");
    sounds = data.sounds || [];
    const select = $("#audio-device");
    const current = cfg.audio.device || "";
    select.innerHTML = `<option value="">System default</option>` +
      (data.devices || []).map((d) => `<option value="${esc(d)}"${d === current ? " selected" : ""}>${esc(d)}</option>`).join("");
    renderSoundList();
    fillSoundSelects();
  }

  // ------------------------------------------------------------------- live

  function applyPreview(data) {
    lastData = data;
    const pill = $("#state-pill");
    pill.textContent = data.state;
    pill.className = "pill" + (data.state === "test" ? " test" : data.state === "cycle" ? " live" :
      (data.state === "flash" || data.state === "hold") ? " win" : "");

    if (geo && data.pixels !== geo.pixels) { loadGeometry(); return; }

    if (activeTab === "play") {
      views.play.setFrame(data.live);
      views.play.setWinner(data.winner);
    } else if (activeTab === "test") {
      views.test.setFrame(data.live);
    } else if (activeTab === "effects") {
      if (data.fx) {
        views.effects.setFrame(data.fx);
      } else {
        if (fxPreviewKind) { fxPreviewKind = null; updateFxCaption(); }
        views.effects.setFrame(data.live);
      }
    }

    $("#progress").style.width = Math.round((data.progress || 0) * 100) + "%";
    const lamp = $("#lamp");
    const level = data.lamp || 0;
    const value = Math.round(40 + level * 215);
    lamp.style.background = `rgb(${value}, ${Math.round(value * 0.66)}, ${Math.round(value * 0.2)})`;
    lamp.style.boxShadow = level > 0.1 ? `0 0 ${6 + level * 14}px rgba(255,170,60,${level})` : "none";
    $("#trigger").disabled = !(data.state === "idle" || data.state === "spotlight");

    updateNowPlaying(data.idle);
    const playing = data.idle ? `${data.idle.playlist}/${data.idle.item}` : "";
    if (playing !== lastPlaying && activeTab === "effects" &&
        !$("#pl-items").contains(document.activeElement)) {
      renderPlaylist();
    }
    lastPlaying = playing;
    updateTestUi(data.test);
    renderPower(data.power);

    if (lastPresses !== null && data.presses !== lastPresses) {
      const count = $("#press-count");
      count.classList.add("flash");
      setTimeout(() => count.classList.remove("flash"), 400);
    }
    lastPresses = data.presses;
    $("#press-count").textContent = data.presses;
    $("#press-last").textContent = data.last_press_at
      ? `, last at ${new Date(data.last_press_at * 1000).toLocaleTimeString()}` : "";
  }

  function startPolling() {
    if (pollTimer) return;
    const rate = Math.min(10, Math.max(1, (cfg && cfg.web.preview_fps) || 10));
    pollTimer = setInterval(async () => {
      try {
        applyPreview(await api("/api/preview" + (activeTab === "effects" && fxPreviewKind ? "?fx=1" : "")));
      } catch (err) { /* the next tick will retry */ }
    }, Math.round(1000 / rate));
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  /* The websocket is the nice path. Without a websocket library behind
     uvicorn, or behind a proxy that blocks it, fall back to polling. */
  function connectSocket() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let opened = false;
    try {
      socket = new WebSocket(`${proto}://${location.host}/ws/preview`);
    } catch (err) {
      startPolling();
      return;
    }
    socket.onopen = () => { opened = true; socketWorked = true; stopPolling(); };
    socket.onmessage = (event) => {
      try { applyPreview(JSON.parse(event.data)); } catch (err) { /* ignore */ }
    };
    socket.onclose = () => {
      clearTimeout(reconnectTimer);
      startPolling();
      if (!opened && !socketWorked) {
        reconnectTimer = setTimeout(connectSocket, 30000);
      } else {
        $("#state-pill").textContent = "reconnecting";
        $("#state-pill").className = "pill err";
        reconnectTimer = setTimeout(connectSocket, 2000);
      }
    };
    socket.onerror = () => { try { socket.close(); } catch (err) { /* ignore */ } };
  }

  async function refreshStatus() {
    let status;
    try { status = await api("/api/status"); } catch (err) { return; }
    const cards = [
      ["Rounds played", status.rounds],
      ["Last winner", status.last_winner || "-"],
      ["Frame rate", status.fps + " fps"],
      ["Button", status.button.backend === "gpiozero" ? "connected" : status.button.backend],
      ["Audio", status.audio.available ? status.audio.device : status.audio.status],
      ["DDP", status.ddp_error ? "error" : "sending"],
    ];
    $("#status-cards").innerHTML = cards.map(([k, v]) =>
      `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("");
    $("#status-json").textContent = JSON.stringify(status, null, 2);
  }

  function updateCurveNote() {
    const t = cfg.timing;
    const low = t.min_step_ms / 1000, high = t.max_step_ms / 1000;
    const natural = (n) => (n <= 1 ? [high] : Array.from({ length: n }, (_, i) =>
      low + (high - low) * Math.pow(i / (n - 1), t.easing)));
    let steps = parseInt(t.steps, 10) || 0;
    let durations;
    if (steps > 0) {
      durations = natural(steps);
    } else {
      let n = 1;
      durations = natural(n);
      while (durations.reduce((a, b) => a + b, 0) < t.cycle_seconds && n < 400) {
        n += 1;
        durations = natural(n);
      }
      steps = n;
    }
    const total = durations.reduce((a, b) => a + b, 0) || 1;
    const factor = t.cycle_seconds / total;
    $("#curve-note").textContent =
      `${steps} hops in ${t.cycle_seconds}s, from ${Math.round(durations[0] * factor * 1000)}ms up to ` +
      `${Math.round(durations[durations.length - 1] * factor * 1000)}ms. ` +
      (factor < 0.85 ? "The curve is being squeezed to fit; fewer steps or a lower slowest dwell would keep the lazy ending." : "");
  }

  // ------------------------------------------------------------------- boot

  async function load() {
    const [data, fx, tinfo] = await Promise.all([
      api("/api/config"), api("/api/effects"), api("/api/testmode"),
    ]);
    cfg = data.config;
    palette = data.palette;
    catalogue = fx.effects;
    fxByKey = {};
    catalogue.forEach((f) => { fxByKey[f.key] = f; });
    testInfo = tinfo;

    const patternOpts = Object.entries(testInfo.patterns)
      .map(([k, l]) => `<option value="${esc(k)}">${esc(l)}</option>`).join("");
    $("#brush-pattern").innerHTML = patternOpts;
    $$(".pattern-select").forEach((s) => { s.innerHTML = patternOpts; });
    $("#brush-colour").value = cfg.test.colour;
    $("#brush-level").value = cfg.test.level;
    $("#brush-level-v").textContent = pct(cfg.test.level);
    $("#brush-pattern").value = cfg.test.pattern in testInfo.patterns ? cfg.test.pattern : "solid";
    $("#seq-dwell").value = cfg.test.dwell_seconds;
    $("#seq-speed").value = cfg.test.walk_speed;
    $("#wpp").textContent = cfg.render.watts_per_pixel;
    makeSwatches($("#brush-swatches"), [
      ...TEST_SWATCHES.map((h) => [h, h]),
      ...Object.entries(palette).filter(([n]) => n !== "off"),
    ], (hex) => { $("#brush-colour").value = hex; });

    plIndex = Math.max(0, cfg.playlists.findIndex((p) => p.id === cfg.idle.playlist));

    buildPathSwatches(document);
    bindInputs(document);
    refreshInputs(document);
    await loadGeometry();
    renderSequences();
    renderSectionCards();
    renderControllers();
    renderFxGrid();
    renderPlaylist();
    updateCurveNote();
    updateFxCaption();
    await loadSounds();
    markClean();
  }

  async function save() {
    try {
      const result = await api("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfg),
      });
      cfg = result.config;
      stopEditing();
      refreshInputs(document);
      await loadGeometry();
      renderSectionCards();
      renderControllers();
      renderPlaylist();
      fillSoundSelects();
      markClean();
      toast("Saved and applied");
    } catch (err) {
      toast(err.message, true);
    }
  }

  function showTab(name) {
    activeTab = name;
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
    Object.entries(views).forEach(([key, v]) => {
      v.visible = key === name;
      if (v.visible) v.layout();
    });
    if (name === "hardware") refreshStatus();
    if (name === "effects" && fxPreviewKind) post("/api/effects/preview/keepalive").catch(() => {});
  }

  function wireControls() {
    $$(".tab").forEach((tab) => tab.addEventListener("click", () => showTab(tab.dataset.tab)));
    $("#save").addEventListener("click", save);

    $("#trigger").addEventListener("click", () => {
      post("/api/trigger").then((r) => { if (!r.accepted) toast(r.reason, true); })
        .catch((e) => toast(e.message, true));
    });
    $("#stop").addEventListener("click", () => post("/api/stop").then(() => toast("Stopped")));
    $("#all-on").addEventListener("click", () =>
      post("/api/test/all", { seconds: 15 }).then(() => toast("All sections on for 15s")));
    $("#blackout").addEventListener("click", () => post("/api/blackout").then(() => toast("Blackout")));
    ["#np-next", "#np2-next"].forEach((id) => $(id).addEventListener("click", () =>
      post("/api/idle/next").then(() => toast("Next effect"))));
    $("#np-resume").addEventListener("click", () => post("/api/effects/resume"));

    // Test tab
    $("#test-exit-banner").addEventListener("click", () =>
      post("/api/testmode/exit").then(() => toast("Test mode off, back to normal")));
    $("#brush-level").addEventListener("input", () => { $("#brush-level-v").textContent = pct($("#brush-level").value); });
    $("#brush-apply").addEventListener("click", () =>
      post("/api/testmode/restyle", brush()).then(() => toast("Restyled")));
    $("#test-clear").addEventListener("click", () => post("/api/testmode/clear"));
    wireChips(document);
    ["a", "b"].forEach((p) => $(`#ab-${p}-level`).addEventListener("input", () => {
      $(`#ab-${p}-level-v`).textContent = pct($(`#ab-${p}-level`).value);
    }));
    $("#ab-show").addEventListener("click", () =>
      post("/api/testmode/compare", { a: abSide("a"), b: abSide("b") })
        .then(() => toast("A and B lit"))
        .catch((e) => toast(e.message, true)));
    $("#ab-swap").addEventListener("click", () => {
      const a = abSide("a"), b = abSide("b");
      setAbSide("a", b);
      setAbSide("b", a);
    });
    $("#ab-match").addEventListener("click", () => {
      const a = abSide("a");
      setAbSide("b", Object.assign({}, a, { target: $("#ab-b-target").value }));
    });
    $("#seq-stop").addEventListener("click", () => post("/api/testmode/sequence/stop"));
    let lampTimer = null;
    $("#lamp-level").addEventListener("input", () => {
      clearTimeout(lampTimer);
      lampTimer = setTimeout(() => post("/api/testmode/lamp", { level: parseFloat($("#lamp-level").value) }), 80);
    });
    $("#lamp-release").addEventListener("click", () => post("/api/testmode/lamp", { level: null }));
    $("#diag-run").addEventListener("click", () => runDiagnostics($("#diag-out")));
    $("#diag-run-2").addEventListener("click", () => runDiagnostics($("#diag-out-2")));

    wirePlaylistControls();

    $("#sound-upload").addEventListener("change", async (event) => {
      const files = Array.from(event.target.files || []);
      for (const file of files) {
        const body = new FormData();
        body.append("file", file);
        try {
          const result = await api("/api/sounds", { method: "POST", body });
          sounds = result.sounds;
        } catch (err) {
          toast(`${file.name}: ${err.message}`, true);
        }
      }
      event.target.value = "";
      renderSoundList();
      fillSoundSelects();
      toast("Upload complete");
    });

    document.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-play]");
      if (!btn) return;
      const name = getPath(cfg, btn.dataset.play);
      if (!name) { toast("No sound chosen", true); return; }
      post("/api/sounds/play", { name })
        .then((r) => toast(r.ok ? "Playing on the Pi" : r.status, !r.ok))
        .catch((e) => toast(e.message, true));
    });

    window.addEventListener("beforeunload", (event) => {
      if (dirty) { event.preventDefault(); event.returnValue = ""; }
    });
  }

  setupViews();
  wireControls();
  load().then(() => {
    connectSocket();
    refreshStatus();
    setInterval(refreshStatus, 5000);
  }).catch((err) => toast("Could not load: " + err.message, true));
})();
