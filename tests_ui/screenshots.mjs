// README artwork: node tests_ui/screenshots.mjs
// Uses the real dashboard with synthetic data and intercepted requests only.
// The example conversations are illustrations, not screenshots of any AI app.
import {readFileSync, mkdirSync} from "node:fs";
import {chromium, expect} from "@playwright/test";

const STATIC = new URL("../firebase/mcp/static/", import.meta.url);
const OUTPUT = new URL("../docs/images/", import.meta.url);
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

// The pitch: one line on the left, one question and one answer on the right, read in a glance.
const promoCSS = `
  :root { color-scheme: light; --bg:#f5f5f7; --surface:#fff; --ink:#1d1d1f;
    --muted:#6e6e73; --line:#e8e8ed; --blue:#0071e3; }
  @media(prefers-color-scheme:dark) { :root { color-scheme:dark; --bg:#000;
    --surface:#1c1c1e; --ink:#f5f5f7; --muted:#a1a1a6; --line:#333336; --blue:#0a84ff; } }
  * {box-sizing:border-box} body {margin:0; background:var(--bg); color:var(--ink);
    font:17px/1.4 -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif; -webkit-font-smoothing:antialiased}
  main {position:relative; width:${WIDTH}px; padding:64px 64px 72px; display:grid;
    grid-template-columns:1fr 420px; gap:56px; align-items:center}
  .product {margin:0 0 14px; color:var(--muted); font-size:16px; font-weight:600}
  h1 {margin:0; font-size:54px; line-height:1.04; letter-spacing:-.04em; font-weight:700}
  .user {width:fit-content; max-width:340px; margin:0 0 14px auto; padding:13px 18px;
    border-radius:22px 22px 6px 22px; background:var(--blue); color:#fff; font-size:18px}
  .answer {padding:26px 28px; border-radius:24px; background:var(--surface)}
  .figure {font-size:46px; line-height:1; font-weight:700; letter-spacing:-.035em; font-variant-numeric:tabular-nums}
  .caption {margin-top:8px; color:var(--muted); font-size:17px}
  .sample {position:absolute; right:64px; bottom:28px; margin:0; color:var(--muted); font-size:13px}
`;
// This connector's tiny visuals: a line saying where the expense went, and the payments that settle a group.
const visualCSS = `
  :root { --green:#1d8a3f; } @media(prefers-color-scheme:dark) { :root { --green:#30d158; } }
  .saved {display:flex; align-items:center; gap:7px; margin-bottom:16px; color:var(--green); font-size:14px; font-weight:600}
  .saved::before {content:""; width:7px; height:7px; border-radius:50%; background:currentColor}
  .payments {margin:20px 0 0; padding:0; list-style:none}
  .payments li {display:flex; justify-content:space-between; padding:12px 0; border-top:1px solid var(--line); font-size:17px}
  .payments li:last-child {padding-bottom:0}
  .payments strong {font-variant-numeric:tabular-nums}
`;
const examples = {
  "example-record": `
    <section><p class="product">AI connector for Spliit</p>
      <h1>Just paid?<br>Tell your AI.</h1></section>
    <section>
      <div class="user">Dinner was $84.60. Split it with Alex and Sam.</div>
      <div class="answer">
        <div class="saved">Added to Spliit · Trip</div>
        <div class="figure">$28.20 each</div>
        <div class="caption">Dinner, $84.60, split three ways</div>
      </div>
    </section>
    <p class="sample">Simulated example · made-up data</p>`,
  "example-settle": `
    <section><p class="product">AI connector for Spliit</p>
      <h1>Who owes whom?<br>Just ask.</h1></section>
    <section>
      <div class="user">How do we settle up Trip?</div>
      <div class="answer">
        <div class="figure">2 payments</div>
        <ul class="payments"><li><span>Alex → Jamie</span><strong>$75</strong></li>
          <li><span>Sam → Jamie</span><strong>$105</strong></li></ul>
      </div>
    </section>
    <p class="sample">Simulated example · made-up data</p>`,
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

    // The AI apps panel with ChatGPT's steps open, from its heading down.
    await page.locator('[data-connect="chatgpt"]').click();
    // Taller than the window, so measured in page coordinates and captured from the full page.
    const [top, bottom] = await page.locator("#apps").evaluate(head => [
      head.getBoundingClientRect().top + scrollY - 18,
      head.nextElementSibling.getBoundingClientRect().bottom + scrollY + 24]);
    // The sticky nav would otherwise be drawn over the middle of a full-page capture.
    await save("connect-chatgpt", {fullPage: true, clip: {x: 0, y: top, width: WIDTH, height: Math.ceil(bottom - top)},
      style: ".nav { position: static !important; }"});
    if (errors.length) throw new Error(errors.join("\n"));

    for (const [name, body] of Object.entries(examples)) {
      // A short viewport, so the full-page capture ends where the content does.
      await page.setViewportSize({width: WIDTH, height: 400});
      await page.setContent(`<!doctype html><html lang="en"><meta charset="utf-8">
        <title>Spliit connector — simulated example</title><style>${promoCSS}\n${visualCSS}</style><main>${body}</main></html>`);
      await page.evaluate(() => document.fonts.ready);
      await save(name, {fullPage: true});
    }
    await context.close();
    console.log(`Generated 5 ${colorScheme} screenshots with synthetic data.`);
  }
} finally {
  await browser.close();
}
