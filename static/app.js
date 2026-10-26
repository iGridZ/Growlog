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
const r5 = (n) => Math.round(n / 5) * 5;
const dnum = (n) => String(n).replace(".", ",");
const autoVal = (k, f) => ((S.assist || {})[k] || {})[f];
function slotText(a) {
  const t = fmtTs(a.slot_at);
  return a.slot_today ? `heute ${t.slice(-5)}` : t;
}
function renderAssist() {
  const rows = act().filter((p) => (S.assist || {})[p.key]);
  $("#assist-card").hidden = !rows.length;
  $("#assist").innerHTML = rows.map((p) => {
    const a = S.assist[p.key];
    const bits = [];
    if (a.target) bits.push(`Ziel ${a.target} g${a.target_manual ? "" : " (auto)"}`);
    if (a.threshold) bits.push(`Schwelle ${a.threshold} g${a.threshold_manual ? "" : " (auto)"}`);
    return `<div class="as"><div class="as-head"><span><b>${esc(p.key)}</b> ${a.current} g</span>` +
      `<span class="muted">${a.pct != null ? a.pct + " %" : ""}</span></div>` +
      (a.pct != null ? `<div class="bar ${a.pct < 25 ? "low" : ""}"><i style="width:${a.pct}%"></i></div>` : "") +
      `<small>${bits.join(" · ")}</small>` +
      (a.rate_lit != null ? `<small>Verbrauch: Licht ~${dnum(a.rate_lit)} g/h · Dunkel ~${dnum(a.rate_dark)} g/h · ≈ ${a.use24} g/Tag</small>` : "") +
      (a.slot_at ? `<div class="as-due">Gießen: <b>${slotText(a)}</b> · erwartet ${a.slot_expected} g → ca. <b>${a.suggest_ml} ml</b></div>` : "") +
      (a.due_at ? `<small>Schwelle erreicht laut Modell: ${fmtTs(a.due_at)}</small>` : "") + `</div>`;
  }).join("") + '<p class="hint">Richtwerte aus deinen Messungen: Licht und Dunkel getrennt, neuere Messungen zählen stärker, Wachstum eingerechnet. Der Fingertest entscheidet. Ziel und Schwelle kannst du im Tab „Pflanzen“ selbst setzen.</p>';
}

// ---------- Tabs
function showTab(name) {
  document.querySelectorAll("nav button, .tab").forEach((e) => e.classList.remove("active"));
  const nb = document.querySelector(`nav button[data-tab="${name}"]`);
  if (nb) nb.classList.add("active");
  $("#tab-" + name).classList.add("active");
  if (name === "discord") loadDiscord();
  if (name === "log") renderLog();
  if (name === "plants") renderPlants();
  if (name === "account") renderAccount();
}
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("#me-btn").onclick = () => showTab("account");

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
    const A = (S.assist || {})[i.dataset.k];
    const ml = A && A.target && v < A.target - 5 ? ` · ${r5((A.target - v) / (A.ratio || 1))} ml bis Ziel` : "";
    el.textContent = `${sign(d)} g in ~${h} h${ml}`;
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
      const A = (S.assist || {})[p.key];
      return `<div class="plant"><div class="name"><b>${p.key}</b><small>${p.name}${
        lastW ? ` · zuletzt ${lastW.ml[p.key]} ml` : ""
      }</small>${A && A.suggest_ml > 0 ? `<div><button type="button" class="link sug" data-sug="${p.key}" data-ml="${A.suggest_ml}">Vorschlag ${A.suggest_ml} ml</button></div>` : ""}</div>
      <input type="number" inputmode="numeric" data-k="${p.key}" placeholder="ml" value="${keep[p.key] || ""}"></div>`;
    })
    .join("");
  document.querySelectorAll("#g-fields input").forEach((i) => i.addEventListener("input", calcMix));
  document.querySelectorAll("[data-sug]").forEach((b) => (b.onclick = () => {
    document.querySelector(`#g-fields input[data-k="${b.dataset.sug}"]`).value = b.dataset.ml;
    calcMix();
  }));
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
function renderChart(weigh = S.weigh, plants = S.plants, el = "#chart", lg = "#legend") {
  const pts = weigh.slice(-24);
  if (pts.length < 2) return ($(el).innerHTML = '<p class="hint">Mindestens zwei Einträge nötig.</p>');
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
  plants.forEach((p, i) => {
    const line = pts.filter((s) => p.key in s.values).map((s) => `${x(s.ts)},${y(s.values[p.key])}`);
    if (line.length) svg += `<polyline fill="none" stroke="${css(COLORS[i % 5])}" stroke-width="2.5" points="${line.join(" ")}"/>`;
  });
  [pts[0], pts.at(-1)].forEach((s, i) => {
    svg += `<text x="${i ? W - R : L}" y="${H - 6}" text-anchor="${i ? "end" : "start"}" font-size="15" fill="${css("--muted")}">${fmtTs(s.ts)}</text>`;
  });
  $(el).innerHTML = svg + "</svg>";
  $(lg).innerHTML = plants
    .map((p, i) => `<span><i style="background:var(${COLORS[i % 5]})"></i>${esc(p.key)}</span>`).join("");
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
    `<button data-prm="${esc(p.key)}" title="Entfernen">🗑</button>` +
    `<div class="prow2"><input class="pt" type="number" inputmode="numeric" data-k="${esc(p.key)}" value="${p.target || ""}" ` +
    `placeholder="Ziel${autoVal(p.key, "auto_target") ? " (auto " + autoVal(p.key, "auto_target") + ")" : ""}">` +
    `<input class="pth" type="number" inputmode="numeric" data-k="${esc(p.key)}" value="${p.threshold || ""}" ` +
    `placeholder="Schwelle${autoVal(p.key, "auto_threshold") ? " (auto " + autoVal(p.key, "auto_threshold") + ")" : ""}"></div></div>`
  ).join("") || '<p class="empty">Noch keine Pflanzen. Lege oben die erste an.</p>';
  $("#p-arch-card").hidden = !arch.length;
  $("#p-arch").innerHTML = arch.map((p) =>
    `<div class="item"><div>${esc(p.key)} · ${esc(p.name)}</div><button data-prestore="${esc(p.key)}">↩ zurück</button></div>`
  ).join("");
  document.querySelectorAll("[data-psave]").forEach((b) => (b.onclick = async () => {
    const k = b.dataset.psave;
    const name = document.querySelector(`.pn[data-k="${k}"]`).value;
    const key = document.querySelector(`.pk[data-k="${k}"]`).value;
    try { await api("/api/plants/" + encodeURIComponent(k), "PUT", {
      name, key,
      target: document.querySelector(`.pt[data-k="${k}"]`).value,
      threshold: document.querySelector(`.pth[data-k="${k}"]`).value,
    }); toast("Gespeichert"); await load(); }
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

// ---------- Konto
$("#acc-share").onchange = async (e) => {
  try { await api("/api/me", "PUT", { share: e.target.checked }); S.me.share = e.target.checked; toast(e.target.checked ? "Profil sichtbar" : "Profil privat"); }
  catch (err) { toast(err.message, true); e.target.checked = !e.target.checked; }
};
async function loadCommunity() {
  let d; try { d = await api("/api/community"); } catch { return; }
  $("#comm-card").hidden = !d.users.length;
  $("#prof-view").hidden = true;
  $("#comm-list").innerHTML = d.users.map((u) => `<div class="item"><div>🌱 <b>${esc(u.username)}</b></div><button data-prof="${u.id}">Ansehen</button></div>`).join("");
  document.querySelectorAll("[data-prof]").forEach((b) => (b.onclick = async () => {
    try {
      const p = await api("/api/profile/" + b.dataset.prof);
      $("#prof-title").textContent = p.username;
      renderChart(p.weigh, p.plants, "#pchart", "#plegend");
      $("#pwater").innerHTML = [...p.water].reverse().slice(0, 10).map((w) => {
        const ml = p.plants.filter((x) => x.key in w.ml).map((x) => `${x.key} ${w.ml[x.key]} ml`).join(" · ");
        const mix = [w.ph != null && `pH ${w.ph}`, w.alfa && `Alfa ${w.alfa}`, w.roots && `Roots ${w.roots}`, w.extra].filter(Boolean).join(" · ");
        return `<div class="item"><div><div class="when">${fmtTs(w.ts)}</div><div class="vals">${esc(ml)}</div>${mix ? `<div class="when">${esc(mix)}</div>` : ""}</div></div>`;
      }).join("") || '<p class="hint">Noch nichts.</p>';
      $("#pweigh").innerHTML = [...p.weigh].reverse().slice(0, 10).map((s) =>
        `<div class="item"><div><div class="when">${fmtTs(s.ts)}</div><div class="vals">${p.plants.filter((x) => x.key in s.values).map((x) => `${esc(x.key)} ${s.values[x.key]}`).join(" · ")}</div></div></div>`
      ).join("") || '<p class="hint">Noch nichts.</p>';
      $("#prof-view").hidden = false;
      $("#prof-view").scrollIntoView({ behavior: "smooth" });
    } catch (err) { toast(err.message, true); }
  }));
}
function copyText(text) {
  return navigator.clipboard.writeText(text).then(() => toast("Kopiert")).catch(() => toast("Bitte von Hand kopieren", true));
}
function renderAccount() {
  const me = S.me || { username: "", role: "user" };
  $("#acc-name").textContent = me.username;
  $("#acc-role").textContent = me.role === "admin" ? "(Admin)" : "";
  $("#pw-user").value = me.username;
  $("#acc-hook").placeholder = S.webhook ? "Webhook gespeichert – zum Ändern neue Adresse einfügen" : "https://discord.com/api/webhooks/…";
  $("#hook-remove").hidden = !S.webhook;
  $("#acc-share").checked = !!me.share;
  loadCommunity();
  if (me.role === "admin") { loadUsers(); loadConfig(); }
}
async function loadConfig() {
  try { const c = await api("/api/config"); $("#cfg-tz").value = c.tz; $("#cfg-proxy").checked = c.trust_proxy; } catch {}
}
$("#cfg-save").onclick = async () => {
  try { await api("/api/config", "PUT", { tz: $("#cfg-tz").value, trust_proxy: $("#cfg-proxy").checked }); toast("Gespeichert"); await load(); }
  catch (err) { toast(err.message, true); }
};
$("#pw-form").onsubmit = async (e) => {
  e.preventDefault();
  if ($("#pw-new").value !== $("#pw-new2").value) return toast("Die neuen Passwörter stimmen nicht überein", true);
  try {
    await api("/api/me", "PUT", { current_password: $("#pw-cur").value, new_password: $("#pw-new").value });
    ["#pw-cur", "#pw-new", "#pw-new2"].forEach((s) => ($(s).value = ""));
    toast("Passwort geändert");
  } catch (err) { toast(err.message, true); }
};
$("#hook-save").onclick = async () => {
  const v = $("#acc-hook").value.trim();
  if (!v) return toast("Bitte eine Webhook-Adresse einfügen", true);
  try { await api("/api/me", "PUT", { webhook: v }); $("#acc-hook").value = ""; toast("Webhook gespeichert"); await load(); renderAccount(); }
  catch (err) { toast(err.message, true); }
};
$("#hook-remove").onclick = async () => {
  if (!confirm("Webhook entfernen?")) return;
  await api("/api/me", "PUT", { webhook: "" }); toast("Webhook entfernt"); await load(); renderAccount();
};
async function loadUsers() {
  let d;
  try { d = await api("/api/users"); } catch { return; }
  const myName = (S.me || {}).username;
  $("#user-list").innerHTML = d.users.map((u) => {
    const self = u.username === myName;
    return `<div class="item"><div><b>${esc(u.username)}</b> <span class="muted">${u.role === "admin" ? "Admin" : "Nutzer"}${u.active ? "" : " · gesperrt"}</span></div>` +
      `<div class="ubtns"><button data-upw="${u.id}" title="Passwort zurücksetzen">🔑</button>` +
      (self ? "" : `<button data-uact="${u.id}" data-on="${u.active ? 1 : 0}" title="${u.active ? "Sperren" : "Entsperren"}">${u.active ? "⏸" : "▶"}</button>` +
        `<button data-urole="${u.id}" data-r="${u.role}" title="Rolle wechseln">${u.role === "admin" ? "⬇" : "👑"}</button>` +
        `<button data-udel="${u.id}" data-n="${esc(u.username)}" title="Löschen">🗑</button>`) + `</div></div>`;
  }).join("");
  const act = async (id, body, ok) => { try { await api("/api/users/" + id, "PUT", body); toast(ok); loadUsers(); } catch (err) { toast(err.message, true); } };
  document.querySelectorAll("[data-upw]").forEach((b) => (b.onclick = () => {
    const name = d.users.find((u) => u.id == b.dataset.upw).username;
    const pw = prompt(`Neues Passwort für ${name} (mind. 8 Zeichen):`);
    if (pw) act(b.dataset.upw, { password: pw }, "Passwort gesetzt");
  }));
  document.querySelectorAll("[data-uact]").forEach((b) => (b.onclick = () => act(b.dataset.uact, { active: b.dataset.on !== "1" }, "Gespeichert")));
  document.querySelectorAll("[data-urole]").forEach((b) => (b.onclick = () => act(b.dataset.urole, { role: b.dataset.r === "admin" ? "user" : "admin" }, "Rolle geändert")));
  document.querySelectorAll("[data-udel]").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Benutzer „${b.dataset.n}“ und ALLE seine Daten unwiderruflich löschen?`)) return;
    try { await api("/api/users/" + b.dataset.udel, "DELETE"); toast("Benutzer gelöscht"); loadUsers(); } catch (err) { toast(err.message, true); }
  }));
  $("#inv-list").innerHTML = d.invites.map((i) =>
    `<div class="item"><div><span class="muted">Offene Einladung</span><div class="when">gültig bis ${fmtTs(i.expires)}</div></div>` +
    `<div class="ubtns"><button data-icopy="${esc(i.token)}">📋</button><button data-idel="${esc(i.token)}">✕</button></div></div>`).join("");
  document.querySelectorAll("[data-icopy]").forEach((b) => (b.onclick = () => copyText(`${location.origin}/register?token=${b.dataset.icopy}`)));
  document.querySelectorAll("[data-idel]").forEach((b) => (b.onclick = async () => { await api("/api/invites/" + b.dataset.idel, "DELETE"); loadUsers(); }));
}
$("#nu-add").onclick = async () => {
  try {
    await api("/api/users", "POST", { username: $("#nu-name").value, password: $("#nu-pw").value });
    $("#nu-name").value = ""; $("#nu-pw").value = ""; toast("Benutzer angelegt"); loadUsers();
  } catch (err) { toast(err.message, true); }
};
$("#inv-create").onclick = async () => {
  try {
    const r = await api("/api/invites", "POST", {});
    const link = `${location.origin}${r.path}`;
    $("#inv-out").innerHTML = `<input type="text" readonly value="${esc(link)}" onclick="this.select()"><p class="hint">Gültig ${r.days} Tage, einmal nutzbar.</p>`;
    copyText(link);
    loadUsers();
  } catch (err) { toast(err.message, true); }
};

// ---------- Discord
let discordText = "";
async function loadDiscord() {
  const d = await api("/api/discord?water=" + ($("#d-water").checked ? 1 : 0));
  discordText = d.text;
  $("#d-text").textContent = d.text.replace(/^```\n?|\n?```$/g, "");
  $("#d-post").disabled = !S.webhook;
  $("#d-hint").textContent = S.webhook ? "" : "Zum direkten Senden den Discord-Webhook im Tab Konto hinterlegen.";
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
    const me = S.me || { username: "", role: "user" };
    $("#me-name").textContent = me.username;
    document.querySelectorAll("[data-admin]").forEach((e) => (e.hidden = me.role !== "admin"));
    $("#admin-card").hidden = me.role !== "admin";
  } catch (e) { $("#status").textContent = "offline"; return; }
  renderWeighForm();
  renderWaterForm();
  renderAssist();
  if (S.settings) {
    $("#s-on").value = S.settings.light_on;
    $("#s-off").value = S.settings.light_off;
    $("#s-water").value = S.settings.water_time;
  }
  $("#backup-hint").textContent = S.last_backup
    ? `Automatisches Tages-Backup in data/backups (14 Tage), zuletzt ${S.last_backup}.` : "";
  $("#n-plant").innerHTML = '<option value="">alle</option>' + act().map((p) => `<option>${p.key}</option>`).join("");
  if ($("#tab-log").classList.contains("active")) renderLog();
  if ($("#tab-plants").classList.contains("active")) renderPlants();
  if (!S.plants.length && !plantsHintShown) {
    plantsHintShown = true;
    document.querySelector('nav button[data-tab="plants"]').click();
  }
}
$("#s-save").onclick = async () => {
  try {
    await api("/api/settings", "PUT", { light_on: $("#s-on").value, light_off: $("#s-off").value, water_time: $("#s-water").value });
    toast("Zeiten gespeichert");
    await load();
  } catch (e) { toast(e.message, true); }
};
$("#restore").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  if (!confirm("Alle aktuellen Daten durch diese Sicherung ersetzen? Vorher wird automatisch eine Kopie angelegt.")) {
    e.target.value = "";
    return;
  }
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await fetch("/api/restore", { method: "POST", body: fd });
    if (r.status === 401) return (window.location.href = "/login");
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || "Fehler beim Wiederherstellen");
    toast("Wiederhergestellt");
    await load();
  } catch (err) { toast(err.message, true); }
  e.target.value = "";
};
$("#logout").onclick = async () => {
  if (!confirm("Abmelden?")) return;
  await fetch("/logout", { method: "POST" });
  window.location.href = "/login";
};
$("#w-ts").value = localInput();
$("#g-ts").value = localInput();
load();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
