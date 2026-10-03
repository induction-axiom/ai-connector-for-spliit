import {initializeApp} from "https://www.gstatic.com/firebasejs/12.3.0/firebase-app.js";
import {getAuth, GoogleAuthProvider, signInWithPopup, browserSessionPersistence,
  setPersistence, onAuthStateChanged, signOut}
  from "https://www.gstatic.com/firebasejs/12.3.0/firebase-auth.js";

const el = id => document.getElementById(id);
const VIEWS = ["overview", "developer"];
let auth, user, status, keyFormOpen = false, busy = false;

// ---------- Small helpers ----------

class OwnerSessionExpired extends Error {}

async function api(path, body = {}) {
  const response = await fetch(path, {method: "POST", credentials: "same-origin",
    headers: {"Authorization": "Bearer " + await user.getIdToken(),
      "Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await response.json().catch(() => ({}));
  if (response.status === 403 && data.error === "owner_login_required") throw new OwnerSessionExpired();
  if (!response.ok && !data.result) throw new Error(data.error || "request_failed");
  return data;
}

function ago(value) {
  if (!value) return null;
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const seconds = (date.getTime() - Date.now()) / 1000;
  const [size, unit] = [[86400, "day"], [3600, "hour"], [60, "minute"], [1, "second"]]
    .find(([size]) => Math.abs(seconds) >= size) || [1, "second"];
  return new Intl.RelativeTimeFormat(undefined, {numeric: "auto"}).format(Math.round(seconds / size), unit);
}

function monthDay(value) {
  const date = typeof value === "number" ? new Date(value * 1000) : null;
  if (!date || Number.isNaN(date.getTime())) return null;
  const year = date.getFullYear() === new Date().getFullYear() ? undefined : "numeric";
  return new Intl.DateTimeFormat(undefined, {month: "short", day: "numeric", year}).format(date);
}

function say(id, text, tone = "neutral") {
  el(id).textContent = text;
  el(id).dataset.tone = tone;
}

// ---------- Plain-language copy for result codes ----------

// What each Splitwise outcome means for the owner.
const SPLITWISE_COPY = {
  connected: "Answering",
  key_missing: "No API key yet",
  key_rejected: "Splitwise doesn't accept the key",
  rate_limited: "Splitwise asked the connector to slow down",
  splitwise_unavailable: "Splitwise isn't answering",
  forbidden: "Splitwise refused the request",
};

const KEY_COPY = {
  key_saved: ["Key saved. Your AI apps can use Splitwise now.", "success"],
  key_invalid: ["Paste your Splitwise API key first.", "danger"],
  key_rejected: ["Splitwise didn't accept that key. Create a new one and try again.", "danger"],
  rate_limited: ["Splitwise is limiting requests. Wait a minute and try again.", "warning"],
  splitwise_unavailable: ["Splitwise isn't answering right now. Nothing was saved; try again later.", "warning"],
};

// ---------- Views and routing ----------

function currentView() {
  const name = location.hash.slice(1);
  return VIEWS.includes(name) ? name : "overview";
}

function showView() {
  const name = currentView();
  for (const view of VIEWS) el(view + "-view").classList.toggle("hidden", view !== name);
  for (const tab of el("tabs").querySelectorAll("a"))
    tab.setAttribute("aria-current", tab.dataset.view === name ? "page" : "false");
  if (name === "developer" && user) preview(previewTarget);
  window.scrollTo({top: 0});
}

function showSignedIn(signedIn) {
  el("signin-view").classList.toggle("hidden", signedIn);
  el("tabs").classList.toggle("hidden", !signedIn);
  el("account").classList.toggle("hidden", !signedIn);
  if (signedIn) showView();
  else for (const view of VIEWS) el(view + "-view").classList.add("hidden");
}

// ---------- Overview ----------

// "Gemini", "Gemini and Claude", "3 apps"
function appNames(apps) {
  const names = [...new Set(apps.map(appName))];
  return names.length > 2 ? names.length + " apps" : names.join(" and ");
}

// The page answers one question first: can my AI use my Splitwise?
function overallState(splitwise, apps) {
  if (splitwise.state === "key_missing") return {
    tone: "neutral", label: "Setup", title: "Add your Splitwise API key.", action: "key",
    summary: "Your AI apps use Splitwise through this key. It takes a minute to create one.",
  };
  if (splitwise.state === "key_rejected") return {
    tone: "danger", label: "Action required", title: "Replace your Splitwise API key.", action: "key",
    summary: "Splitwise no longer accepts the saved key, so your AI apps can't use Splitwise.",
  };
  if (splitwise.state !== "connected") return {
    tone: "warning", label: "Needs attention", title: "Splitwise isn't answering right now.", action: "check",
    summary: (SPLITWISE_COPY[splitwise.state] || "Something went wrong") + ". Your AI apps will try again when you ask.",
  };
  const name = splitwise.user?.first_name;
  if (!apps.length) return {
    tone: "neutral", label: "Almost ready", title: "Connect an AI app.", action: "guide",
    summary: `Splitwise is connected${name ? " as " + name : ""}. Add it to an AI app to start asking about your expenses.`,
  };
  const used = apps.map(app => app.last_used_at).filter(Boolean).sort().at(-1);
  return {
    tone: "success", label: "Ready", title: `${appNames(apps)} can use your Splitwise.`, action: "check",
    summary: `Connected${name ? " as " + name : ""}.` + (used ? ` Last used ${ago(used)}.` : ""),
  };
}

function renderStatus() {
  const splitwise = status.splitwise || {};
  const failure = status.last_failure;
  const apps = status.apps || [];
  const state = overallState(splitwise, apps);
  el("overview-hero").dataset.tone = state.tone;
  el("status-label").textContent = state.label;
  el("status-title").textContent = state.title;
  el("status-summary").textContent = state.summary;
  el("primary-action").disabled = busy;
  el("primary-action").dataset.action = state.action;
  el("primary-action").textContent = state.action === "guide" ? "Connect an AI app" : "Check again";
  // When a key is needed, the form itself is the one action on the page.
  const needsKey = state.action === "key";
  el("primary-action").classList.toggle("hidden", needsKey);
  showKeyForm(needsKey || keyFormOpen, !needsKey);
  el("key-title").textContent = splitwise.state === "key_missing" ? "Add your Splitwise API key"
    : "Replace your Splitwise API key";
  el("key-remove").classList.toggle("hidden", !splitwise.key_saved_at);
  el("key-replace").classList.toggle("hidden", !splitwise.key_saved_at);
  el("apps-list").replaceChildren(...(apps.length ? apps.map(appRow) : [emptyRow("No apps are connected yet.")]));

  const person = splitwise.user;
  el("fact-account").textContent = person
    ? [person.first_name, person.last_name].filter(Boolean).join(" ") + (person.email ? ` (${person.email})` : "")
    : "—";
  el("fact-key").textContent = splitwise.key_saved_at ? "Saved " + monthDay(splitwise.key_saved_at) : "Not added";
  el("fact-live").textContent = SPLITWISE_COPY[splitwise.state] || splitwise.state || "—";
  el("fact-last-error").textContent = failure
    ? `${SPLITWISE_COPY[failure.code] || failure.code}, ${ago(failure.at)}` : "None";

  const diagnostics = status.diagnostics || {};
  el("mcp-url").textContent = diagnostics.mcp_endpoint || "—";
  // With several deployments, the project ID is how you tell this one apart.
  if (diagnostics.project_id) {
    el("project-id").textContent = diagnostics.project_id;
    el("project").classList.remove("hidden");
    document.title = diagnostics.project_id + " · AI connector for Splitwise";
    renderResources(diagnostics);
  }
  el("fact-version").textContent = [diagnostics.connector_version, diagnostics.commit?.slice(0, 7),
    diagnostics.committed_on].filter(Boolean).join(" · ") || "—";
  if (diagnostics.repository) {
    const github = `https://github.com/${diagnostics.repository}`;
    el("source").href = diagnostics.commit ? `${github}/tree/${diagnostics.commit}` : github;
    el("source").classList.remove("hidden");
    el("fact-source").replaceChildren(external(github, diagnostics.repository + " ↗"));
  }
  checkForUpdate(diagnostics.connector_version, diagnostics.repository);
  el("fact-state").firstElementChild.textContent = splitwise.state || "—";
}

async function loadStatus() {
  status = await api("/owner/status");
  renderStatus();
}

// One operation at a time: these controls stay disabled until it ends.
function setBusy(value) {
  busy = value;
  for (const id of ["primary-action", "key-submit", "key-remove", "key-replace"]) el(id).disabled = value;
}

function showKeyForm(show, cancellable) {
  el("key-panel").classList.toggle("hidden", !show);
  el("key-cancel").classList.toggle("hidden", !cancellable);
  if (!show) el("key-form").reset();
}

el("primary-action").onclick = async () => {
  if (el("primary-action").dataset.action === "guide") return el("apps").scrollIntoView({behavior: "smooth"});
  setBusy(true);
  say("message", "Checking Splitwise…");
  try {
    await loadStatus();
    say("message", "");
  } catch (error) {
    if (error instanceof OwnerSessionExpired) return expireSession();
    say("message", "Your connector's status couldn't be loaded. Try again.", "danger");
  } finally {
    setBusy(false);
  }
};

el("key-form").onsubmit = async event => {
  event.preventDefault();
  setBusy(true);
  say("message", "Checking the key with Splitwise…");
  try {
    const {result} = await api("/owner/key", {api_key: el("api-key").value});
    el("api-key").value = "";
    if (result === "key_saved") keyFormOpen = false;
    await loadStatus();
    say("message", ...(KEY_COPY[result] || ["The key couldn't be checked. Nothing was saved.", "danger"]));
  } catch (error) {
    if (error instanceof OwnerSessionExpired) return expireSession();
    say("message", "The key couldn't be saved. Try again.", "danger");
  } finally {
    setBusy(false);
  }
  window.scrollTo({top: 0, behavior: "smooth"});
};

el("key-replace").onclick = () => {
  keyFormOpen = true;
  renderStatus();
  el("key-panel").scrollIntoView({behavior: "smooth"});
  el("api-key").focus();
};

el("key-cancel").onclick = () => {
  keyFormOpen = false;
  renderStatus();
};

// Two clicks: the first asks, the second destroys the saved key.
el("key-remove").onclick = async () => {
  const button = el("key-remove");
  if (!button.dataset.confirm) {
    button.dataset.confirm = "1";
    button.textContent = "Click again to remove";
    return;
  }
  delete button.dataset.confirm;
  button.textContent = "Remove key";
  setBusy(true);
  try {
    await api("/owner/key/remove");
    await loadStatus();
    say("message", "Key removed. Your AI apps can't use Splitwise until you add one again. "
      + "To cancel the key itself, delete it on your Splitwise apps page.", "success");
    window.scrollTo({top: 0, behavior: "smooth"});
  } catch (error) {
    if (error instanceof OwnerSessionExpired) return expireSession();
    say("message", "Couldn't remove the key. Try again.", "danger");
  } finally {
    setBusy(false);
  }
};

// ---------- AI apps ----------

function appName(app) {
  // Gemini registers under the name "Google"; its callback host is clearer.
  if (app.redirect_host === "oauth-redirect.googleusercontent.com") return "Gemini";
  if (app.name) return app.name;
  if (app.redirect_host === "chatgpt.com") return "ChatGPT";
  if (app.redirect_host === "claude.ai" || app.redirect_host === "claude.com") return "Claude";
  return app.redirect_host || "Unknown app";
}

function emptyRow(text) {
  const row = document.createElement("li");
  row.className = "row muted";
  row.textContent = text;
  return row;
}

function appRow(app) {
  const row = document.createElement("li");
  row.className = "row";
  const name = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = appName(app);
  const detail = document.createElement("span");
  const connected = monthDay(app.connected_at);
  detail.textContent = [connected ? "Connected " + connected : "Connected",
    app.last_used_at && "Last used " + ago(app.last_used_at)].filter(Boolean).join(" · ");
  name.append(title, detail);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "link-button danger";
  button.textContent = "Disconnect";
  // Two-step: the first click asks, the second one disconnects.
  button.onclick = async () => {
    if (!button.dataset.confirm) {
      button.dataset.confirm = "1";
      button.textContent = "Confirm disconnect";
      return;
    }
    button.disabled = true;
    try {
      await api("/owner/apps/disconnect", {client_id: app.client_id});
      await loadStatus();
    } catch (error) {
      if (error instanceof OwnerSessionExpired) return expireSession();
      button.textContent = "Try again";
      button.disabled = false;
    }
  };
  row.append(name, button);
  return row;
}

async function copyText(value, button) {
  try {
    await navigator.clipboard.writeText(value);
    button.textContent = "Copied";
  } catch {
    button.textContent = "Select and copy";
  }
  setTimeout(() => { button.textContent = "Copy"; }, 2000);
}
el("copy-url").onclick = event => copyText(el("mcp-url").textContent, event.currentTarget);

function copyField(value) {
  const field = document.createElement("div");
  field.className = "copy-field";
  const code = document.createElement("code");
  code.textContent = value;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "pill small";
  button.textContent = "Copy";
  button.onclick = () => copyText(value, button);
  field.append(code, button);
  return field;
}

function external(href, text, className = "") {
  const link = document.createElement("a");
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener";
  link.className = className;
  link.textContent = text;
  return link;
}

// Where Gemini Spark lists and adds custom apps, checked September 2026.
const GEMINI_APPS = "https://gemini.google.com/spark/apps";
// Where ChatGPT lists and manages custom plugins, checked September 2026.
const CHATGPT_PLUGINS = "https://chatgpt.com/plugins";
// Where Claude lists and manages connectors, checked September 2026.
const CLAUDE_CONNECTORS = "https://claude.ai/customize/connectors";

// A step is text, optionally with a value to copy or a link to open.
function guides() {
  const d = status?.diagnostics || {};
  return {
    chatgpt: {title: "Connect ChatGPT", steps: [
      {text: "Open Plugins in ChatGPT on the web.",
        href: CHATGPT_PLUGINS, label: "Open ChatGPT Plugins"},
      "Choose Add, then Create MCP App. Name it Splitwise.",
      {text: "Under Connection, keep Server URL and paste this address. Leave Authentication on OAuth.",
        copy: d.mcp_endpoint},
      "Tick I understand and want to continue, then choose Create.",
      "When a sign-in window opens, choose this Google account and allow access.",
      "On the plugin's page, choose Try in chat. Later, type @Splitwise in any chat to use it.",
    ], note: "No Create MCP App under Add? Turn on Developer mode in ChatGPT's settings first. ChatGPT renames its menus from time to time; look for the closest match."},
    claude: {title: "Connect Claude", steps: [
      {text: "Open Connectors in Claude on the web.",
        href: CLAUDE_CONNECTORS, label: "Open Claude Connectors"},
      "Choose +, then Add custom connector. Name it Splitwise.",
      {text: "Paste this address as the server URL, then continue.", copy: d.mcp_endpoint},
      "Keep the detected settings: Sign in now, and Register automatically. Leave Request headers empty, then choose Add.",
      "When a sign-in window opens, choose this Google account and allow access.",
      "In a chat, make sure Splitwise is turned on under + and Connectors, then ask about your expenses.",
    ], note: "Claude renames its menus from time to time; look for the closest match."},
    gemini: {title: "Connect Gemini", steps: [
      {text: "Open Apps in Gemini Spark on the web.",
        href: GEMINI_APPS, label: "Open Gemini Spark apps"},
      {text: "Choose Add a custom app, and paste this address as the MCP server URL.", copy: d.mcp_endpoint},
      "Leave Client ID and Client secret empty, then continue.",
      "When a sign-in window opens, choose this Google account and allow access.",
      "In a Spark task, type @ and choose Splitwise, then ask about your expenses.",
    ], note: "Gemini renames its menus from time to time; look for the closest match."},
    update: {title: `Update to ${latestRelease?.version}`, steps: [
      {text: "Open Cloud Shell with the latest code. Sign in with the Google account that owns this project if asked.",
        // The stable branch always holds the latest release, never unreleased work on main.
        href: `https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/${d.repository}&cloudshell_git_branch=stable&show=terminal`,
        label: "Open Cloud Shell"},
      {text: "Paste this into the terminal and press Enter:",
        copy: `firebase/scripts/bootstrap.sh ${d.project_id} ${user?.email}`},
      "Type y when asked. It takes about 5 minutes. Your API key and AI app connections stay as they are.",
    ], note: "Updating only deploys new code into your own project. You can read every change first under What's new."},
  };
}

function openGuide(name) {
  const guide = guides()[name];
  el("guide-title").textContent = guide.title;
  const list = document.createElement("ol");
  list.className = "steps";
  for (const step of guide.steps) {
    const {text, copy, href, label} = typeof step === "string" ? {text: step} : step;
    const item = document.createElement("li");
    item.append(text);
    if (copy) item.append(copyField(copy));
    if (href) item.append(external(href, label, "pill small step-link"));
    list.append(item);
  }
  const note = document.createElement("p");
  note.className = "panel-note";
  note.textContent = guide.note;
  el("guide-body").replaceChildren(list, note);
  el("guide").showModal();
}

for (const button of document.querySelectorAll("[data-guide]"))
  button.onclick = () => openGuide(button.dataset.guide);
// Clicking the dimmed backdrop closes the sheet.
el("guide").onclick = event => { if (event.target === el("guide")) el("guide").close(); };

// ---------- Updates ----------

let latestRelease = null;

function isNewer(candidate, current) {
  const [a, b] = [candidate, current].map(v => v.split(".").map(Number));
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i];
  return false;
}

// Asks GitHub from your browser, once per page load; nothing is sent about you or your data.
async function checkForUpdate(current, repository) {
  if (latestRelease || !repository || !/^\d+\.\d+\.\d+$/.test(current || "")) return;
  try {
    const response = await fetch(`https://api.github.com/repos/${repository}/releases/latest`);
    const release = response.ok ? await response.json() : null;
    const version = release?.tag_name?.replace(/^v/, "");
    if (!version || !/^\d+\.\d+\.\d+$/.test(version) || !isNewer(version, current)) return;
    latestRelease = {version, url: release.html_url};
    el("update-text").textContent = `Version ${version} is available. You're on ${current}.`;
    el("update-notes").href = release.html_url;
    el("update").classList.remove("hidden");
  } catch {
    // Offline or rate-limited: simply no banner.
  }
}
el("update-how").onclick = () => openGuide("update");

// ---------- Your Firebase project ----------

function renderResources(d) {
  const project = encodeURIComponent(d.project_id);
  const firebase = `https://console.firebase.google.com/project/${project}`;
  const cloud = path => `https://console.cloud.google.com/${path}?project=${project}`;
  const database = id => `${firebase}/firestore/databases/${id === "(default)" ? "-default-" : id}/data`;
  // The Cloud Run address is <service>-<project number>.<region>.run.app.
  const [label, region] = new URL(d.mcp_endpoint).hostname.split(".");
  const service = label.replace(/-\d+$/, "");
  const rows = [
    ["This dashboard and MCP", "The Cloud Run service your AI apps talk to", cloud(`run/detail/${region}/${service}/metrics`)],
    ["Splitwise API key", "Secret Manager; only the connector can read it", cloud(`security/secret-manager/secret/${d.api_key_secret}/versions`)],
    ["AI app sign-ins", "Firestore database with OAuth grants; no Splitwise data", database(d.auth_database)],
    ["Logs", "What the connector has been doing", cloud("logs/query")],
    ["Usage and billing", "What this project costs on the Blaze plan", `${firebase}/usage`],
  ];
  el("resources").replaceChildren(...rows.map(([title, detail, href]) => {
    const row = document.createElement("li");
    const link = external(href, "");
    const strong = document.createElement("strong");
    strong.textContent = title;
    const span = document.createElement("span");
    span.textContent = detail;
    link.append(strong, span);
    row.append(link);
    return row;
  }));
  el("project").href = `${firebase}/overview`;
}

// ---------- Developer ----------

let previewTarget = "get_status";

async function preview(target) {
  previewTarget = target;
  for (const button of document.querySelectorAll("[data-preview]"))
    button.setAttribute("aria-pressed", String(button.dataset.preview === target));
  const output = el("preview-output");
  output.textContent = "Loading…";
  try {
    output.textContent = JSON.stringify(await api("/owner/preview", {target}), null, 2);
  } catch (error) {
    if (error instanceof OwnerSessionExpired) return expireSession();
    output.textContent = "The preview couldn't be loaded.";
  }
}

for (const button of document.querySelectorAll("[data-preview]"))
  button.onclick = () => preview(button.dataset.preview);

// ---------- Sign-in ----------

async function signedIn(value) {
  user = value;
  el("identity").textContent = user.email;
  showSignedIn(true);
  try {
    await loadStatus();
  } catch (error) {
    // The first request is also where a non-owner Google account is turned away.
    if (error instanceof OwnerSessionExpired)
      return expireSession("Sign in with the Google account that owns this deployment.");
    say("message", "Your connector's status couldn't be loaded. Try reloading.", "danger");
  }
}

async function expireSession(text = "Your sign-in expired. Sign in again to continue.") {
  await signOut(auth);
  say("signin-message", text);
}

function signedOut() {
  user = status = null;
  keyFormOpen = false;
  el("key-panel").classList.add("hidden");
  el("project").classList.add("hidden");
  showSignedIn(false);
  el("signin").disabled = false;
}

try {
  const config = await fetch("/firebase-config").then(response => {
    if (!response.ok) throw new Error();
    return response.json();
  });
  auth = getAuth(initializeApp(config));
  // Session persistence: survives a reload of this tab, cleared when the tab closes.
  await setPersistence(auth, browserSessionPersistence);
  onAuthStateChanged(auth, value => {
    if (value) signedIn(value);
    else {
      signedOut();
      if (el("signin-message").textContent === "Loading…") say("signin-message", "");
    }
  });
} catch {
  say("signin-message", "Sign-in is unavailable. Check this deployment's Firebase setup.", "danger");
}

el("signin").onclick = async () => {
  el("signin").disabled = true;
  say("signin-message", "Opening Google sign-in…");
  try {
    const provider = new GoogleAuthProvider();
    provider.setCustomParameters({prompt: "select_account"});
    await signInWithPopup(auth, provider);
    say("signin-message", "");
  } catch {
    say("signin-message", "Sign-in wasn't completed.");
    el("signin").disabled = false;
  }
};

el("signout").onclick = () => signOut(auth).then(() => say("signin-message", "Signed out."));
window.addEventListener("hashchange", () => { if (user) showView(); });
