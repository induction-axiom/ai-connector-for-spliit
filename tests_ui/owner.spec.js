// Synthetic data only: the page is served from firebase/mcp/static, with Google sign-in
// and every /owner API replaced, so no deployment or account is touched.
import {readFileSync} from "node:fs";
import {test, expect} from "@playwright/test";

const STATIC = new URL("../firebase/mcp/static/", import.meta.url);
const BASE = "https://owner.test";
const LINK = "https://spliit.app/groups/secretGroupId123456789";

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

const TRIP = {name: "Trip", state: "ok", currency: "CAD", members: ["Jong", "Alex", "Sam"], me: "Jong"};
const CLAUDE = {client_id: "c1", name: "Claude", redirect_host: "claude.ai"};

function status(state) {
  return {
    spliit: {state: state.spliit, groups: state.groups},
    last_failure: state.lastFailure,
    changes: state.changes.slice(0, 10),
    more_changes: state.changes.length > 10,
    apps: [],
    diagnostics: {connector_version: "test", repository: "example/example", project_id: "your-project",
      groups_secret: "spliit-groups", auth_database: "(default)", mcp_endpoint: BASE + "/mcp"},
  };
}

// Serve the dashboard; each /owner/groups/add waits until the test settles it.
async function openDashboard(page, {groups = [], changes = [], spliit = "connected", lastFailure = null} = {}) {
  const state = {groups, changes, spliit, lastFailure, adds: [], removed: [], errors: []};
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
    if (url.pathname === "/owner/status") return json(status(state));
    if (url.pathname === "/owner/changes") {
      const older = state.changes.filter(c => c.at < route.request().postDataJSON().before);
      return json({changes: older.slice(0, 10), more_changes: older.length > 10});
    }
    if (url.pathname === "/owner/groups/lookup") {
      const {link} = route.request().postDataJSON();
      return json(link === LINK
        ? {result: "found", name: "Trip", members: [{id: "p1", name: "Jong"}, {id: "p2", name: "Alex"}]}
        : {result: "link_invalid"});
    }
    if (url.pathname === "/owner/groups/add") {
      const body = route.request().postDataJSON();
      return new Promise(settle => state.adds.push({body, reply: result => {
        if (result === "group_added") state.groups = [TRIP];
        json({result});
        settle();
      }}));
    }
    if (url.pathname === "/owner/groups/remove") {
      state.removed.push(route.request().postDataJSON().name);
      state.groups = [];
      return json({result: "group_removed"});
    }
    return json({});
  });
  await page.goto(BASE + "/owner");
  return state;
}

test("a group is added from its link, choosing which member you are", async ({page}) => {
  const state = await openDashboard(page);
  await expect(page.locator("#status-title")).toHaveText("Add a Spliit group.");
  await expect(page.locator("#group-cancel")).toBeHidden();
  await page.fill("#group-link", "https://example.com/not-a-group");
  await page.click("#lookup-submit");
  await expect(page.locator("#message")).toHaveText(
    "That isn't a Spliit group link. It looks like https://spliit.app/groups/…");

  await page.fill("#group-link", LINK);
  await page.click("#lookup-submit");
  await expect(page.locator("#members-title")).toHaveText("Which member of Trip are you?");
  await page.getByLabel("Jong").check();
  await page.click("#add-submit");
  await expect.poll(() => state.adds.length).toBe(1);
  expect(state.adds[0].body).toEqual({link: LINK, me: "p1"});
  await expect(page.locator("#add-submit")).toBeDisabled();
  state.adds[0].reply("group_added");

  await expect(page.locator("#message")).toHaveText("Group added. Your AI apps can use it now.");
  await expect(page.locator("#status-title")).toHaveText("Connect an AI app.");
  await expect(page.locator("#group-panel")).toBeHidden();
  await expect(page.locator("#groups-list")).toContainText("You are Jong · CAD · 3 members");
  expect(state.errors).toEqual([]);
});

test("removing a group takes two clicks", async ({page}) => {
  const state = await openDashboard(page, {groups: [TRIP]});
  const remove = page.locator("#groups-list button");
  await remove.click();
  await expect(remove).toHaveText("Confirm remove");
  expect(state.removed).toEqual([]);
  await remove.click();
  await expect(page.locator("#status-title")).toHaveText("Add a Spliit group.");
  expect(state.removed).toEqual(["Trip"]);
  expect(state.errors).toEqual([]);
});

test("Spliit's status shows only when something is wrong", async ({page}) => {
  const now = Date.now() / 1000;
  const old = {code: "spliit_unavailable", at: now - 8 * 86400};
  const state = await openDashboard(page, {groups: [TRIP], lastFailure: old});
  await expect(page.locator("#groups-list")).toContainText("Trip");
  await expect(page.locator("#spliit-facts")).toBeHidden();

  state.lastFailure = {code: "spliit_unavailable", at: now - 3600};
  await page.reload();
  await expect(page.locator("#fact-last-error")).toHaveText("Spliit isn't answering, 1 hour ago");
  await expect(page.locator("#fact-live-row")).toBeHidden();

  state.spliit = "spliit_unavailable";
  await page.reload();
  await expect(page.locator("#fact-live")).toHaveText("Spliit isn't answering");
  expect(state.errors).toEqual([]);
});

test("AI changes say what each app added, changed and deleted", async ({page}) => {
  const dinner = {title: "Dinner", amount: "45.00", date: "2026-10-01", paid_by: "Jong", split_mode: "EVENLY",
    paid_for: [{name: "Jong", share: null}, {name: "Alex", share: null}], category: "Dining", notes: null};
  const payback = {...dinner, title: "Reimbursement", amount: "15.00", paid_by: "Alex", is_reimbursement: true};
  const state = await openDashboard(page, {groups: [TRIP], changes: [
    {at: Date.now() / 1000, app: CLAUDE, tool: "delete_expense", group: "Trip", before: payback, after: null},
    {at: Date.now() / 1000, app: CLAUDE, tool: "update_expense", group: "Trip",
      before: dinner, after: {...dinner, amount: "50.00"}},
    {at: Date.now() / 1000, app: CLAUDE, tool: "create_expense", group: "Trip", before: null, after: dinner},
  ]});
  const rows = page.locator("#changes-list li");
  await expect(rows.nth(0)).toContainText("Claude deleted a reimbursement");
  await expect(rows.nth(0)).toContainText("15.00 · paid by Alex · Trip");
  await expect(rows.nth(1)).toContainText("Claude changed Dinner");
  await expect(rows.nth(1)).toContainText("amount 45.00 → 50.00 · Trip");
  await expect(rows.nth(2)).toContainText("Claude added Dinner");
  await expect(rows.nth(2)).toContainText("45.00 · paid by Jong · Trip");
  expect(state.errors).toEqual([]);
});

test("AI changes show an amount paid in another currency", async ({page}) => {
  const ramen = {title: "Ramen", amount: "27.09", date: "2026-10-02", paid_by: "Jong", split_mode: "EVENLY",
    paid_for: [{name: "Jong", share: null}], category: "Dining", notes: null,
    original: {amount: "3000", currency: "JPY", rate: "0.00903"}};
  const state = await openDashboard(page, {groups: [TRIP], changes: [
    {at: Date.now() / 1000, app: CLAUDE, tool: "update_expense", group: "Trip", before: ramen,
      after: {...ramen, amount: "27.40", original: {...ramen.original, rate: "0.00913333"}}},
    {at: Date.now() / 1000, app: CLAUDE, tool: "create_expense", group: "Trip", before: null, after: ramen},
  ]});
  const rows = page.locator("#changes-list li");
  await expect(rows.nth(0)).toContainText("amount 27.09 → 27.40 · Trip");
  await expect(rows.nth(0)).not.toContainText("paid in");
  await expect(rows.nth(1)).toContainText("27.09 (3000 JPY) · paid by Jong · Trip");
  expect(state.errors).toEqual([]);
});

test("AI changes show ten at a time, then older ones on request", async ({page}) => {
  const expense = {title: "Coffee", amount: "4.00", paid_by: "Jong"};
  const changes = Array.from({length: 13}, (_, i) => ({at: 1000 - i, app: CLAUDE, tool: "create_expense",
    group: "Trip", before: null, after: {...expense, title: `Coffee ${i + 1}`}}));
  const state = await openDashboard(page, {groups: [TRIP], changes});
  const rows = page.locator("#changes-list li");
  await expect(rows).toHaveCount(10);
  await expect(rows.nth(9)).toContainText("Claude added Coffee 10");
  await page.locator("#changes-more").click();
  await expect(rows).toHaveCount(13);
  await expect(rows.nth(12)).toContainText("Claude added Coffee 13");
  await expect(page.locator("#changes-more")).toBeHidden();
  expect(state.errors).toEqual([]);
});

test("the ChatGPT guide links to Plugins and offers the address to paste", async ({page}) => {
  const state = await openDashboard(page, {groups: [TRIP]});
  await expect(page.locator("#mcp-url")).toHaveText(BASE + "/mcp");
  await page.evaluate(() => document.querySelector('[data-guide="chatgpt"]').click());
  const guide = page.locator("#guide");
  await expect(guide).toBeVisible();
  await expect(guide).toContainText("Name it Spliit");
  await expect(guide).toContainText(BASE + "/mcp");
  const links = await guide.locator("a.step-link").evaluateAll(
    anchors => anchors.map(a => [a.textContent, a.href, a.target]));
  expect(links).toContainEqual(["Open ChatGPT Plugins", "https://chatgpt.com/plugins", "_blank"]);
  expect(state.errors).toEqual([]);
});
