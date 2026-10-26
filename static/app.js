"use strict";
const $ = (s) => document.querySelector(s);
const COLORS = ["--c0", "--c1", "--c2", "--c3", "--c4"];
let S = { plants: [], weigh: [], water: [], notes: [], webhook: false };
const act = () => S.plants.filter((p) => p.active);
const NOPLANTS = '<p class="empty">Noch keine Pflanzen angelegt. Das geht im Tab „Pflanzen“.</p>';
let plantsHintShown = false;

// ---------- Helfer
const WD = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const pad = (n) => String(n).padStart(2, "0");
function localInput(d = new Date()) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fmtTs(ts) {
  const d = new Date(ts);
  return `${WD[d.getDay()]} ${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const tsFrom = (sel) => new Date($(sel).value).toISOString();
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  setTimeout(() => (t.className = ""), 2200);
}
async function api(path, method = "GET", body) {
  const r = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401 && path !== "/logout") {
    window.location.href = "/login";
    throw new Error("Nicht angemeldet");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
const lastWeigh = (key) => {
  for (let i = S.weigh.length - 1; i >= 0; i--) if (key in S.weigh[i].values) return S.weigh[i];
  return null;
};
const sign = (n) => (n > 0 ? "+" + n : String(n));

// ---------- Tabs
document.querySelectorAll("nav button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll("nav button, .tab").forEach((e) => e.classList.remove("active"));
    b.classList.add("active");
    $("#tab-" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "discord") loadDiscord();
    if (b.dataset.tab === "log") renderLog();
    if (b.dataset.tab === "plants") renderPlants();
  })
);

// ---------- Wiegen
function renderWeighForm() {
  const keep = {};
  document.querySelectorAll("#w-fields input").forEach((i) => (keep[i.dataset.k] = i.value));
  $("#w-fields").innerHTML = !act().length ? NOPLANTS : act()
    .map((p) => {
      const lw = lastWeigh(p.key);
      return `<div class="plant"><div class="name"><b>${p.key}</b><small>${p.name}${
        lw ? ` · zuletzt ${lw.values[p.key]} g` : ""
      }</small><div class="delta" id="wd-${p.key}"></div></div>
      <input type="number" inputmode="numeric" data-k="${p.key}" placeholder="g" value="${keep[p.key] || ""}"></div>`;
    })
    .join("");
  document.querySelectorAll("#w-fields input").forEach((i) => i.addEventListener("input", liveDelta));
  liveDelta();
}
function liveDelta() {
  document.querySelectorAll("#w-fields input").forEach((i) => {
    const el = $("#wd-" + i.dataset.k);
    const lw = lastWeigh(i.dataset.k);
    const v = parseInt(i.value, 10);
    if (!lw || !v) return (el.textContent = "");
    const d = v - lw.values[i.dataset.k];
    const at = new Date($("#w-ts").value).getTime() || Date.now();
    const h = ((at - new Date(lw.ts)) / 36e5).toFixed(0);
    el.textContent = `${sign(d)} g in ~${h} h`;
    el.className = "delta " + (d < 0 ? "neg" : "pos");
  });
}
$("#w-ts").addEventListener("input", liveDelta);
$("#w-save").onclick = async () => {
  const values = {};
  document.querySelectorAll("#w-fields input").forEach((i) => i.value && (values[i.dataset.k] = i.value));
  try {
    await api("/api/weigh", "POST", { ts: tsFrom("#w-ts"), values });
    document.querySelectorAll("#w-fields input").forEach((i) => (i.value = ""));
    $("#w-ts").value = localInput();
    toast("Gewichte gespeichert");
    await load();
  } catch (e) { toast(e.message, true); }
};

// ---------- Gießen
function renderWaterForm() {
  const keep = {};
  document.querySelectorAll("#g-fields input").forEach((i) => (keep[i.dataset.k] = i.value));
  $("#g-fields").innerHTML = !act().length ? NOPLANTS : act()
    .map((p) => {
      const lastW = [...S.water].reverse().find((w) => p.key in w.ml);
      return `<div class="plant"><div class="name"><b>${p.key}</b><small>${p.name}${
        lastW ? ` · zuletzt ${lastW.ml[p.key]} ml` : ""
      }</small></div>
      <input type="number" inputmode="numeric" data-k="${p.key}" placeholder="ml" value="${keep[p.key] || ""}"></div>`;
    })
    .join("");
  document.querySelectorAll("#g-fields input").forEach((i) => i.addEventListener("input", calcMix));
  calcMix();
}
function calcMix() {
  const total = [...document.querySelectorAll("#g-fields input")].reduce((a, i) => a + (parseInt(i.value, 10) || 0), 0);
  const alfa = parseFloat($("#g-alfa").value.replace(",", ".")) || 0;
  const roots = parseFloat($("#g-roots").value.replace(",", ".")) || 0;
  $("#g-calc").textContent = total
    ? `Gesamt ${total} ml → AlfaBoost ${((alfa * total) / 1000).toFixed(2)} ml, Betta Roots ${((roots * total) / 1000).toFixed(2)} ml`
    : "";
}
["#g-alfa", "#g-roots"].forEach((s) => $(s).addEventListener("input", calcMix));
$("#g-save").onclick = async () => {
  const ml = {};
  document.querySelectorAll("#g-fields input").forEach((i) => i.value && (ml[i.dataset.k] = i.value));
  try {
    await api("/api/water", "POST", {
      ts: tsFrom("#g-ts"), ml, ph: $("#g-ph").value, alfa: $("#g-alfa").value,
      roots: $("#g-roots").value, extra: $("#g-extra").value,
    });
    document.querySelectorAll("#g-fields input").forEach((i) => (i.value = ""));
    $("#g-ts").value = localInput();
    toast("Gießen gespeichert");
    await load();
  } catch (e) { toast(e.message, true); }
};

// ---------- Verlauf
function renderChart() {
  const pts = S.weigh.slice(-24);
  if (pts.length < 2) return ($("#chart").innerHTML = '<p class="hint">Mindestens zwei Einträge nötig.</p>');
  const W = 600, H = 240, L = 52, R = 10, T = 10, B = 24;
  const all = pts.flatMap((s) => Object.values(s.values));
  let min = Math.min(...all), max = Math.max(...all);
  const pad10 = Math.max(10, (max - min) * 0.1);
  min -= pad10; max += pad10;
  const t0 = new Date(pts[0].ts).getTime(), t1 = new Date(pts.at(-1).ts).getTime();
  const x = (ts) => L + ((new Date(ts) - t0) / (t1 - t0 || 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - min) / (max - min)) * (H - T - B);
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  let svg = `<svg viewBox="0 0 ${W} ${H}">`;
  for (let i = 0; i <= 4; i++) {
    const v = min + ((max - min) * i) / 4, yy = y(v);
    svg += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" stroke="${css("--line")}"/>` +
      `<text x="${L - 6}" y="${yy + 4}" text-anchor="end" font-size="15" fill="${css("--muted")}">${Math.round(v)}</text>`;
  }
  S.plants.forEach((p, i) => {
    const line = pts.filter((s) => p.key in s.values).map((s) => `${x(s.ts)},${y(s.values[p.key])}`);
    if (line.length) svg += `<polyline fill="none" stroke="${css(COLORS[i % 5])}" stroke-width="2.5" points="${line.join(" ")}"/>`;
  });
  [pts[0], pts.at(-1)].forEach((s, i) => {
    svg += `<text x="${i ? W - R : L}" y="${H - 6}" text-anchor="${i ? "end" : "start"}" font-size="15" fill="${css("--muted")}">${fmtTs(s.ts)}</text>`;
  });
  $("#chart").innerHTML = svg + "</svg>";
  $("#legend").innerHTML = S.plants
    .map((p, i) => `<span><i style="background:var(${COLORS[i % 5]})"></i>${p.key}</span>`).join("");
}
function renderLog() {
  renderChart();
  $("#weigh-list").innerHTML = [...S.weigh].reverse().slice(0, 30).map((s) => {
    const vals = S.plants.filter((p) => p.key in s.values).map((p) => {
      const d = s.delta[p.key];
      return `${p.key} ${s.values[p.key]}${d !== undefined ? ` <span class="delta ${d < 0 ? "neg" : "pos"}">(${sign(d)})</span>` : ""}`;
    }).join(" · ");
    return `<div class="item"><div><div class="when">${fmtTs(s.ts)}${s.hours ? ` · ${s.hours} h seit zuvor` : ""}</div><div class="vals">${vals}</div></div>
      <button data-del="weigh/${s.ids}">✕</button></div>`;
  }).join("") || '<p class="hint">Noch nichts.</p>';
  $("#water-list").innerHTML = [...S.water].reverse().slice(0, 20).map((w) => {
    const ml = S.plants.filter((p) => p.key in w.ml).map((p) => `${p.key} ${w.ml[p.key]} ml`).join(" · ");
    const mix = [w.ph != null && `pH ${w.ph}`, w.alfa && `Alfa ${w.alfa}`, w.roots && `Roots ${w.roots}`, w.extra].filter(Boolean).join(" · ");
    return `<div class="item"><div><div class="when">${fmtTs(w.ts)}</div><div class="vals">${ml}</div>${mix ? `<div class="when">${mix}</div>` : ""}</div>
      <button data-del="water/${w.ids}">✕</button></div>`;
  }).join("") || '<p class="hint">Noch nichts.</p>';
  $("#note-list").innerHTML = S.notes.map((n) =>
    `<div class="item"><div><div class="when">${fmtTs(n.ts)}${n.plant ? " · " + n.plant : ""}</div><div>${esc(n.text)}</div></div>
     <button data-del="note/${n.id}">✕</button></div>`).join("") || '<p class="hint">Noch keine Notizen.</p>';
  document.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Eintrag löschen?")) return;
    await api("/api/" + b.dataset.del, "DELETE");
    await load();
  }));
}
const esc = (t) => t.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
$("#n-save").onclick = async () => {
  const text = $("#n-text").value.trim();
  if (!text) return;
  await api("/api/note", "POST", { text, plant: $("#n-plant").value });
  $("#n-text").value = "";
  await load();
};

// ---------- Pflanzen
function renderPlants() {
  const live = act(), arch = S.plants.filter((p) => !p.active);
  $("#p-list").innerHTML = live.map((p) =>
    `<div class="item prow"><input class="pn" data-k="${esc(p.key)}" value="${esc(p.name)}" maxlength="60">` +
    `<input class="pk" data-k="${esc(p.key)}" value="${esc(p.key)}" maxlength="6">` +
    `<button data-psave="${esc(p.key)}" title="Speichern">💾</button>` +
    `<button data-prm="${esc(p.key)}" title="Entfernen">🗑</button></div>`
  ).join("") || '<p class="empty">Noch keine Pflanzen. Lege oben die erste an.</p>';
  $("#p-arch-card").hidden = !arch.length;
  $("#p-arch").innerHTML = arch.map((p) =>
    `<div class="item"><div>${esc(p.key)} · ${esc(p.name)}</div><button data-prestore="${esc(p.key)}">↩ zurück</button></div>`
  ).join("");
  document.querySelectorAll("[data-psave]").forEach((b) => (b.onclick = async () => {
    const k = b.dataset.psave;
    const name = document.querySelector(`.pn[data-k="${k}"]`).value;
    const key = document.querySelector(`.pk[data-k="${k}"]`).value;
    try { await api("/api/plants/" + encodeURIComponent(k), "PUT", { name, key }); toast("Gespeichert"); await load(); }
    catch (e) { toast(e.message, true); }
  }));
  document.querySelectorAll("[data-prm]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Pflanze entfernen? Hat sie schon Einträge, wird sie nur archiviert.")) return;
    try {
      const r = await api("/api/plants/" + encodeURIComponent(b.dataset.prm), "DELETE");
      toast(r.archived ? "Archiviert" : "Gelöscht");
      await load();
    } catch (e) { toast(e.message, true); }
  }));
  document.querySelectorAll("[data-prestore]").forEach((b) => (b.onclick = async () => {
    try { await api("/api/plants/" + encodeURIComponent(b.dataset.prestore), "PUT", { active: true }); await load(); }
    catch (e) { toast(e.message, true); }
  }));
}
$("#p-add").onclick = async () => {
  const name = $("#p-name").value.trim();
  if (!name) return toast("Name fehlt", true);
  try {
    await api("/api/plants", "POST", { name, key: $("#p-key").value });
    $("#p-name").value = ""; $("#p-key").value = "";
    toast("Pflanze angelegt");
    await load();
  } catch (e) { toast(e.message, true); }
};
$("#p-name").addEventListener("keydown", (e) => e.key === "Enter" && $("#p-add").click());

// ---------- Discord
let discordText = "";
async function loadDiscord() {
  const d = await api("/api/discord?water=" + ($("#d-water").checked ? 1 : 0));
  discordText = d.text;
  $("#d-text").textContent = d.text.replace(/^```\n?|\n?```$/g, "");
  $("#d-post").disabled = !S.webhook;
  $("#d-hint").textContent = S.webhook ? "" : "Zum direkten Senden DISCORD_WEBHOOK_URL in der docker-compose.yml setzen.";
}
$("#d-water").onchange = loadDiscord;
$("#d-copy").onclick = async () => {
  try { await navigator.clipboard.writeText(discordText); toast("Kopiert"); }
  catch { const r = document.createRange(); r.selectNodeContents($("#d-text")); getSelection().removeAllRanges(); getSelection().addRange(r); toast("Text markiert – manuell kopieren", true); }
};
$("#d-post").onclick = async () => {
  try { await api("/api/discord/post", "POST", { text: discordText }); toast("An Discord gesendet"); }
  catch (e) { toast(e.message, true); }
};

// ---------- Laden
async function load() {
  try {
    S = await api("/api/state");
    $("#status").textContent = "";
    $("#logout").hidden = !S.auth;
  } catch (e) { $("#status").textContent = "offline"; return; }
  renderWeighForm();
  renderWaterForm();
  $("#n-plant").innerHTML = '<option value="">alle</option>' + act().map((p) => `<option>${p.key}</option>`).join("");
  if ($("#tab-log").classList.contains("active")) renderLog();
  if ($("#tab-plants").classList.contains("active")) renderPlants();
  if (!S.plants.length && !plantsHintShown) {
    plantsHintShown = true;
    document.querySelector('nav button[data-tab="plants"]').click();
  }
}
$("#logout").onclick = async () => {
  if (!confirm("Abmelden?")) return;
  await fetch("/logout", { method: "POST" });
  window.location.href = "/login";
};
$("#w-ts").value = localInput();
$("#g-ts").value = localInput();
load();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
