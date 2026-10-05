// README artwork: node tests_ui/screenshots.mjs
// Uses the real dashboard with synthetic data and intercepted requests only.
// The conversation illustrations are examples, not screenshots of any AI app.
import {readFileSync, mkdirSync} from "node:fs";
import {chromium, expect} from "@playwright/test";

const STATIC = new URL("../firebase/mcp/static/", import.meta.url);
const OUTPUT = new URL("../docs/images/", import.meta.url);
// Upstream iPhone 14 Pro frame, pinned with its MIT license in templates/.
const DEVICE_CSS = readFileSync(new URL("templates/iphone-14-pro.css", import.meta.url), "utf8");
const BASE = "https://owner.example";
// Every image is this many CSS pixels wide, so the README shows them at one width and text size.
const WIDTH = 1040;
const NOW = new Date("2026-10-04T18:00:00Z");
const seconds = NOW.getTime() / 1000;
const apps = [
  {client_id: "demo-claude", name: "Claude", redirect_host: "claude.ai",
    connected_at: seconds - 3 * 86400, last_used_at: seconds - 120},
  {client_id: "demo-chatgpt", name: "ChatGPT", redirect_host: "chatgpt.com",
    connected_at: seconds - 2 * 86400, last_used_at: seconds - 1800},
];
const dinner = {title: "Dinner", amount: "84.60", date: "2026-10-03", paid_by: "Jamie",
  split_mode: "EVENLY", paid_for: ["Jamie", "Alex", "Sam"].map(name => ({name, share: null})),
  category: "Dining", notes: null};
const ramen = {...dinner, title: "Ramen", amount: "27.09", date: "2026-10-04",
  paid_for: dinner.paid_for.slice(0, 2),
  original: {amount: "3000", currency: "JPY", rate: "0.00903"}};
const status = {
  spliit: {state: "connected", groups: [
    {name: "Trip", state: "ok", currency: "CAD", members: ["Jamie", "Alex", "Sam"], me: "Jamie"},
    {name: "Apartment", state: "ok", currency: "CAD", members: ["Jamie", "Alex"], me: "Jamie"},
  ]},
  last_failure: null, apps,
  changes: [
    {at: seconds - 120, app: apps[0], tool: "record_reimbursement", group: "Trip", before: null,
      after: {...dinner, title: "Reimbursement", amount: "15.00", date: "2026-10-04",
        paid_by: "Alex", paid_for: [{name: "Jamie", share: null}], is_reimbursement: true}},
    {at: seconds - 1800, app: apps[1], tool: "update_expense", group: "Trip", before: dinner,
      after: {...dinner, amount: "90.00"}},
    {at: seconds - 3600, app: apps[0], tool: "create_expense", group: "Trip", before: null, after: ramen},
    {at: seconds - 86400, app: apps[1], tool: "create_expense", group: "Trip", before: null, after: dinner},
  ],
  diagnostics: {connector_version: "demo", repository: "induction-axiom/ai-connector-for-spliit",
    project_id: "my-spliit-ai", groups_secret: "spliit-groups", auth_database: "(default)",
    mcp_endpoint: "https://spliit-demo.us-east4.example/mcp"},
};
const FIREBASE_APP = "export const initializeApp = config => ({config});";
const FIREBASE_AUTH = `
  export const getAuth = app => ({app});
  export class GoogleAuthProvider { setCustomParameters() {} }
  export const browserSessionPersistence = "session";
  export const setPersistence = async () => {};
  export const signInWithPopup = async () => {};
  export const signOut = async () => {};
  export const onAuthStateChanged = (auth, callback) => setTimeout(() => callback(
    {email: "you@example.com", getIdToken: async () => "demo-token"}));
`;

async function dashboard(page) {
  await page.clock.install({time: NOW});
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname === "www.gstatic.com") {
      if (!["firebase-app.js", "firebase-auth.js"].some(name => url.pathname.endsWith(name)))
        throw new Error("Unexpected Firebase module");
      return route.fulfill({contentType: "text/javascript",
        body: url.pathname.endsWith("firebase-auth.js") ? FIREBASE_AUTH : FIREBASE_APP});
    }
    if (url.origin !== BASE) throw new Error("Unexpected external request");
    const json = body => route.fulfill({contentType: "application/json", body: JSON.stringify(body)});
    if (url.pathname === "/owner")
      return route.fulfill({contentType: "text/html", body: readFileSync(new URL("owner.html", STATIC))});
    if (["/assets/style.css", "/assets/owner.js"].includes(url.pathname))
      return route.fulfill({contentType: url.pathname.endsWith(".js") ? "text/javascript" : "text/css",
        body: readFileSync(new URL(url.pathname.slice(8), STATIC))});
    if (url.pathname === "/firebase-config") return json({authDomain: "auth.example"});
    if (url.pathname === "/owner/status") return json(status);
    throw new Error("Unexpected owner request");
  });
  await page.goto(BASE + "/owner");
  await expect(page.locator("#status-title")).toHaveText("Claude and ChatGPT can use your Spliit groups.");
  await expect(page.locator("#changes-list li")).toHaveCount(4);
  await page.clock.pauseAt(NOW.getTime() + 1000);
  await page.evaluate(() => document.fonts.ready);
  return errors;
}

const conversationCSS = `
  :root { color-scheme: light; --bg:#f5f5f7; --surface:#fff; --ink:#1d1d1f;
    --muted:#6e6e73; --line:#e8e8ed; --blue:#0071e3; --green:#1d8a3f; }
  @media(prefers-color-scheme:dark) { :root { color-scheme:dark; --bg:#000;
    --surface:#1c1c1e; --ink:#f5f5f7; --muted:#a1a1a6; --line:#333336;
    --blue:#0071e3; --green:#30d158; } }
  * {box-sizing:border-box} body {margin:0; background:var(--bg); color:var(--ink);
    font:21px/1.48 -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;
    -webkit-font-smoothing:antialiased} main {padding:38px; width:${WIDTH}px}
  header {display:flex; align-items:center; justify-content:space-between; gap:18px; margin:0 6px 26px}
  header strong {font-size:22px; letter-spacing:-.025em} .sample {font-size:16px;
    background:var(--line); color:var(--muted); padding:5px 14px; border-radius:24px; white-space:nowrap}
  .user {max-width:760px; margin:0 0 22px auto; padding:20px 24px; border-radius:28px 28px 8px 28px;
    background:var(--blue); color:#fff} .answer {padding:28px 32px; margin-bottom:24px;
    background:var(--surface); border-radius:28px} p {margin:0 0 18px} p:last-child {margin:0}
  .tool {display:inline-block; font:15px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;
    color:var(--muted); background:var(--line); padding:5px 10px; border-radius:9px; margin-bottom:18px}
  h1 {font-size:34px; letter-spacing:-.035em; line-height:1.16; margin:0 0 20px}
  .success {color:var(--green)} .muted {color:var(--muted)}
  dl {margin:0 0 18px} dl div {display:flex; justify-content:space-between; padding:12px 0;
    border-top:1px solid var(--line); gap:24px} dt {color:var(--muted)} dd {margin:0; font-weight:600}
  .small {font-size:17px} .balance {width:100%; border-collapse:collapse; margin:8px 0 24px}
  .balance td {padding:10px 0; border-top:1px solid var(--line)} .balance td:last-child {text-align:right; font-weight:600}
  .transfers {margin:0 0 18px; padding:0; list-style:none} .transfers li {display:flex;
    justify-content:space-between; padding:12px 0; border-top:1px solid var(--line)}
  footer {margin:4px 6px 0; color:var(--muted); font-size:15px; line-height:1.5}
  .mobile-example {padding:32px}
  .mobile-example footer {text-align:center; font-size:14px}
  .mobile-layout {display:grid; grid-template-columns:420px 428px; gap:64px; align-items:center;
    justify-content:center; margin:26px 0 28px}
  .intro {text-align:left} .intro h1 {font-size:54px; line-height:1.05; margin:0}
  .mobile-layout .device {filter:drop-shadow(0 18px 24px #0002)}
  .device .phone {display:flex; flex-direction:column; background:var(--bg); overflow:hidden}
  .phone-status {display:flex; align-items:center; justify-content:space-between; padding:17px 24px 8px;
    min-height:54px; font-size:14px; font-weight:600}
  .battery {width:24px; height:12px; border:1.5px solid var(--ink); border-radius:3px; padding:2px}
  .battery::after {content:""; display:block; background:var(--ink); height:100%; border-radius:1px}
  .phone-app {text-align:center; border-bottom:1px solid var(--line); padding:8px 18px 14px}
  .phone-app strong {font-size:18px} .phone-app span {display:block; font-size:13px; color:var(--muted); margin-top:3px}
  .phone-chat {padding:16px 16px 4px; font-size:17px; line-height:1.45}
  .chat-date {text-align:center; font-size:12px; color:var(--muted); margin-bottom:16px}
  .phone .user {margin-left:14px; padding:16px; margin-bottom:18px; border-radius:22px 22px 6px 22px}
  .voice-label {display:flex; align-items:center; gap:7px; margin-bottom:12px; font-size:12px; font-weight:600}
  .mic {width:18px; height:24px; flex:none} .wave {display:flex; align-items:center; gap:2px; height:24px; margin-left:auto}
  .wave i {display:block; width:3px; border-radius:4px; background:currentColor}
  .phone .answer {padding:18px; border-radius:22px; margin-bottom:16px}
  .saved {display:flex; align-items:center; gap:7px; font-size:13px; font-weight:600; color:var(--green); margin-bottom:12px}
  .saved::before {content:""; width:7px; height:7px; border-radius:50%; background:currentColor}
  .phone .answer h1 {font-size:26px; margin-bottom:16px} .phone dl {font-size:15px; margin-bottom:16px}
  .phone dl div {display:flex; padding:10px 0; gap:14px} .phone .small {font-size:13px}
  .composer {display:flex; align-items:center; gap:12px; margin:auto 16px 18px; padding:10px 12px;
    border:1px solid var(--line); border-radius:28px; background:var(--surface)}
  .composer span {flex:1; font-size:14px; color:var(--muted)} .mic-button {display:flex; align-items:center;
    justify-content:center; width:38px; height:38px; border-radius:50%; background:var(--blue); color:#fff}
  .home-indicator {width:134px; height:5px; margin:0 auto 10px; border-radius:8px; background:var(--ink)}
`;
const microphone = `<svg class="mic" viewBox="0 0 24 28" fill="none" stroke="currentColor" stroke-width="1.8"
  stroke-linecap="round" aria-hidden="true"><rect x="8" y="2" width="8" height="15" rx="4"/>
  <path d="M4 12v2a8 8 0 0 0 16 0v-2M12 22v4M8 26h8"/></svg>`;
const voiceLabel = `<div class="voice-label">${microphone}<span>Voice input · transcribed</span>
  <span class="wave" aria-hidden="true">${[8, 16, 22, 12, 18, 24, 10, 18, 12].map(height => `<i style="height:${height}px"></i>`).join("")}</span></div>`;
const examples = {
  "example-record": `
    <header><strong>AI connector for Spliit</strong><span class="sample">Simulated example</span></header>
    <div class="mobile-layout">
      <div class="intro"><h1>Just paid?<br>Tell your AI.</h1></div>
      <div class="device device-iphone-14-pro device-black">
      <div class="device-frame"><div class="device-screen phone">
      <div class="phone-status"><span>18:00</span><span class="battery"></span></div>
      <div class="phone-app"><strong>Your AI app</strong><span>Connected to Spliit</span></div>
      <div class="phone-chat">
        <div class="chat-date">Sunday, October 4, 2026</div>
        <div class="user">${voiceLabel}Dinner was eighty-four sixty Canadian.
          Add it to Trip for today, split with Alex and Sam.</div>
        <div class="answer">
          <div class="saved">Saved in Spliit · Trip</div>
          <h1>Dinner added.</h1>
          <dl><div><dt>Paid by you</dt><dd>CAD 84.60</dd></div>
            <div><dt>Split evenly</dt><dd>CAD 28.20 each</dd></div></dl>
          <p>Shared with Alex and Sam.</p>
          <p class="muted small">Everyone can see it in Spliit.</p>
        </div>
      </div>
      <div class="composer"><span>Add another expense…</span><div class="mic-button">${microphone}</div></div>
      <div class="home-indicator"></div>
      </div></div>
      <div class="device-stripe"></div><div class="device-header"></div>
      <div class="device-sensors"></div><div class="device-btns"></div>
      <div class="device-power"></div><div class="device-home"></div>
      </div>
    </div>
    <footer>Illustration with made-up data. Voice input uses your AI app or phone keyboard; the connector records the expense in Spliit.</footer>`,
  "example-settle": `
    <header><strong>Who owes whom? Just ask.</strong><span class="sample">Simulated example</span></header>
    <div class="user">Who owes whom in Trip, and how do we settle up?</div>
    <div class="answer">
      <div class="tool">get_balances · Trip · CAD</div>
      <h1>Two payments settle the group.</h1>
      <table class="balance"><tbody>
        <tr><td>Jamie is owed</td><td class="success">CAD 180.00</td></tr>
        <tr><td>Alex owes</td><td>CAD 75.00</td></tr>
        <tr><td>Sam owes</td><td>CAD 105.00</td></tr>
      </tbody></table>
      <p><strong>Spliit’s suggested payments</strong></p>
      <ul class="transfers"><li><span>Alex → Jamie</span><strong>CAD 75.00</strong></li>
        <li><span>Sam → Jamie</span><strong>CAD 105.00</strong></li></ul>
      <p class="muted small">These are amounts to pay each other, not money transfers made by the connector.</p>
    </div>
    <div class="user">Alex paid me back CAD 15 on Oct 4, 2026. Record it.</div>
    <div class="answer">
      <div class="tool">record_reimbursement · Trip</div>
      <h1 class="success">Repayment recorded.</h1>
      <p>Alex paid Jamie <strong>CAD 15.00</strong>. Alex now owes CAD 60.00; Sam still owes CAD 105.00.</p>
    </div>
    <footer>Illustration with made-up names and balances. This is a separate scenario from the expense example above.</footer>`,
};

mkdirSync(OUTPUT, {recursive: true});
const browser = await chromium.launch();
try {
  for (const colorScheme of ["light", "dark"]) {
    const context = await browser.newContext({viewport: {width: WIDTH, height: 1100},
      deviceScaleFactor: 1.5, colorScheme, locale: "en-CA", timezoneId: "America/Toronto",
      reducedMotion: "reduce", serviceWorkers: "block"});
    const page = await context.newPage();
    const errors = await dashboard(page);
    const save = (name, options = {}) => page.screenshot({path: new URL(`${name}-${colorScheme}.png`, OUTPUT).pathname,
      animations: "disabled", ...options});

    const groups = await page.locator("#groups-list").locator("..").boundingBox();
    await save("overview", {clip: {x: 0, y: 0, width: WIDTH, height: Math.ceil(groups.y + groups.height + 24)}});

    // Crop the actual change log, including its explanatory heading and note.
    const changes = page.locator("#changes-list").locator("..");
    await changes.scrollIntoViewIfNeeded();
    const changesBox = await changes.boundingBox();
    const headingBox = await changes.locator("xpath=preceding-sibling::div[1]").boundingBox();
    await save("ai-changes", {clip: {x: 0, y: headingBox.y - 18, width: WIDTH,
      height: Math.ceil(changesBox.y + changesBox.height + 24 - (headingBox.y - 18))}});

    await page.locator('[data-guide="chatgpt"]').click();
    await expect(page.locator("#guide")).toBeVisible();
    await page.clock.runFor(500);
    const guideBox = await page.locator("#guide").boundingBox();
    await save("connect-chatgpt", {clip: {x: 0, y: guideBox.y - 48,
      width: WIDTH, height: guideBox.height + 96}});
    if (errors.length) throw new Error(errors.join("\n"));

    for (const [name, body] of Object.entries(examples)) {
      const mobile = name === "example-record";
      await page.setViewportSize({width: WIDTH, height: 1100});
      await page.setContent(`<!doctype html><html lang="en"><meta charset="utf-8">
        <title>Spliit connector — simulated example</title><style>${mobile ? DEVICE_CSS : ""}\n${conversationCSS}</style><main class="${mobile ? "mobile-example" : ""}">${body}</main></html>`);
      await page.evaluate(() => document.fonts.ready);
      if (mobile) {
        const layout = await page.locator(".phone").evaluate(phone => ({
          fits: phone.scrollHeight <= phone.clientHeight,
          chatBottom: phone.querySelector(".phone-chat").getBoundingClientRect().bottom,
          composerTop: phone.querySelector(".composer").getBoundingClientRect().top,
        }));
        if (!layout.fits || layout.chatBottom > layout.composerTop)
          throw new Error("The mobile conversation overflows the phone frame: " + JSON.stringify(layout));
      }
      await save(name, {fullPage: true});
    }
    await context.close();
    console.log(`Generated 5 ${colorScheme} screenshots with synthetic data.`);
  }
} finally {
  await browser.close();
}
