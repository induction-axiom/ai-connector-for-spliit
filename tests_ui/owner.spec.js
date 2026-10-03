// Synthetic data only: the page is served from firebase/mcp/static, with Google sign-in
// and every /owner API replaced, so no deployment or account is touched.
import {readFileSync} from "node:fs";
import {test, expect} from "@playwright/test";

const STATIC = new URL("../firebase/mcp/static/", import.meta.url);
const BASE = "https://owner.test";
const KEY = "k".repeat(40);

const FIREBASE_APP = "export const initializeApp = config => ({config});";
const FIREBASE_AUTH = `
  export const getAuth = app => ({app});
  export class GoogleAuthProvider { setCustomParameters() {} }
  export const browserSessionPersistence = "session";
  export const setPersistence = async () => {};
  export const signInWithPopup = async () => {};
  export const signOut = async () => {};
  export const onAuthStateChanged = (auth, callback) => setTimeout(() => callback(
    {email: "owner@example.com", getIdToken: async () => "synthetic-owner-token"}));
`;

function status(state, apps = []) {
  const connected = state === "connected";
  return {
    splitwise: {state, checked_at: 1790000000, latency_ms: 120,
      key_saved_at: state === "key_missing" ? null : 1790000000,
      user: connected ? {id: 1, first_name: "Sam", last_name: null, email: "sam@example.com"} : undefined},
    health: {last_ok_at: connected ? 1790000000 : null, last_error_code: null, last_error_at: null},
    apps,
    diagnostics: {connector_version: "test", repository: "example/example", project_id: "your-project",
      api_key_secret: "splitwise-api-key", auth_database: "(default)", mcp_endpoint: BASE + "/mcp"},
  };
}

// Serve the dashboard; /owner/key waits until the test settles it.
async function openDashboard(page, initial = "key_missing") {
  const state = {splitwise: initial, keys: [], removed: 0, errors: []};
  page.on("pageerror", error => state.errors.push(error.message));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    const json = body => route.fulfill({contentType: "application/json", body: JSON.stringify(body)});
    if (url.hostname === "www.gstatic.com")
      return route.fulfill({contentType: "text/javascript",
        body: url.pathname.endsWith("firebase-auth.js") ? FIREBASE_AUTH : FIREBASE_APP});
    if (url.origin !== BASE) return route.fulfill({status: 404, body: ""});
    if (url.pathname === "/owner")
      return route.fulfill({contentType: "text/html", body: readFileSync(new URL("owner.html", STATIC))});
    if (url.pathname.startsWith("/assets/")) {
      const name = url.pathname.slice("/assets/".length);
      return route.fulfill({contentType: name.endsWith(".js") ? "text/javascript" : "text/css",
        body: readFileSync(new URL(name, STATIC))});
    }
    if (url.pathname === "/firebase-config") return json({authDomain: "example.test"});
    if (url.pathname === "/owner/status") return json(status(state.splitwise));
    if (url.pathname === "/owner/key") {
      const {api_key} = route.request().postDataJSON();
      return new Promise(settle => state.keys.push({api_key, reply: result => {
        if (result === "key_saved") state.splitwise = "connected";
        json({result});
        settle();
      }}));
    }
    if (url.pathname === "/owner/key/remove") {
      state.removed += 1;
      state.splitwise = "key_missing";
      return json({result: "key_removed"});
    }
    return json({});
  });
  await page.goto(BASE + "/owner");
  return state;
}

async function nextKey(state) {
  await expect.poll(() => state.keys.length).toBeGreaterThan(0);
  return state.keys.shift();
}

test("a new key is checked, saved, and the page turns to connecting an AI app", async ({page}) => {
  const state = await openDashboard(page);
  await expect(page.locator("#status-title")).toHaveText("Add your Splitwise API key.");
  await expect(page.locator("#primary-action")).toBeHidden();
  await expect(page.locator("#key-cancel")).toBeHidden();
  await page.fill("#api-key", KEY);
  await page.click("#key-submit");

  const pending = await nextKey(state);
  expect(pending.api_key).toBe(KEY);
  await expect(page.locator("#key-submit")).toBeDisabled();
  await expect(page.locator("#message")).toHaveText("Checking the key with Splitwise…");
  pending.reply("key_saved");

  await expect(page.locator("#message")).toHaveText("Key saved. Your AI apps can use Splitwise now.");
  await expect(page.locator("#status-title")).toHaveText("Connect an AI app.");
  await expect(page.locator("#key-panel")).toBeHidden();
  await expect(page.locator("#fact-account")).toHaveText("Sam (sam@example.com)");
  await expect(page.locator("#api-key")).toHaveValue("");
  expect(state.errors).toEqual([]);
});

test("a rejected key keeps the form open and says why", async ({page}) => {
  const state = await openDashboard(page, "key_rejected");
  await expect(page.locator("#status-title")).toHaveText("Replace your Splitwise API key.");
  await page.fill("#api-key", "w".repeat(40));
  await page.click("#key-submit");
  (await nextKey(state)).reply("key_rejected");
  await expect(page.locator("#message")).toHaveText(
    "Splitwise didn't accept that key. Create a new one and try again.");
  await expect(page.locator("#key-panel")).toBeVisible();
  await expect(page.locator("#key-submit")).toBeEnabled();
  expect(state.errors).toEqual([]);
});

test("removing the key takes two clicks", async ({page}) => {
  const state = await openDashboard(page, "connected");
  await expect(page.locator("#key-panel")).toBeHidden();
  await page.click("#key-remove");
  await expect(page.locator("#key-remove")).toHaveText("Click again to remove");
  expect(state.removed).toBe(0);
  await page.click("#key-remove");
  await expect(page.locator("#status-title")).toHaveText("Add your Splitwise API key.");
  expect(state.removed).toBe(1);
  expect(state.errors).toEqual([]);
});

test("replace key opens the form, and cancel closes it", async ({page}) => {
  const state = await openDashboard(page, "connected");
  await page.click("#key-replace");
  await expect(page.locator("#key-panel")).toBeVisible();
  await expect(page.locator("#key-title")).toHaveText("Replace your Splitwise API key");
  await page.click("#key-cancel");
  await expect(page.locator("#key-panel")).toBeHidden();
  expect(state.errors).toEqual([]);
});

test("the ChatGPT guide links to Plugins and offers the address to paste", async ({page}) => {
  const state = await openDashboard(page, "connected");
  await expect(page.locator("#mcp-url")).toHaveText(BASE + "/mcp");
  await page.evaluate(() => document.querySelector('[data-guide="chatgpt"]').click());
  const guide = page.locator("#guide");
  await expect(guide).toBeVisible();
  await expect(guide).toContainText("Name it Splitwise");
  await expect(guide).toContainText(BASE + "/mcp");
  const links = await guide.locator("a.step-link").evaluateAll(
    anchors => anchors.map(a => [a.textContent, a.href, a.target]));
  expect(links).toContainEqual(["Open ChatGPT Plugins", "https://chatgpt.com/plugins", "_blank"]);
  expect(state.errors).toEqual([]);
});
