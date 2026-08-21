import test from "node:test";
import assert from "node:assert/strict";


class FakeElement {
  constructor() {
    this.attributes = new Map();
    this.children = [];
    this.classList = { toggle() {} };
    this.dataset = {};
    this.hidden = false;
    this.textContent = "";
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = [...children];
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  focus() {}
}


class FakeCustomEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.detail = options.detail;
  }
}


function successfulPayload(path) {
  const payloads = {
    "/api/bootstrap": { providers: {}, sync_configured: true },
    "/api/accounts": { accounts: [] },
    "/api/menu-summary": {
      usage: { state: "unknown", highest_percent: null },
      sync: { state: "unknown", attention_count: null },
      observed_at: null,
    },
  };
  assert.ok(Object.hasOwn(payloads, path), `unexpected fixture request: ${path}`);
  return payloads[path];
}


function nativeActionTarget(action, destination) {
  const target = {
    dataset: { nativeAction: action, destination },
  };
  return {
    closest(selector) {
      return selector === "[data-native-action]" ? target : null;
    },
  };
}


test("production app posts only through the exact dotsyncNative bridge", async () => {
  const documentListeners = new Map();
  const windowListeners = new Map();
  const body = new FakeElement();
  const popover = new FakeElement();
  const manager = new FakeElement();
  const popoverContent = new FakeElement();
  const managerContent = new FakeElement();
  const summaryUsage = new FakeElement();
  const summarySync = new FakeElement();
  const summaryUpdated = new FakeElement();
  const fixedElements = new Map([
    ["body", body],
    ['[data-surface="popover"]', popover],
    ['[data-surface="manager"]', manager],
    ["#popover-content", popoverContent],
    ["#manager-content", managerContent],
    ["#summary-usage", summaryUsage],
    ["#summary-sync", summarySync],
    ["#popover-updated", summaryUpdated],
  ]);
  globalThis.document = {
    body,
    hidden: false,
    createElement: () => new FakeElement(),
    createTextNode: (text) => ({ textContent: text }),
    querySelector: (selector) => fixedElements.get(selector) ?? new FakeElement(),
    querySelectorAll: () => [],
    addEventListener(name, listener) {
      documentListeners.set(name, listener);
    },
  };

  const exactMessages = [];
  const aliasMessages = [];
  globalThis.window = {
    location: {
      search: `?token=${"A".repeat(43)}&surface=popover&destination=overview`,
      pathname: "/",
    },
    history: { replaceState() {} },
    webkit: {
      messageHandlers: {
        dotsyncNative: { postMessage: (message) => exactMessages.push(message) },
        dotsync: { postMessage: (message) => aliasMessages.push(message) },
      },
    },
    addEventListener(name, listener) {
      windowListeners.set(name, listener);
    },
    dispatchEvent(event) {
      const listener = windowListeners.get(event.type);
      if (typeof listener === "function") {
        listener(event);
      }
      return true;
    },
    setTimeout,
    clearTimeout,
    setInterval: () => 1,
  };
  globalThis.CustomEvent = FakeCustomEvent;
  globalThis.fetch = async (path) => ({
    ok: true,
    status: 200,
    async json() {
      return successfulPayload(path);
    },
  });

  await import("../../../lib/dotsync/web/static/app.mjs?bridge-contract");
  const click = documentListeners.get("click");
  assert.equal(typeof click, "function");
  assert.deepEqual(exactMessages, []);
  assert.equal(
    typeof window.__dotsyncReceiveManagerSyncHandoff,
    "function",
  );
  assert.deepEqual(
    Object.getOwnPropertyDescriptor(
      window,
      "__dotsyncReceiveManagerSyncHandoff",
    ),
    {
      configurable: false,
      enumerable: false,
      writable: false,
      value: window.__dotsyncReceiveManagerSyncHandoff,
    },
  );
  assert.equal(window.__dotsyncReceiveManagerSyncHandoff("restore"), false);
  assert.equal(window.__dotsyncReceiveManagerSyncHandoff("apply"), true);
  assert.deepEqual(exactMessages, [
    { action: "open_manager", destination: "sync", direction: "apply" },
  ]);

  await click({ target: nativeActionTarget("open_manager", "accounts") });
  assert.deepEqual(exactMessages, [
    { action: "open_manager", destination: "sync", direction: "apply" },
    { action: "open_manager", destination: "accounts" },
  ]);
  assert.deepEqual(aliasMessages, []);

  delete window.webkit.messageHandlers.dotsyncNative;
  await click({ target: nativeActionTarget("open_manager", "settings") });
  assert.deepEqual(exactMessages, [
    { action: "open_manager", destination: "sync", direction: "apply" },
    { action: "open_manager", destination: "accounts" },
  ]);
  assert.deepEqual(aliasMessages, []);
});
