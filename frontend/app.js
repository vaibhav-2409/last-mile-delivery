/* =========================================================================
   Last-Mile Dispatch — single-page client (no build step, no framework)
   ========================================================================= */
const S = {
  token: localStorage.getItem("lm_token") || null,
  user: JSON.parse(localStorage.getItem("lm_user") || "null"),
  view: null,
  quote: null,
  cache: {},
};

const $ = (sel, root = document) => root.querySelector(sel);
const app = () => $("#app");

/* ---------- api ---------- */
async function api(path, { method = "GET", body, quiet = false } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (S.token) headers.Authorization = `Bearer ${S.token}`;
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && S.token) { signOut(); throw new Error("Session expired."); }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const msg = Array.isArray(data?.detail)
      ? data.detail.map((d) => d.msg || d).join(", ")
      : data?.detail || `Request failed (${res.status})`;
    if (!quiet) toast(msg, "bad");
    throw new Error(msg);
  }
  return data;
}

/* ---------- helpers ---------- */
const money = (n) => "₹" + Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const title = (s) => String(s || "").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
const when = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true });
};
const dateOnly = (iso) => (iso ? new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—");
const short = (h) => (h ? h.slice(0, 8) : "genesis");

const PILL = {
  CREATED: "", ASSIGNED: "live", PICKED_UP: "live", IN_TRANSIT: "live", OUT_FOR_DELIVERY: "live",
  DELIVERED: "done", FAILED: "bad", RESCHEDULED: "alt", CANCELLED: "",
};
const pill = (st) => `<span class="pill ${PILL[st] ?? ""}">${title(st)}</span>`;

/** Hand back a slot with its loading state cleared — the class carries centring
 *  and padding that must not survive into the real content. */
function ready(target) {
  const el = typeof target === "string" ? $(target) : target;
  el.classList.remove("loading");
  return el;
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function signOut() {
  S.token = null; S.user = null; S.cache = {};
  localStorage.removeItem("lm_token"); localStorage.removeItem("lm_user");
  render();
}

function setSession(payload) {
  S.token = payload.access_token; S.user = payload.user;
  localStorage.setItem("lm_token", S.token);
  localStorage.setItem("lm_user", JSON.stringify(S.user));
  S.view = null;
  render();
}

/* =========================================================================
   Auth
   ========================================================================= */
function authView() {
  app().innerHTML = `
  <div class="auth-wrap">
    <section class="auth-hero">
      <span class="eyebrow">Last-Mile Dispatch · Chennai network</span>
      <h1>Price it, assign it,<br><em>prove where it went.</em></h1>
      <p class="hero-lead">
        Bookings are priced by a rate engine you configure — zones, volumetric weight,
        separate B2B and B2C cards, COD surcharge. Agents are matched to the pickup by
        distance and spare capacity. Every status change is written once and chained by hash.
      </p>
      <div class="hero-stats">
        <div class="hero-stat"><b id="hs-zones">—</b><span>Zones live</span></div>
        <div class="hero-stat"><b id="hs-pins">—</b><span>Pincodes mapped</span></div>
        <div class="hero-stat"><b>SHA-256</b><span>Tracking chain</span></div>
      </div>
    </section>

    <section class="auth-panel">
      <div class="auth-card">
        <div class="tabs-inline" role="tablist">
          <button role="tab" aria-selected="true" data-tab="login">Sign in</button>
          <button role="tab" aria-selected="false" data-tab="register">Create account</button>
        </div>

        <form id="login-form">
          <div class="field"><label for="li-email">Email</label><input id="li-email" type="email" required autocomplete="email"></div>
          <div class="field"><label for="li-pass">Password</label><input id="li-pass" type="password" required autocomplete="current-password"></div>
          <button class="btn block" type="submit">Sign in</button>
        </form>

        <form id="register-form" class="hide">
          <div class="field"><label for="rg-name">Full name</label><input id="rg-name" required></div>
          <div class="field"><label for="rg-email">Email</label><input id="rg-email" type="email" required></div>
          <div class="field"><label for="rg-phone">Mobile</label><input id="rg-phone" placeholder="+9198…"><div class="hint">Used for delivery SMS alerts.</div></div>
          <div class="field"><label for="rg-pass">Password</label><input id="rg-pass" type="password" minlength="6" required></div>
          <button class="btn block" type="submit">Create customer account</button>
        </form>

        <div class="demo-keys">
          <span class="eyebrow">Demo logins — tap to fill</span>
          <div class="chips">
            <button class="chip" data-fill="admin@lastmile.dev|Admin@123">admin</button>
            <button class="chip" data-fill="rohit@example.com|Passw0rd!">customer</button>
            <button class="chip" data-fill="arun.agent@lastmile.dev|Passw0rd!">agent · north</button>
            <button class="chip" data-fill="divya.agent@lastmile.dev|Passw0rd!">agent · south</button>
          </div>
        </div>
      </div>
    </section>
  </div>`;

  api("/api/zones", { quiet: true }).then((z) => ($("#hs-zones").textContent = z.length)).catch(() => {});
  api("/api/serviceable", { quiet: true }).then((a) => ($("#hs-pins").textContent = a.length)).catch(() => {});

  document.querySelectorAll("[data-tab]").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", x === b));
      $("#login-form").classList.toggle("hide", b.dataset.tab !== "login");
      $("#register-form").classList.toggle("hide", b.dataset.tab !== "register");
    })
  );
  document.querySelectorAll("[data-fill]").forEach((b) =>
    b.addEventListener("click", () => {
      const [email, pass] = b.dataset.fill.split("|");
      $("#li-email").value = email; $("#li-pass").value = pass;
    })
  );
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      setSession(await api("/api/auth/login", { method: "POST", body: { email: $("#li-email").value, password: $("#li-pass").value } }));
    } catch {}
  });
  $("#register-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      setSession(await api("/api/auth/register", {
        method: "POST",
        body: { name: $("#rg-name").value, email: $("#rg-email").value, phone: $("#rg-phone").value || null, password: $("#rg-pass").value },
      }));
      toast("Account created. Welcome aboard.", "ok");
    } catch {}
  });
}

/* =========================================================================
   Shell
   ========================================================================= */
const NAV = {
  CUSTOMER: [["book", "Book a delivery", "＋"], ["orders", "My orders", "▤"]],
  AGENT: [["jobs", "My deliveries", "▤"], ["profile", "Availability", "◉"]],
  ADMIN: [["overview", "Overview", "▦"], ["orders", "Orders", "▤"], ["book", "Book for customer", "＋"],
          ["zones", "Zones & areas", "◈"], ["rates", "Rate cards", "₹"], ["agents", "Agents", "◉"],
          ["notifications", "Notifications", "✉"]],
};

function shell(bodyHtml) {
  const role = S.user.role;
  const items = NAV[role].map(([id, label, ico]) =>
    `<button class="nav-btn" data-nav="${id}" ${S.view === id ? 'aria-current="page"' : ""}><span class="ico">${ico}</span>${label}</button>`
  ).join("");

  app().innerHTML = `
  <div class="shell">
    <nav class="sidebar">
      <div class="brand">
        <div class="brand-mark">LM</div>
        <div><b>Dispatch</b><span>Last-mile</span></div>
      </div>
      ${items}
      <div class="sidebar-foot">
        <div class="who">
          <b>${esc(S.user.name)}</b>
          <span class="role-tag ${role.toLowerCase()}">${role}</span>
        </div>
        <button class="btn ghost small block" data-act="signout">Sign out</button>
      </div>
    </nav>
    <main class="main">${bodyHtml}</main>
  </div>`;

  document.querySelectorAll("[data-nav]").forEach((b) =>
    b.addEventListener("click", () => { S.view = b.dataset.nav; render(); })
  );
  $('[data-act="signout"]').addEventListener("click", signOut);
}

const head = (eyebrow, h, p) =>
  `<div class="page-head"><span class="eyebrow">${eyebrow}</span><h2>${h}</h2>${p ? `<p>${p}</p>` : ""}</div>`;

const emptyState = (b, p) => `<div class="empty"><b>${b}</b>${p}</div>`;

/* =========================================================================
   Booking (customer + admin-on-behalf)
   ========================================================================= */
async function bookView() {
  const isAdmin = S.user.role === "ADMIN";
  let customers = [];
  if (isAdmin) customers = await api("/api/admin/customers").catch(() => []);

  shell(`
    ${head(isAdmin ? "Admin · new booking" : "New booking", isAdmin ? "Book on behalf of a customer" : "Book a delivery",
      "Charges are calculated from the live rate cards before anything is confirmed. Nothing is booked until you press confirm.")}
    <div class="grid book">
      <div class="panel">
        <form id="book-form">
          ${isAdmin ? `<div class="field"><label for="bk-customer">Customer</label>
            <select id="bk-customer" required>
              <option value="">Choose a customer…</option>
              ${customers.map((c) => `<option value="${c.id}">${esc(c.name)} · ${esc(c.email)}</option>`).join("")}
            </select></div>` : ""}

          <span class="eyebrow">Pickup</span>
          <div class="field-row" style="margin-top:8px">
            <div class="field"><label for="bk-pc">Contact name</label><input id="bk-pc"></div>
            <div class="field"><label for="bk-pp">Contact phone</label><input id="bk-pp"></div>
          </div>
          <div class="field"><label for="bk-paddr">Address</label><textarea id="bk-paddr" rows="2" required></textarea></div>
          <div class="field"><label for="bk-ppin">Pincode</label><input id="bk-ppin" data-zone-for="pickup" required inputmode="numeric">
            <div class="hint" id="hint-pickup">Zone is detected from this pincode.</div></div>

          <span class="eyebrow">Drop</span>
          <div class="field-row" style="margin-top:8px">
            <div class="field"><label for="bk-dc">Contact name</label><input id="bk-dc"></div>
            <div class="field"><label for="bk-dp">Contact phone</label><input id="bk-dp"></div>
          </div>
          <div class="field"><label for="bk-daddr">Address</label><textarea id="bk-daddr" rows="2" required></textarea></div>
          <div class="field"><label for="bk-dpin">Pincode</label><input id="bk-dpin" data-zone-for="drop" required inputmode="numeric">
            <div class="hint" id="hint-drop">Zone is detected from this pincode.</div></div>

          <span class="eyebrow">Package</span>
          <div class="field-row" style="margin-top:8px">
            <div class="field"><label for="bk-l">Length cm</label><input id="bk-l" type="number" step="0.1" min="0.1" value="40" required></div>
            <div class="field"><label for="bk-b">Breadth cm</label><input id="bk-b" type="number" step="0.1" min="0.1" value="30" required></div>
            <div class="field"><label for="bk-h">Height cm</label><input id="bk-h" type="number" step="0.1" min="0.1" value="20" required></div>
            <div class="field"><label for="bk-w">Weight kg</label><input id="bk-w" type="number" step="0.01" min="0.01" value="2" required></div>
          </div>
          <div class="field-row">
            <div class="field"><label for="bk-type">Order type</label>
              <select id="bk-type"><option value="B2C">B2C — retail</option><option value="B2B">B2B — business</option></select></div>
            <div class="field"><label for="bk-pay">Payment</label>
              <select id="bk-pay"><option value="PREPAID">Prepaid</option><option value="COD">Cash on delivery</option></select></div>
          </div>
          <div class="field"><label for="bk-desc">What's inside <span class="muted">(optional)</span></label><input id="bk-desc" placeholder="Documents, spare parts…"></div>
          ${isAdmin ? `<label class="switch"><input type="checkbox" id="bk-auto" checked><span>Auto-assign the nearest agent on confirm</span></label>` : ""}
        </form>
      </div>

      <aside class="quote" id="quote-panel">
        <div class="quote-head">
          <span class="eyebrow">Estimated charge</span>
          <div class="quote-total" id="q-total">—</div>
          <div class="lane" id="q-lane"><span class="muted">Enter both pincodes to price this shipment.</span></div>
        </div>
        <div class="quote-body" id="q-body">
          <p class="dim">The engine picks the rate card that matches your order type and whether the parcel stays inside one zone.</p>
        </div>
      </aside>
    </div>`);

  const form = $("#book-form");
  form.addEventListener("input", debounce(refreshQuote, 450));
  form.addEventListener("change", refreshQuote);
  document.querySelectorAll("[data-zone-for]").forEach((inp) =>
    inp.addEventListener("blur", () => lookupZone(inp))
  );
  refreshQuote();
}

const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

async function lookupZone(input) {
  const hint = $(`#hint-${input.dataset.zoneFor}`);
  const pin = input.value.trim();
  if (!pin) { hint.className = "hint"; hint.textContent = "Zone is detected from this pincode."; return; }
  try {
    const z = await api(`/api/zone-lookup/${encodeURIComponent(pin)}`, { quiet: true });
    hint.className = "hint ok"; hint.textContent = `Serviceable · ${z.name} (${z.code})`;
  } catch {
    hint.className = "hint bad"; hint.textContent = "We don't deliver to this pincode yet.";
  }
}

function bookPayload() {
  return {
    customer_id: $("#bk-customer") ? Number($("#bk-customer").value) || null : null,
    pickup_contact: $("#bk-pc").value || null, pickup_phone: $("#bk-pp").value || null,
    pickup_address: $("#bk-paddr").value, pickup_pincode: $("#bk-ppin").value.trim(),
    drop_contact: $("#bk-dc").value || null, drop_phone: $("#bk-dp").value || null,
    drop_address: $("#bk-daddr").value, drop_pincode: $("#bk-dpin").value.trim(),
    length_cm: +$("#bk-l").value, breadth_cm: +$("#bk-b").value, height_cm: +$("#bk-h").value,
    actual_weight_kg: +$("#bk-w").value,
    order_type: $("#bk-type").value, payment_type: $("#bk-pay").value,
    package_description: $("#bk-desc").value || null,
    auto_assign: $("#bk-auto") ? $("#bk-auto").checked : false,
  };
}

async function refreshQuote() {
  const p = bookPayload();
  const ready = p.pickup_pincode && p.drop_pincode && p.length_cm > 0 && p.breadth_cm > 0 && p.height_cm > 0 && p.actual_weight_kg > 0;
  if (!ready) { S.quote = null; return; }
  try {
    S.quote = await api("/api/orders/quote", {
      method: "POST", quiet: true,
      body: {
        pickup_pincode: p.pickup_pincode, drop_pincode: p.drop_pincode,
        length_cm: p.length_cm, breadth_cm: p.breadth_cm, height_cm: p.height_cm,
        actual_weight_kg: p.actual_weight_kg, order_type: p.order_type, payment_type: p.payment_type,
      },
    });
    paintQuote(S.quote);
  } catch (err) {
    S.quote = null;
    $("#q-total").textContent = "—";
    $("#q-lane").innerHTML = `<span class="muted">${esc(err.message)}</span>`;
    $("#q-body").innerHTML = `<p class="dim">Fix the highlighted details and the price will update.</p>`;
  }
}

function paintQuote(q) {
  const volWins = q.weight_basis === "VOLUMETRIC";
  $("#q-total").textContent = money(q.total_charge);
  $("#q-lane").innerHTML =
    `<span>${esc(q.pickup_zone.code)}</span><span class="arrow">→</span><span>${esc(q.drop_zone.code)}</span>
     <span class="pill ${q.scope === "INTRA" ? "done" : "live"}">${q.scope}</span>
     <span class="pill">${q.order_type}</span>`;
  $("#q-body").innerHTML = `
    <div class="weight-box">
      <div class="${volWins ? "" : "win"}"><span class="dim">Actual</span><b>${q.actual_weight_kg} kg</b></div>
      <div class="${volWins ? "win" : ""}"><span class="dim">Volumetric</span><b>${q.volumetric_weight_kg} kg</b></div>
      <div class="win"><span class="dim">Billed on</span><b>${q.billable_weight_kg} kg</b></div>
    </div>
    <p class="dim" style="margin:0 0 12px">
      L×B×H ÷ ${q.volumetric_divisor} = ${q.volumetric_weight_kg} kg volumetric.
      We bill the higher figure, rounded up to the next slab.
    </p>
    ${q.lines.map((l) => `
      <div class="qline">
        <div><div class="lbl">${esc(l.label)}</div><div class="det">${esc(l.detail)}</div></div>
        <div class="amt">${money(l.amount)}</div>
      </div>`).join("")}
    <div class="qline"><div class="lbl"><b>Total payable</b></div><div class="amt"><b>${money(q.total_charge)}</b></div></div>
    <button class="btn block" style="margin-top:16px" id="confirm-btn">Confirm and book · ${money(q.total_charge)}</button>
    <p class="dim" style="text-align:center;margin:10px 0 0">Rate card: ${esc(q.rate_card_name)}</p>`;
  $("#confirm-btn").addEventListener("click", confirmBooking);
}

async function confirmBooking() {
  const form = $("#book-form");
  if (!form.reportValidity()) return;
  const btn = $("#confirm-btn");
  btn.disabled = true; btn.textContent = "Booking…";
  try {
    const order = await api("/api/orders", { method: "POST", body: bookPayload() });
    toast(`Order ${order.order_code} booked · ${money(order.total_charge)}`, "ok");
    S.view = "orders";
    render();
    setTimeout(() => openOrder(order.id), 260);
  } catch {
    btn.disabled = false;
    btn.textContent = `Confirm and book · ${money(S.quote?.total_charge || 0)}`;
  }
}

/* =========================================================================
   Orders list (customer + admin)
   ========================================================================= */
async function ordersView() {
  const isAdmin = S.user.role === "ADMIN";
  shell(`
    ${head(isAdmin ? "Operations" : "My shipments", isAdmin ? "All orders" : "My orders",
      isAdmin ? "Filter by status, zone or agent. Open any order to assign, override or audit its history."
              : "Open an order to see the live tracking timeline.")}
    ${isAdmin ? `<div class="filters" id="filters"></div>` : ""}
    <div class="panel"><div id="orders-slot" class="loading">Loading orders…</div></div>`);

  if (isAdmin) {
    const [zones, agents] = await Promise.all([api("/api/admin/zones"), api("/api/admin/agents")]);
    S.cache.zones = zones; S.cache.agents = agents;
    $("#filters").innerHTML = `
      <div class="field"><label for="f-status">Status</label><select id="f-status"><option value="">Any status</option>
        ${["CREATED","ASSIGNED","PICKED_UP","IN_TRANSIT","OUT_FOR_DELIVERY","DELIVERED","FAILED","RESCHEDULED","CANCELLED"]
          .map((s) => `<option value="${s}">${title(s)}</option>`).join("")}</select></div>
      <div class="field"><label for="f-zone">Zone</label><select id="f-zone"><option value="">Any zone</option>
        ${zones.map((z) => `<option value="${z.id}">${esc(z.name)}</option>`).join("")}</select></div>
      <div class="field"><label for="f-agent">Agent</label><select id="f-agent"><option value="">Any agent</option>
        ${agents.map((a) => `<option value="${a.user_id}">${esc(a.user.name)}</option>`).join("")}</select></div>
      <div class="field grow"><label for="f-search">Search</label><input id="f-search" placeholder="Order number or pincode"></div>
      <button class="btn ghost" id="f-clear">Clear</button>`;
    ["f-status", "f-zone", "f-agent"].forEach((id) => $("#" + id).addEventListener("change", loadOrders));
    $("#f-search").addEventListener("input", debounce(loadOrders, 350));
    $("#f-clear").addEventListener("click", () => {
      ["f-status", "f-zone", "f-agent", "f-search"].forEach((id) => ($("#" + id).value = ""));
      loadOrders();
    });
  }
  loadOrders();
}

async function loadOrders() {
  const q = new URLSearchParams();
  if ($("#f-status")?.value) q.set("status", $("#f-status").value);
  if ($("#f-zone")?.value) q.set("zone_id", $("#f-zone").value);
  if ($("#f-agent")?.value) q.set("agent_id", $("#f-agent").value);
  if ($("#f-search")?.value) q.set("search", $("#f-search").value);

  const slot = $("#orders-slot");
  const orders = await api("/api/orders?" + q.toString());
  if (!orders.length) {
    ready(slot).innerHTML = emptyState("No orders here yet",
      S.user.role === "CUSTOMER" ? "Book a delivery and it will show up on this list." : "Try clearing the filters.");
    return;
  }
  const isAdmin = S.user.role === "ADMIN";
  ready(slot).innerHTML = `<div class="table-wrap"><table>
    <thead><tr>
      <th>Order</th>${isAdmin ? "<th>Customer</th>" : ""}<th>Lane</th><th>Type</th>
      <th>Billed</th><th>Charge</th><th>Agent</th><th>Status</th><th>Booked</th>
    </tr></thead><tbody>
    ${orders.map((o) => `
      <tr class="clickable" data-order="${o.id}">
        <td><span class="code">${esc(o.order_code)}</span></td>
        ${isAdmin ? `<td>${esc(o.customer?.name || "—")}</td>` : ""}
        <td class="mono dim">${esc(o.pickup_zone?.code || o.pickup_pincode)} → ${esc(o.drop_zone?.code || o.drop_pincode)}</td>
        <td><span class="pill">${o.order_type}</span> ${o.payment_type === "COD" ? '<span class="pill alt">COD</span>' : ""}</td>
        <td class="num">${o.billable_weight_kg} kg</td>
        <td class="num">${money(o.total_charge)}</td>
        <td class="dim">${esc(o.agent?.name || "Unassigned")}</td>
        <td>${pill(o.status)}</td>
        <td class="dim mono" style="font-size:12px">${when(o.created_at)}</td>
      </tr>`).join("")}
    </tbody></table></div>`;
  slot.querySelectorAll("[data-order]").forEach((tr) =>
    tr.addEventListener("click", () => openOrder(Number(tr.dataset.order)))
  );
}

/* =========================================================================
   Order drawer — the waybill rail lives here
   ========================================================================= */
function closeDrawer() {
  $(".scrim")?.remove();
  $(".drawer")?.remove();
  document.removeEventListener("keydown", escClose);
}
function escClose(e) { if (e.key === "Escape") closeDrawer(); }

async function openOrder(id) {
  closeDrawer();
  const scrim = document.createElement("div");
  scrim.className = "scrim";
  scrim.addEventListener("click", closeDrawer);
  const drawer = document.createElement("aside");
  drawer.className = "drawer";
  drawer.innerHTML = `<div class="loading">Opening order…</div>`;
  document.body.append(scrim, drawer);
  document.addEventListener("keydown", escClose);

  const o = await api(`/api/orders/${id}`);
  const isAdmin = S.user.role === "ADMIN";
  let agents = [];
  if (isAdmin) agents = S.cache.agents || (S.cache.agents = await api("/api/admin/agents").catch(() => []));

  drawer.innerHTML = `
    <div class="drawer-head">
      <div>
        <span class="eyebrow">Waybill</span>
        <h3 class="mono" style="font-size:19px;margin-top:4px">${esc(o.order_code)}</h3>
        <div class="row" style="margin-top:8px">${pill(o.status)}<span class="pill">${o.order_type}</span>
          <span class="pill ${o.payment_type === "COD" ? "alt" : ""}">${title(o.payment_type)}</span></div>
      </div>
      <button class="x-btn" aria-label="Close">×</button>
    </div>
    <div class="drawer-body">
      <div class="addr-pair" style="margin-bottom:18px">
        <div class="addr"><span class="eyebrow">Pickup · ${esc(o.pickup_zone?.code || "?")}</span>
          <p>${esc(o.pickup_address)}</p><p class="dim mono">${esc(o.pickup_pincode)}${o.pickup_contact ? " · " + esc(o.pickup_contact) : ""}</p></div>
        <div class="addr"><span class="eyebrow">Drop · ${esc(o.drop_zone?.code || "?")}</span>
          <p>${esc(o.drop_address)}</p><p class="dim mono">${esc(o.drop_pincode)}${o.drop_contact ? " · " + esc(o.drop_contact) : ""}</p></div>
      </div>

      <div class="panel" style="background:var(--bg);margin-bottom:18px">
        <div class="panel-head"><h3>Charge breakdown</h3><span class="mono" style="font-size:18px">${money(o.total_charge)}</span></div>
        <dl class="kv">
          <dt>Dimensions</dt><dd>${o.length_cm} × ${o.breadth_cm} × ${o.height_cm} cm</dd>
          <dt>Actual weight</dt><dd>${o.actual_weight_kg} kg</dd>
          <dt>Volumetric</dt><dd>${o.volumetric_weight_kg} kg</dd>
          <dt>Billed on</dt><dd>${o.billable_weight_kg} kg · ${title(o.weight_basis)}</dd>
          <dt>Rate scope</dt><dd>${o.rate_scope || "—"} · card #${o.rate_card_id ?? "—"}</dd>
          <dt>Freight</dt><dd>${money(o.freight_charge)}</dd>
          <dt>Fuel surcharge</dt><dd>${money(o.fuel_surcharge)}</dd>
          <dt>COD handling</dt><dd>${money(o.cod_surcharge)}</dd>
          <dt>Customer</dt><dd>${esc(o.customer?.name || "—")}</dd>
          <dt>Agent</dt><dd>${esc(o.agent?.name || "Unassigned")}</dd>
          <dt>Attempts</dt><dd>${o.delivery_attempts}</dd>
          <dt>Scheduled</dt><dd>${dateOnly(o.scheduled_date)}</dd>
        </dl>
      </div>

      ${o.failure_reason ? `<div class="panel" style="background:rgba(229,83,61,.07);border-color:rgba(229,83,61,.3)">
        <span class="eyebrow" style="color:var(--ember)">Last failure</span>
        <p style="margin:6px 0 0">${esc(o.failure_reason)}</p></div>` : ""}

      <div id="order-actions"></div>

      <div class="panel" style="background:var(--bg)">
        <div class="panel-head"><h3>Tracking history</h3><span class="dim">${o.events.length} checkpoints · append-only</span></div>
        <div class="rail">
          ${o.events.map((e, i) => {
            const last = i === o.events.length - 1;
            const cls = e.status === "FAILED" ? "is-bad"
                      : e.status === "DELIVERED" ? "is-done"
                      : last ? "is-current" : "is-done";
            return `<div class="stop ${cls}">
              <div class="stop-dot">${i + 1}</div>
              <div class="stop-head">
                <span class="stop-title">${title(e.status)}</span>
                <span class="stop-time">${when(e.created_at)}</span>
                ${e.is_override ? '<span class="override-flag">Admin override</span>' : ""}
              </div>
              ${e.note ? `<div class="stop-note">${esc(e.note)}</div>` : ""}
              <div class="stop-meta">by ${esc(e.actor_name || "system")}${e.actor_role ? " · " + title(e.actor_role) : ""}${e.location_text ? " · " + esc(e.location_text) : ""}</div>
              <div class="chain" title="This checkpoint's SHA-256, chained to the one before it">
                <span class="link">⛓</span><b>${short(e.prev_hash)}</b><span class="link">→</span><b>${short(e.event_hash)}</b>
              </div>
            </div>`;
          }).join("")}
        </div>
      </div>
    </div>`;

  drawer.querySelector(".x-btn").addEventListener("click", closeDrawer);
  renderOrderActions(o, agents);
}

function renderOrderActions(o, agents) {
  const slot = $("#order-actions");
  const role = S.user.role;
  const done = ["DELIVERED", "CANCELLED"].includes(o.status);
  let html = "";

  if (role === "CUSTOMER") {
    if (o.status === "FAILED") {
      html = `<div class="panel" style="border-color:rgba(242,194,48,.3)">
        <div class="panel-head"><h3>Reschedule this delivery</h3></div>
        <p class="dim" style="margin-top:-8px">Pick a new date and we'll put a fresh agent on it.</p>
        <div class="row wrap">
          <div class="field grow" style="margin:0"><label for="rs-date">New delivery date</label><input id="rs-date" type="date" min="${new Date(Date.now() + 864e5).toISOString().slice(0, 10)}"></div>
          <div class="field grow" style="margin:0"><label for="rs-reason">Reason <span class="muted">(optional)</span></label><input id="rs-reason" placeholder="I'll be home after 6pm"></div>
        </div>
        <button class="btn" style="margin-top:14px" data-do="reschedule">Confirm new date</button>
      </div>`;
    } else if (!done) {
      html = `<div class="panel"><div class="spread"><div><b>Need to stop this shipment?</b>
        <p class="dim" style="margin:4px 0 0">Cancelling is permanent.</p></div>
        <button class="btn danger" data-do="cancel">Cancel order</button></div></div>`;
    }
  }

  if (role === "ADMIN") {
    html = `<div class="panel">
      <div class="panel-head"><h3>Dispatch controls</h3></div>
      <div class="field"><label for="ad-agent">Assign an agent</label>
        <div class="row">
          <select id="ad-agent" class="grow">
            <option value="">Choose an agent…</option>
            ${agents.map((a) => `<option value="${a.user_id}" ${a.user_id === o.agent_id ? "selected" : ""}>
              ${esc(a.user.name)} · ${esc(a.home_zone?.code || "no zone")} · ${a.active_orders}/${a.max_active_orders}${a.is_available ? "" : " · off duty"}
            </option>`).join("")}
          </select>
          <button class="btn" data-do="assign" ${done ? "disabled" : ""}>Assign</button>
          <button class="btn ghost" data-do="auto" ${done ? "disabled" : ""}>Auto-assign nearest</button>
        </div>
      </div>
      <div class="field" style="margin-top:8px"><label for="ad-status">Override status</label>
        <div class="row">
          <select id="ad-status" class="grow">
            ${["CREATED","ASSIGNED","PICKED_UP","IN_TRANSIT","OUT_FOR_DELIVERY","DELIVERED","FAILED","RESCHEDULED","CANCELLED"]
              .map((s) => `<option value="${s}" ${s === o.status ? "selected" : ""}>${title(s)}</option>`).join("")}
          </select>
          <input id="ad-note" placeholder="Why are you overriding?" class="grow" style="background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px 12px">
          <button class="btn ghost" data-do="override">Force status</button>
        </div>
        <div class="hint">Overrides skip the normal lifecycle but are stamped in the history with your name.</div>
      </div>
      <button class="btn ghost small" data-do="integrity" style="margin-top:6px">Verify tracking chain</button>
    </div>`;
  }

  slot.innerHTML = html;

  slot.querySelector('[data-do="reschedule"]')?.addEventListener("click", async () => {
    const d = $("#rs-date").value;
    if (!d) return toast("Pick a delivery date first.", "bad");
    await api(`/api/orders/${o.id}/reschedule`, {
      method: "POST",
      body: { requested_date: new Date(d + "T10:00:00").toISOString(), reason: $("#rs-reason").value || null },
    });
    toast("Rescheduled. A new agent is on it.", "ok");
    closeDrawer(); loadOrders(); openOrder(o.id);
  });

  slot.querySelector('[data-do="cancel"]')?.addEventListener("click", async () => {
    await api(`/api/orders/${o.id}/cancel`, { method: "POST" });
    toast("Order cancelled.", "ok");
    closeDrawer(); loadOrders();
  });

  slot.querySelector('[data-do="assign"]')?.addEventListener("click", async () => {
    const agentId = Number($("#ad-agent").value);
    if (!agentId) return toast("Choose an agent to assign.", "bad");
    await api(`/api/orders/${o.id}/assign`, { method: "POST", body: { agent_id: agentId } });
    toast("Agent assigned.", "ok");
    S.cache.agents = null; closeDrawer(); loadOrders(); openOrder(o.id);
  });

  slot.querySelector('[data-do="auto"]')?.addEventListener("click", async () => {
    const updated = await api(`/api/orders/${o.id}/auto-assign`, { method: "POST" });
    toast(`Assigned to ${updated.agent?.name || "an agent"}.`, "ok");
    S.cache.agents = null; closeDrawer(); loadOrders(); openOrder(o.id);
  });

  slot.querySelector('[data-do="override"]')?.addEventListener("click", async () => {
    await api(`/api/admin/orders/${o.id}/override-status`, {
      method: "POST",
      body: { status: $("#ad-status").value, note: $("#ad-note").value || null, failure_reason: $("#ad-note").value || null },
    });
    toast("Status forced and logged.", "ok");
    closeDrawer(); loadOrders(); openOrder(o.id);
  });

  slot.querySelector('[data-do="integrity"]')?.addEventListener("click", async () => {
    const r = await api(`/api/admin/orders/${o.id}/integrity`);
    toast(r.intact ? `Chain verified across ${r.events} checkpoints.` : `Tampering detected at event ${r.broken_at}.`, r.intact ? "ok" : "bad");
  });
}

/* =========================================================================
   Agent
   ========================================================================= */
const NEXT_STEPS = {
  ASSIGNED: [["PICKED_UP", "Mark picked up"]],
  PICKED_UP: [["IN_TRANSIT", "Mark in transit"]],
  IN_TRANSIT: [["OUT_FOR_DELIVERY", "Out for delivery"]],
  OUT_FOR_DELIVERY: [["DELIVERED", "Mark delivered"]],
};

async function agentJobsView() {
  shell(`${head("Field", "My deliveries", "Update each parcel as you go. Customers get an email the moment you do.")}
    <div id="jobs-slot" class="loading">Loading your runs…</div>`);
  const orders = await api("/api/agent/orders");
  const slot = $("#jobs-slot");
  const live = orders.filter((o) => !["DELIVERED", "CANCELLED"].includes(o.status));
  const past = orders.filter((o) => ["DELIVERED", "CANCELLED"].includes(o.status));

  if (!orders.length) { ready(slot).innerHTML = emptyState("Nothing assigned", "New jobs appear here as dispatch assigns them."); return; }

  ready(slot).innerHTML = `
    ${live.length ? `<span class="eyebrow">Active · ${live.length}</span>` : ""}
    ${live.map(jobCard).join("")}
    ${past.length ? `<span class="eyebrow" style="display:block;margin-top:26px">Completed · ${past.length}</span>` : ""}
    ${past.map(jobCard).join("")}`;

  slot.querySelectorAll("[data-step]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const { order, step } = btn.dataset;
      btn.disabled = true;
      try {
        await api(`/api/agent/orders/${order}/status`, {
          method: "POST",
          body: { status: step, note: `${title(step)} by ${S.user.name}` },
        });
        toast(`Marked ${title(step).toLowerCase()}. Customer notified.`, "ok");
        agentJobsView();
      } catch { btn.disabled = false; }
    })
  );
  slot.querySelectorAll("[data-fail]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const reason = prompt("What went wrong? The customer sees this and can reschedule.");
      if (!reason) return;
      await api(`/api/agent/orders/${btn.dataset.fail}/status`, {
        method: "POST", body: { status: "FAILED", failure_reason: reason },
      });
      toast("Attempt logged as failed. Customer notified.", "ok");
      agentJobsView();
    })
  );
  slot.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => openOrder(Number(b.dataset.view))));
}

function jobCard(o) {
  const steps = NEXT_STEPS[o.status] || [];
  // an attempt can only be failed while the parcel is actually in the agent's hands
  const canFail = ["ASSIGNED", "PICKED_UP", "IN_TRANSIT", "OUT_FOR_DELIVERY"].includes(o.status);
  return `<article class="job ${steps.length ? "next" : ""}">
    <div class="spread">
      <div>
        <span class="code mono">${esc(o.order_code)}</span>
        <div class="row wrap" style="margin-top:8px">${pill(o.status)}<span class="pill">${o.order_type}</span>
          ${o.payment_type === "COD" ? `<span class="pill alt">Collect ${money(o.total_charge)}</span>` : ""}</div>
      </div>
      <div style="text-align:right">
        <div class="mono">${o.billable_weight_kg} kg</div>
        <div class="dim">${esc(o.pickup_pincode)} → ${esc(o.drop_pincode)}</div>
      </div>
    </div>
    ${o.scheduled_date ? `<p class="dim" style="margin:10px 0 0">Scheduled for ${dateOnly(o.scheduled_date)}</p>` : ""}
    ${o.status === "FAILED" ? `<p class="dim" style="margin:10px 0 0">Waiting on the customer to pick a new date.</p>` : ""}
    <div class="job-actions">
      ${steps.map(([s, label]) => `<button class="btn small" data-order="${o.id}" data-step="${s}">${label}</button>`).join("")}
      ${canFail ? `<button class="btn small danger" data-fail="${o.id}">Delivery failed</button>` : ""}
      <button class="btn small ghost" data-view="${o.id}">Open waybill</button>
    </div>
  </article>`;
}

async function agentProfileView() {
  shell(`${head("Field", "Availability & location",
    "Dispatch matches parcels to whoever is on duty, closest to the pickup, with room on their run.")}
    <div id="prof-slot" class="loading">Loading…</div>`);
  const p = await api("/api/agent/me");
  ready("#prof-slot").innerHTML = `
    <div class="grid two">
      <div class="panel">
        <div class="panel-head"><h3>Duty status</h3></div>
        <label class="switch"><input type="checkbox" id="av-toggle" ${p.is_available ? "checked" : ""}>
          <span id="av-label">${p.is_available ? "On duty — you'll receive new jobs" : "Off duty — no new jobs"}</span></label>
        <dl class="kv" style="margin-top:20px">
          <dt>Home zone</dt><dd>${esc(p.home_zone?.name || "Unassigned")}</dd>
          <dt>Vehicle</dt><dd>${esc(p.vehicle_type)}</dd>
          <dt>Current load</dt><dd>${p.active_orders} / ${p.max_active_orders}</dd>
        </dl>
      </div>
      <div class="panel">
        <div class="panel-head"><h3>Where you are</h3></div>
        <p class="dim" style="margin-top:-8px">Auto-assignment measures from this point to the pickup address.</p>
        <div class="field-row">
          <div class="field"><label for="lat">Latitude</label><input id="lat" type="number" step="0.0001" value="${p.current_lat ?? ""}"></div>
          <div class="field"><label for="lng">Longitude</label><input id="lng" type="number" step="0.0001" value="${p.current_lng ?? ""}"></div>
        </div>
        <div class="row">
          <button class="btn" id="save-loc">Save location</button>
          <button class="btn ghost" id="gps-loc">Use my GPS</button>
        </div>
      </div>
    </div>`;

  $("#av-toggle").addEventListener("change", async (e) => {
    const p2 = await api("/api/agent/me", { method: "PATCH", body: { is_available: e.target.checked } });
    $("#av-label").textContent = p2.is_available ? "On duty — you'll receive new jobs" : "Off duty — no new jobs";
    toast(p2.is_available ? "You're on duty." : "You're off duty.", "ok");
  });
  $("#save-loc").addEventListener("click", async () => {
    await api("/api/agent/me", { method: "PATCH", body: { current_lat: +$("#lat").value, current_lng: +$("#lng").value } });
    toast("Location updated.", "ok");
  });
  $("#gps-loc").addEventListener("click", () => {
    if (!navigator.geolocation) return toast("This browser can't share a location.", "bad");
    navigator.geolocation.getCurrentPosition(
      (pos) => { $("#lat").value = pos.coords.latitude.toFixed(4); $("#lng").value = pos.coords.longitude.toFixed(4); toast("Position captured — save to apply."); },
      () => toast("Location permission was denied.", "bad")
    );
  });
}

/* =========================================================================
   Admin — overview, zones, rates, agents, notifications
   ========================================================================= */
async function overviewView() {
  shell(`${head("Control room", "Network overview", "A live read on volume, revenue booked and who's on the road.")}
    <div id="ov-slot" class="loading">Crunching numbers…</div>`);
  const s = await api("/api/admin/stats");
  const by = s.orders_by_status;
  ready("#ov-slot").innerHTML = `
    <div class="stat-grid" style="margin-bottom:20px">
      <div class="stat"><span class="eyebrow">Orders</span><b>${s.orders_total}</b></div>
      <div class="stat accent"><span class="eyebrow">Revenue booked</span><b>${money(s.revenue_booked)}</b></div>
      <div class="stat"><span class="eyebrow">Agents on duty</span><b>${s.agents_available}/${s.agents_total}</b></div>
      <div class="stat"><span class="eyebrow">Customers</span><b>${s.customers}</b></div>
      <div class="stat"><span class="eyebrow">Checkpoints logged</span><b>${s.tracking_events}</b></div>
      <div class="stat"><span class="eyebrow">Notifications</span><b>${s.notifications_sent}</b></div>
    </div>
    <div class="panel">
      <div class="panel-head"><h3>Where every parcel stands</h3><span class="dim">${s.zones} zones · ${s.areas} pincodes mapped</span></div>
      ${Object.keys(by).length
        ? `<div class="row wrap">${Object.entries(by).map(([k, v]) =>
            `<div class="stat" style="min-width:140px"><span class="eyebrow">${title(k)}</span><b>${v}</b></div>`).join("")}</div>`
        : emptyState("No orders booked yet", "Use “Book for customer” to create the first one.")}
    </div>`;
}

async function zonesView() {
  shell(`${head("Configuration", "Zones & service areas",
    "Zone detection is a pincode lookup. Map a pincode here and the rate engine picks it up on the next quote — no deploy needed.")}
    <div id="z-slot" class="loading">Loading…</div>`);
  const [zones, areas] = await Promise.all([api("/api/admin/zones"), api("/api/admin/areas")]);
  ready("#z-slot").innerHTML = `
    <div class="grid two">
      <div class="panel">
        <div class="panel-head"><h3>Zones</h3><span class="dim">${zones.length}</span></div>
        <div class="table-wrap"><table><thead><tr><th>Code</th><th>Name</th><th>Centroid</th><th>Pincodes</th></tr></thead><tbody>
          ${zones.map((z) => `<tr><td class="code">${esc(z.code)}</td><td>${esc(z.name)}</td>
            <td class="dim mono" style="font-size:12px">${z.centroid_lat ?? "—"}, ${z.centroid_lng ?? "—"}</td>
            <td class="num">${areas.filter((a) => a.zone_id === z.id).length}</td></tr>`).join("")}
        </tbody></table></div>
        <form id="zone-form" style="margin-top:18px;border-top:1px solid var(--line-soft);padding-top:16px">
          <span class="eyebrow">Add a zone</span>
          <div class="field-row" style="margin-top:10px">
            <div class="field"><label for="z-code">Code</label><input id="z-code" required placeholder="CHN-E"></div>
            <div class="field"><label for="z-name">Name</label><input id="z-name" required placeholder="Chennai East"></div>
          </div>
          <div class="field-row">
            <div class="field"><label for="z-lat">Centroid lat</label><input id="z-lat" type="number" step="0.0001"></div>
            <div class="field"><label for="z-lng">Centroid lng</label><input id="z-lng" type="number" step="0.0001"></div>
          </div>
          <button class="btn small">Add zone</button>
        </form>
      </div>

      <div class="panel">
        <div class="panel-head"><h3>Pincode map</h3><span class="dim">${areas.length} serviceable</span></div>
        <div class="table-wrap" style="max-height:340px;overflow-y:auto"><table><thead><tr><th>Pincode</th><th>Area</th><th>Zone</th><th></th></tr></thead><tbody>
          ${areas.map((a) => `<tr><td class="code">${esc(a.pincode)}</td><td>${esc(a.name)}</td>
            <td><span class="pill">${esc(a.zone?.code || "?")}</span></td>
            <td style="text-align:right"><button class="btn small danger" data-del-area="${a.id}">Remove</button></td></tr>`).join("")}
        </tbody></table></div>
        <form id="area-form" style="margin-top:18px;border-top:1px solid var(--line-soft);padding-top:16px">
          <span class="eyebrow">Map a pincode to a zone</span>
          <div class="field-row" style="margin-top:10px">
            <div class="field"><label for="a-pin">Pincode</label><input id="a-pin" required></div>
            <div class="field"><label for="a-name">Area name</label><input id="a-name" required></div>
            <div class="field"><label for="a-zone">Zone</label><select id="a-zone">${zones.map((z) => `<option value="${z.id}">${esc(z.code)} · ${esc(z.name)}</option>`).join("")}</select></div>
          </div>
          <div class="field-row">
            <div class="field"><label for="a-lat">Latitude</label><input id="a-lat" type="number" step="0.0001"></div>
            <div class="field"><label for="a-lng">Longitude</label><input id="a-lng" type="number" step="0.0001"></div>
          </div>
          <button class="btn small">Map pincode</button>
        </form>
      </div>
    </div>`;

  $("#zone-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/admin/zones", { method: "POST", body: {
      code: $("#z-code").value.toUpperCase(), name: $("#z-name").value,
      centroid_lat: +$("#z-lat").value || null, centroid_lng: +$("#z-lng").value || null } });
    toast("Zone added.", "ok"); zonesView();
  });
  $("#area-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/admin/areas", { method: "POST", body: {
      pincode: $("#a-pin").value.trim(), name: $("#a-name").value, zone_id: +$("#a-zone").value,
      lat: +$("#a-lat").value || null, lng: +$("#a-lng").value || null } });
    toast("Pincode mapped.", "ok"); zonesView();
  });
  document.querySelectorAll("[data-del-area]").forEach((b) =>
    b.addEventListener("click", async () => {
      await api(`/api/admin/areas/${b.dataset.delArea}`, { method: "DELETE" });
      toast("Pincode unmapped.", "ok"); zonesView();
    })
  );
}

async function ratesView() {
  shell(`${head("Configuration", "Rate cards, COD & engine settings",
    "Intra-zone and inter-zone rates are configured separately for B2B and B2C. A card with both zones set overrides the default for that lane.")}
    <div id="r-slot" class="loading">Loading…</div>`);
  const [cards, cod, settings, zones] = await Promise.all([
    api("/api/admin/rate-cards"), api("/api/admin/cod-rules"), api("/api/admin/settings"), api("/api/admin/zones"),
  ]);

  ready("#r-slot").innerHTML = `
    <div class="panel">
      <div class="panel-head"><h3>Rate cards</h3><span class="dim">${cards.filter((c) => c.is_active).length} active</span></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Card</th><th>Type</th><th>Scope</th><th>Lane</th><th>Base</th><th>Then</th><th>Min</th><th>Fuel</th><th></th></tr></thead>
        <tbody>${cards.map((c) => `<tr style="${c.is_active ? "" : "opacity:.4"}">
          <td>${esc(c.name)}</td>
          <td><span class="pill">${c.order_type}</span></td>
          <td><span class="pill ${c.scope === "INTRA" ? "done" : "live"}">${c.scope}</span></td>
          <td class="dim mono" style="font-size:12px">${c.from_zone?.code || "any"} → ${c.to_zone?.code || "any"}</td>
          <td class="num">${money(c.base_price)} <span class="dim">/${c.base_weight_kg}kg</span></td>
          <td class="num">${money(c.increment_price)} <span class="dim">/${c.increment_weight_kg}kg</span></td>
          <td class="num">${money(c.min_charge)}</td>
          <td class="num">${c.fuel_surcharge_pct}%</td>
          <td style="text-align:right">${c.is_active ? `<button class="btn small danger" data-off="${c.id}">Retire</button>` : ""}</td>
        </tr>`).join("")}</tbody></table></div>

      <form id="card-form" style="margin-top:20px;border-top:1px solid var(--line-soft);padding-top:16px">
        <span class="eyebrow">Add a rate card</span>
        <div class="field-row" style="margin-top:10px">
          <div class="field"><label for="c-name">Name</label><input id="c-name" required placeholder="B2C inter promo"></div>
          <div class="field"><label for="c-type">Order type</label><select id="c-type"><option>B2C</option><option>B2B</option></select></div>
          <div class="field"><label for="c-scope">Scope</label><select id="c-scope"><option>INTRA</option><option>INTER</option></select></div>
          <div class="field"><label for="c-from">From zone</label><select id="c-from"><option value="">Any</option>${zones.map((z) => `<option value="${z.id}">${esc(z.code)}</option>`).join("")}</select></div>
          <div class="field"><label for="c-to">To zone</label><select id="c-to"><option value="">Any</option>${zones.map((z) => `<option value="${z.id}">${esc(z.code)}</option>`).join("")}</select></div>
        </div>
        <div class="field-row">
          <div class="field"><label for="c-bw">Base weight kg</label><input id="c-bw" type="number" step="0.1" value="1" required></div>
          <div class="field"><label for="c-bp">Base price ₹</label><input id="c-bp" type="number" step="1" value="45" required></div>
          <div class="field"><label for="c-iw">Slab size kg</label><input id="c-iw" type="number" step="0.1" value="0.5" required></div>
          <div class="field"><label for="c-ip">Price per slab ₹</label><input id="c-ip" type="number" step="1" value="18" required></div>
          <div class="field"><label for="c-mc">Minimum ₹</label><input id="c-mc" type="number" step="1" value="45"></div>
          <div class="field"><label for="c-fs">Fuel %</label><input id="c-fs" type="number" step="0.5" value="0"></div>
        </div>
        <button class="btn small">Add rate card</button>
      </form>
    </div>

    <div class="grid two">
      <div class="panel">
        <div class="panel-head"><h3>COD surcharge</h3></div>
        <p class="dim" style="margin-top:-8px">Charged only on COD orders: the higher of the flat fee or the percentage, then clamped.</p>
        ${cod.map((r) => `<form class="cod-form" data-type="${r.order_type}" style="border-top:1px solid var(--line-soft);padding-top:14px;margin-top:12px">
          <div class="row"><span class="pill">${r.order_type}</span><span class="dim">currently ${money(r.flat_fee)} or ${r.percent_of_freight}%</span></div>
          <div class="field-row" style="margin-top:10px">
            <div class="field"><label>Flat ₹</label><input name="flat_fee" type="number" step="1" value="${r.flat_fee}"></div>
            <div class="field"><label>% of freight</label><input name="percent_of_freight" type="number" step="0.1" value="${r.percent_of_freight}"></div>
            <div class="field"><label>Min ₹</label><input name="min_fee" type="number" step="1" value="${r.min_fee}"></div>
            <div class="field"><label>Cap ₹</label><input name="max_fee" type="number" step="1" value="${r.max_fee ?? ""}"></div>
          </div>
          <button class="btn small ghost">Save ${r.order_type} COD</button>
        </form>`).join("")}
      </div>

      <div class="panel">
        <div class="panel-head"><h3>Engine settings</h3></div>
        <p class="dim" style="margin-top:-8px">The two constants the weight calculation depends on. Changing them re-prices every future quote.</p>
        ${settings.map((s) => `<form class="set-form" data-key="${s.key}" style="border-top:1px solid var(--line-soft);padding-top:14px;margin-top:12px">
          <div class="field"><label>${esc(s.key.replace(/_/g, " "))}</label><input name="value" value="${esc(s.value)}">
            <div class="hint">${esc(s.description || "")}</div></div>
          <button class="btn small ghost">Save</button>
        </form>`).join("")}
      </div>
    </div>`;

  $("#card-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/admin/rate-cards", { method: "POST", body: {
      name: $("#c-name").value, order_type: $("#c-type").value, scope: $("#c-scope").value,
      from_zone_id: +$("#c-from").value || null, to_zone_id: +$("#c-to").value || null,
      base_weight_kg: +$("#c-bw").value, base_price: +$("#c-bp").value,
      increment_weight_kg: +$("#c-iw").value, increment_price: +$("#c-ip").value,
      min_charge: +$("#c-mc").value || 0, fuel_surcharge_pct: +$("#c-fs").value || 0 } });
    toast("Rate card added.", "ok"); ratesView();
  });
  document.querySelectorAll("[data-off]").forEach((b) =>
    b.addEventListener("click", async () => {
      await api(`/api/admin/rate-cards/${b.dataset.off}`, { method: "DELETE" });
      toast("Rate card retired.", "ok"); ratesView();
    })
  );
  document.querySelectorAll(".cod-form").forEach((f) =>
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = Object.fromEntries(new FormData(f).entries());
      await api("/api/admin/cod-rules", { method: "PUT", body: {
        order_type: f.dataset.type, flat_fee: +fd.flat_fee, percent_of_freight: +fd.percent_of_freight,
        min_fee: +fd.min_fee, max_fee: fd.max_fee === "" ? null : +fd.max_fee, is_active: true } });
      toast(`${f.dataset.type} COD rule saved.`, "ok"); ratesView();
    })
  );
  document.querySelectorAll(".set-form").forEach((f) =>
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      await api("/api/admin/settings", { method: "PUT", body: { key: f.dataset.key, value: new FormData(f).get("value") } });
      toast("Setting saved.", "ok"); ratesView();
    })
  );
}

async function agentsView() {
  shell(`${head("People", "Delivery agents",
    "Availability, capacity and last known position — the three inputs auto-assignment ranks on.")}
    <div id="ag-slot" class="loading">Loading…</div>`);
  const [agents, zones] = await Promise.all([api("/api/admin/agents"), api("/api/admin/zones")]);
  ready("#ag-slot").innerHTML = `
    <div class="panel">
      <div class="table-wrap"><table>
        <thead><tr><th>Agent</th><th>Zone</th><th>Vehicle</th><th>Load</th><th>Last position</th><th>Duty</th><th></th></tr></thead>
        <tbody>${agents.map((a) => `<tr>
          <td><b>${esc(a.user.name)}</b><div class="dim">${esc(a.user.email)}</div></td>
          <td><span class="pill">${esc(a.home_zone?.code || "—")}</span></td>
          <td class="dim">${esc(a.vehicle_type)}</td>
          <td class="num">${a.active_orders}/${a.max_active_orders}</td>
          <td class="dim mono" style="font-size:12px">${a.current_lat ? `${a.current_lat.toFixed(3)}, ${a.current_lng.toFixed(3)}` : "unknown"}</td>
          <td>${a.is_available ? '<span class="pill done">On duty</span>' : '<span class="pill">Off duty</span>'}</td>
          <td style="text-align:right"><button class="btn small ghost" data-toggle="${a.user_id}" data-now="${a.is_available}">
            ${a.is_available ? "Stand down" : "Put on duty"}</button></td>
        </tr>`).join("")}</tbody></table></div>
    </div>
    <div class="panel">
      <div class="panel-head"><h3>Add an agent</h3></div>
      <form id="agent-form">
        <div class="field-row">
          <div class="field"><label for="g-name">Name</label><input id="g-name" required></div>
          <div class="field"><label for="g-email">Email</label><input id="g-email" type="email" required></div>
          <div class="field"><label for="g-phone">Mobile</label><input id="g-phone"></div>
          <div class="field"><label for="g-pass">Temporary password</label><input id="g-pass" minlength="6" required value="Passw0rd!"></div>
        </div>
        <div class="field-row">
          <div class="field"><label for="g-zone">Home zone</label><select id="g-zone"><option value="">None</option>${zones.map((z) => `<option value="${z.id}">${esc(z.code)} · ${esc(z.name)}</option>`).join("")}</select></div>
          <div class="field"><label for="g-veh">Vehicle</label><select id="g-veh"><option>BIKE</option><option>VAN</option><option>TRUCK</option></select></div>
          <div class="field"><label for="g-cap">Max active orders</label><input id="g-cap" type="number" min="1" value="5"></div>
          <div class="field"><label for="g-lat">Start latitude</label><input id="g-lat" type="number" step="0.0001"></div>
          <div class="field"><label for="g-lng">Start longitude</label><input id="g-lng" type="number" step="0.0001"></div>
        </div>
        <button class="btn small">Add agent</button>
      </form>
    </div>`;

  document.querySelectorAll("[data-toggle]").forEach((b) =>
    b.addEventListener("click", async () => {
      await api(`/api/admin/agents/${b.dataset.toggle}`, { method: "PATCH", body: { is_available: b.dataset.now !== "true" } });
      toast("Duty status updated.", "ok"); S.cache.agents = null; agentsView();
    })
  );
  $("#agent-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/admin/agents", { method: "POST", body: {
      name: $("#g-name").value, email: $("#g-email").value, phone: $("#g-phone").value || null,
      password: $("#g-pass").value, home_zone_id: +$("#g-zone").value || null,
      vehicle_type: $("#g-veh").value, max_active_orders: +$("#g-cap").value,
      current_lat: +$("#g-lat").value || null, current_lng: +$("#g-lng").value || null } });
    toast("Agent added.", "ok"); S.cache.agents = null; agentsView();
  });
}

async function notificationsView() {
  shell(`${head("Comms", "Notification log",
    "Every customer email and SMS the platform has produced. Without provider keys they're recorded as simulated so the flow stays auditable.")}
    <div id="n-slot" class="loading">Loading…</div>`);
  const [rows, health] = await Promise.all([api("/api/admin/notifications"), api("/api/health")]);
  ready("#n-slot").innerHTML = `
    <div class="panel">
      <div class="panel-head"><h3>Providers</h3></div>
      <div class="row wrap">
        <span class="pill ${health.email_provider === "smtp" ? "done" : ""}">Email · ${health.email_provider}</span>
        <span class="pill ${health.sms_provider !== "simulated" ? "done" : ""}">SMS · ${health.sms_provider}</span>
        <span class="dim">Set SMTP_* and SMS_PROVIDER in .env to switch from simulated to live sending.</span>
      </div>
    </div>
    <div class="panel">
      ${rows.length ? `<div class="table-wrap"><table>
        <thead><tr><th>Sent</th><th>Channel</th><th>To</th><th>Trigger</th><th>Subject</th><th>Result</th></tr></thead>
        <tbody>${rows.map((n) => `<tr>
          <td class="dim mono" style="font-size:12px">${when(n.created_at)}</td>
          <td><span class="pill">${n.channel}</span></td>
          <td class="dim">${esc(n.recipient)}</td>
          <td>${n.trigger_status ? pill(n.trigger_status) : "—"}</td>
          <td>${esc(n.subject || "—")}</td>
          <td><span class="pill ${n.status === "SENT" ? "done" : n.status === "FAILED" ? "bad" : ""}">${title(n.status)}</span></td>
        </tr>`).join("")}</tbody></table></div>`
        : emptyState("Nothing sent yet", "Notifications appear the moment an order changes status.")}
    </div>`;
}

/* =========================================================================
   Router
   ========================================================================= */
const ROUTES = {
  CUSTOMER: { book: bookView, orders: ordersView },
  AGENT: { jobs: agentJobsView, profile: agentProfileView },
  ADMIN: { overview: overviewView, orders: ordersView, book: bookView, zones: zonesView,
           rates: ratesView, agents: agentsView, notifications: notificationsView },
};
const HOME = { CUSTOMER: "orders", AGENT: "jobs", ADMIN: "overview" };

function render() {
  closeDrawer();
  if (!S.token || !S.user) return authView();
  const routes = ROUTES[S.user.role];
  if (!S.view || !routes[S.view]) S.view = HOME[S.user.role];
  Promise.resolve(routes[S.view]()).catch((err) => {
    app().innerHTML = `<div class="empty"><b>Something went wrong</b>${esc(err.message)}</div>`;
  });
}

// Deep link: /?track=LM2604... opens that waybill straight after sign-in
(async function boot() {
  render();
  const code = new URLSearchParams(location.search).get("track");
  if (code && S.token) {
    try {
      const o = await api(`/api/orders/code/${encodeURIComponent(code)}`, { quiet: true });
      setTimeout(() => openOrder(o.id), 400);
    } catch {}
  }
})();
