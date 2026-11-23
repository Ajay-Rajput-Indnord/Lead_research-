const API = "http://127.0.0.1:8765/api";
const NOTE_LIMIT = 300;
const $ = (id) => document.getElementById(id);

let companyId = localStorage.getItem("companyId") || "";
let company = null;      // details from /api/companies/:id
let tab = null;          // active tab
let emailConfigured = false;
let emailFrom = "";

// ---------- helpers ----------
async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

function toast(msg, ms = 3500) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), ms);
}

async function busy(btn, label, fn) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = label;
  try { return await fn(); }
  catch (e) { toast(e.message, 6000); }
  finally { btn.disabled = false; btn.textContent = old; }
}

function companySlug(url) {
  const m = /linkedin\.com\/(?:company|showcase|school)\/([^/?#]+)/i.exec(url || "");
  return m ? m[1].toLowerCase() : null;
}

function pageKind(url) {
  if (/linkedin\.com\/in\/[^/?#]+/i.test(url || "")) return "profile";
  if (!companySlug(url)) return null;
  const path = url.split("?")[0].replace(/\/$/, "").toLowerCase();
  if (/\/posts(\/|$)/.test(path)) return "posts";
  if (/\/people(\/|$)/.test(path)) return "people";
  return "about";
}

// Runs inside the LinkedIn tab when the user clicks "Read this page". Reads visible text only.
function readVisiblePage() {
  const root = document.querySelector("main") || document.body;
  const text = root.innerText.replace(/\n{3,}/g, "\n\n").slice(0, 50000);
  const seen = new Set();
  const links = [];
  for (const a of root.querySelectorAll('a[href*="/in/"]')) {
    const href = a.href.split("?")[0];
    if (seen.has(href)) continue;
    seen.add(href);
    links.push({ href, text: (a.innerText || a.getAttribute("aria-label") || "").trim().slice(0, 200) });
    if (links.length >= 500) break;
  }
  return { url: location.href, text, links };
}

// ---------- rendering ----------
async function refreshTab() {
  [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  // Some Chrome setups hide tab.url from side panels; ask the page itself as a fallback.
  if (tab && !tab.url) {
    try {
      const [{ result }] = await chrome.scripting.executeScript({
        target: { tabId: tab.id }, func: () => location.href,
      });
      tab.url = result;
    } catch { /* not a page we have access to */ }
  }
  renderPageState();
}

function renderPageState() {
  const url = tab?.url || "";
  $("tabUrl").textContent = url ? url.split("?")[0] : "(could not read this tab's address)";
  if (!company) return;
  const kind = pageKind(url);
  const linkedSlug = companySlug(company.linkedin_url);
  const thisSlug = companySlug(url);

  // Link row
  const linkRow = $("linkRow");
  linkRow.innerHTML = "";
  if (linkedSlug) {
    linkRow.textContent = `LinkedIn: linkedin.com/company/${linkedSlug}`;
  } else if (thisSlug) {
    const b = document.createElement("button");
    b.className = "primary";
    b.textContent = `Link linkedin.com/company/${thisSlug} to ${company.name}`;
    b.onclick = () => busy(b, "Linking…", async () => {
      await api(`/companies/${companyId}/link`, { method: "POST", body: { url } });
      await loadCompany();
      toast("Linked. Now read the About, Posts and People pages.");
    });
    linkRow.append(b);
  } else {
    linkRow.textContent = `Open ${company.name}'s LinkedIn company page, then link it here.`;
  }

  // Current page + read button
  let label = /linkedin\.com/i.test(url)
    ? "a LinkedIn page, but not a company page or profile — open linkedin.com/company/… or linkedin.com/in/…"
    : "not LinkedIn — switch to (or open) your LinkedIn tab in this window";
  let canRead = false;
  if (kind === "profile") { label = "person profile"; canRead = !!linkedSlug; }
  else if (kind) {
    if (thisSlug === linkedSlug) { label = `company ${kind}`; canRead = true; }
    else label = linkedSlug ? "a different company (won't be read)" : `company ${kind} (link it first)`;
  }
  $("pageKind").textContent = label;
  $("readBtn").disabled = !canRead;
  document.querySelectorAll("[data-go]").forEach((b) => (b.disabled = !linkedSlug));
}

function renderCompany() {
  $("steps").hidden = !company;
  $("suggestBox").hidden = !company;
  $("peopleBox").hidden = !company;
  if (!company) return;

  $("chkAbout").className = company.has_about ? "done" : "";
  $("chkAbout").textContent = company.has_about ? "About ✓" : "About";
  $("chkPosts").className = company.posts_count ? "done" : "";
  $("chkPosts").textContent = `Posts ${company.posts_count}/3`;
  $("chkPeople").className = company.people.length ? "done" : "";
  $("chkPeople").textContent = `People ${company.people.length}`;
  $("peopleCount").textContent = `(${company.people.length})`;
  $("suggestBtn").disabled = !company.people.length;

  const list = $("people");
  list.innerHTML = "";
  for (const p of company.people) list.append(personCard(p));
  renderPageState();
}

function personCard(p) {
  const el = $("personTpl").content.firstElementChild.cloneNode(true);
  const q = (s) => el.querySelector(s);
  q(".name").textContent = p.full_name || "(unknown)";
  q(".headline").textContent = p.linkedin_headline || p.job_title || "";
  q(".score").textContent = p.connect_score ? `${p.connect_score}/10` : "";
  q(".reason").textContent = p.connect_reason || "";
  q(".angle").textContent = p.connect_angle ? `Angle: ${p.connect_angle}` : "";
  if (["sent", "skipped"].includes(p.outreach_status)) {
    el.classList.add("dim");
    q(".write").textContent = p.outreach_status === "sent" ? "Sent ✓" : "Skipped";
    q(".write").disabled = true;
  }

  const showDraft = (note, follow) => {
    q(".draft").hidden = false;
    q(".note").value = note || "";
    q(".followup").value = follow || "";
    updateChars();
  };
  const updateChars = () => {
    const n = q(".note").value.length;
    q(".chars").textContent = `${n}/${NOTE_LIMIT}`;
    q(".chars").classList.toggle("over", n > NOTE_LIMIT);
  };
  q(".note").addEventListener("input", updateChars);
  if (p.connection_note) showDraft(p.connection_note, p.followup_message);

  q(".open").onclick = () => p.linkedin_url && chrome.tabs.update(tab.id, { url: p.linkedin_url });
  q(".open").disabled = !p.linkedin_url;

  const write = (btn, feedback) => busy(btn, "Writing…", async () => {
    const r = await api(`/employees/${p.employee_id}/message`, { method: "POST", body: { feedback } });
    showDraft(r.connection_note, r.followup_message);
    q(".feedback").value = "";
  });
  q(".write").onclick = () => write(q(".write"), null);
  q(".regen").onclick = () => write(q(".regen"), q(".feedback").value.trim() || null);

  q(".copyNote").onclick = async () => { await navigator.clipboard.writeText(q(".note").value); toast("Note copied. Paste it in LinkedIn's 'Add a note'."); };
  q(".copyFollow").onclick = async () => { await navigator.clipboard.writeText(q(".followup").value); toast("Follow-up copied."); };

  const setStatus = (status) => async () => {
    await api(`/employees/${p.employee_id}/status`, { method: "POST", body: { status } });
    await loadCompany();
  };
  q(".sent").onclick = setStatus("sent");
  q(".skip").onclick = setStatus("skipped");

  // ----- email -----
  q(".emailAddr").value = p.work_email || "";
  const showEmail = (subject, body) => {
    q(".emailDraft").hidden = false;
    q(".emailSubject").value = subject || "";
    q(".emailBody").value = body || "";
  };
  if (p.email_status === "sent") {
    q(".emailSent").hidden = false;
    q(".emailSent").textContent = `Email sent to ${p.work_email} on ${(p.email_sent_at || "").replace("T", " ")} — "${p.email_subject}"`;
    q(".writeEmail").hidden = true;
    q(".saveEmail").disabled = true;
    q(".emailAddr").disabled = true;
  } else if (p.email_subject) {
    showEmail(p.email_subject, p.email_body);
  }
  if (!emailConfigured) {
    q(".sendEmail").disabled = true;
    q(".sendEmail").title = "Set the SMTP_* values in .env and restart the server";
  }

  const saveEmail = async () => {
    const email = q(".emailAddr").value.trim();
    if (email === (p.work_email || "")) return;
    await api(`/employees/${p.employee_id}/email-address`, { method: "POST", body: { email } });
    p.work_email = email;
  };
  q(".saveEmail").onclick = () => busy(q(".saveEmail"), "Saving…", async () => { await saveEmail(); toast("Email address saved."); });

  const writeEmail = (btn, feedback) => busy(btn, "Writing…", async () => {
    const r = await api(`/employees/${p.employee_id}/email-draft`, { method: "POST", body: { feedback } });
    showEmail(r.subject, r.body);
    q(".emailFeedback").value = "";
  });
  q(".writeEmail").onclick = () => writeEmail(q(".writeEmail"), null);
  q(".regenEmail").onclick = () => writeEmail(q(".regenEmail"), q(".emailFeedback").value.trim() || null);

  q(".sendEmail").onclick = () => busy(q(".sendEmail"), "Sending…", async () => {
    await saveEmail();
    const to = q(".emailAddr").value.trim();
    if (!to) throw new Error("Add the email address first.");
    const subject = q(".emailSubject").value;
    if (!confirm(`Send this email now?\n\nTo: ${to}\nFrom: ${emailFrom}\nSubject: ${subject}`)) return;
    const r = await api(`/employees/${p.employee_id}/send-email`, {
      method: "POST", body: { subject, body: q(".emailBody").value },
    });
    toast(r.message);
    await loadCompany();
  });
  return el;
}

// ---------- loading ----------
async function loadCompanies() {
  const sel = $("company");
  try {
    const health = await api("/health");
    emailConfigured = !!health.email_configured;
    emailFrom = health.email_from || "";
    const rows = await api("/companies");
    $("server").textContent = emailConfigured ? "connected · email on" : "connected · email off";
    $("server").className = "pill ok";
    sel.replaceChildren(new Option("— choose an approved company —", ""),
      ...rows.map((c) => new Option(`#${c.company_id} ${c.name} (${c.domain || ""})`, String(c.company_id))));
    if (rows.some((c) => String(c.company_id) === companyId)) sel.value = companyId;
    else companyId = "";
    await loadCompany();
  } catch {
    $("server").textContent = "server offline";
    $("server").className = "pill bad";
    sel.innerHTML = `<option>Start it: python main.py serve</option>`;
    setTimeout(loadCompanies, 4000);
  }
}

async function loadCompany() {
  company = companyId ? await api(`/companies/${companyId}`) : null;
  if (!company) $("linkRow").textContent = "";
  renderCompany();
}

// ---------- events ----------
$("company").onchange = async (e) => {
  companyId = e.target.value;
  localStorage.setItem("companyId", companyId);
  $("strategy").hidden = true;
  await loadCompany();
};

document.querySelectorAll("[data-go]").forEach((b) => {
  b.onclick = () => {
    const slug = companySlug(company?.linkedin_url);
    if (slug) chrome.tabs.update(tab.id, { url: `https://www.linkedin.com/company/${slug}/${b.dataset.go}/` });
  };
});

$("readBtn").onclick = () => busy($("readBtn"), "Reading…", async () => {
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: readVisiblePage });
  const r = await api(`/companies/${companyId}/capture`, { method: "POST", body: result });
  toast(r.message);
  await loadCompany();
});

$("suggestBtn").onclick = () => busy($("suggestBtn"), "Thinking…", async () => {
  const r = await api(`/companies/${companyId}/suggest`, { method: "POST" });
  $("strategy").textContent = r.overall_strategy;
  $("strategy").hidden = false;
  await loadCompany();
});

chrome.tabs.onActivated.addListener(refreshTab);
chrome.tabs.onUpdated.addListener((id, info) => { if (info.url || info.status === "complete") refreshTab(); });

refreshTab().then(loadCompanies);
