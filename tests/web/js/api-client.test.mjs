import test from "node:test";
import assert from "node:assert/strict";

import {
  createApiClient,
  readLaunchContext,
} from "../../../lib/dotsync/web/static/api-client.mjs";


const TOKEN = "A".repeat(43);
const JOB_ID = "f0784490-417e-4d03-bd86-289445bb8a91";


function response(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return payload;
    },
  };
}


function recordingFetch(responses = [response(200, { ok: true })]) {
  const calls = [];
  const fetchImpl = async (path, options) => {
    calls.push({ path, options });
    const next = responses.shift();
    assert.notEqual(next, undefined, "test supplied a response for every request");
    return next;
  };
  return { calls, fetchImpl };
}


test("launch context is captured once and the complete query is erased first", async () => {
  const events = [];
  const location = {
    pathname: "/",
    search: `?token=${TOKEN}&surface=manager&destination=accounts`,
  };
  const history = {
    replaceState(state, title, url) {
      events.push(["history", state, title, url]);
    },
  };
  const fetchImpl = async (path, options) => {
    events.push(["fetch", path, options.headers["X-DotSync-Token"]]);
    return response(200, { providers: {} });
  };

  const context = readLaunchContext(location, history);
  location.search = `?token=${"B".repeat(43)}&surface=popover&destination=sync`;
  await createApiClient(context.token, fetchImpl).bootstrap();

  assert.deepEqual(context, {
    token: TOKEN,
    surface: "manager",
    destination: "accounts",
  });
  assert.equal(Object.isFrozen(context), true);
  assert.deepEqual(events, [
    ["history", null, "", "/"],
    ["fetch", "/api/bootstrap", TOKEN],
  ]);
});


test("missing duplicate extra and invalid launch values fail closed", () => {
  const invalidQueries = [
    `?surface=manager&destination=overview`,
    `?token=${TOKEN}&token=${TOKEN}&surface=manager&destination=overview`,
    `?token=${TOKEN}&surface=manager&destination=overview&debug=1`,
    `?token=short&surface=manager&destination=overview`,
    `?token=${TOKEN}&surface=sidebar&destination=overview`,
    `?token=${TOKEN}&surface=manager&destination=dashboard`,
  ];

  for (const search of invalidQueries) {
    const history = {
      replaceState() {
        throw new Error("history must not change for invalid launch context");
      },
    };
    assert.throws(
      () => readLaunchContext({ pathname: "/", search }, history),
      (error) => error instanceof Error && error.message === "invalid_launch",
    );
  }
});


test("every request uses the capability header and disables browser caches", async () => {
  const recorder = recordingFetch([
    response(200, { providers: {} }),
    response(200, { accounts: [] }),
  ]);
  const api = createApiClient(TOKEN, recorder.fetchImpl);

  await api.bootstrap();
  await api.accounts();

  for (const call of recorder.calls) {
    assert.equal(call.options.cache, "no-store");
    assert.equal(call.options.credentials, "omit");
    assert.equal(call.options.headers["Content-Type"], "application/json");
    assert.equal(call.options.headers["X-DotSync-Token"], TOKEN);
  }
});


test("fixed methods own every request method path and JSON key", async () => {
  const recorder = recordingFetch([
    response(200, {}), response(200, {}), response(200, {}),
    response(201, {}), response(200, {}), response(202, { job_id: JOB_ID }),
    response(202, { job_id: JOB_ID }), response(202, { job_id: JOB_ID }),
    response(202, { job_id: JOB_ID }), response(200, {}), response(200, {}),
    response(200, {}), response(200, {}), response(202, { job_id: JOB_ID }),
    response(200, {}), response(200, {}), response(200, {}),
  ]);
  const api = createApiClient(TOKEN, recorder.fetchImpl);
  const accountId = "64007b14-23c0-409d-a1d6-5fc7756d5783";

  await api.bootstrap();
  await api.menuSummary();
  await api.accounts();
  await api.createCodex("개인");
  await api.rename(accountId, "업무");
  await api.login(accountId);
  await api.refresh(accountId);
  await api.logout(accountId);
  await api.remove(accountId, "logout_and_delete");
  await api.job(JOB_ID);
  await api.syncStatus();
  await api.syncApps(["ghostty"]);
  await api.syncPreview("backup", ["ghostty"]);
  await api.syncExecute("f".repeat(64));
  await api.selectSyncFolder();
  await api.revealAppData();
  await api.heartbeat();

  assert.deepEqual(
    recorder.calls.map(({ path, options }) => [
      options.method,
      path,
      options.body === undefined ? undefined : JSON.parse(options.body),
    ]),
    [
      ["GET", "/api/bootstrap", undefined],
      ["GET", "/api/menu-summary", undefined],
      ["GET", "/api/accounts", undefined],
      ["POST", "/api/accounts", { provider: "codex", label: "개인" }],
      ["PATCH", `/api/accounts/${accountId}`, { label: "업무" }],
      ["POST", `/api/accounts/${accountId}/login`, { provider: "codex" }],
      ["POST", `/api/accounts/${accountId}/refresh`, { provider: "codex" }],
      ["POST", `/api/accounts/${accountId}/logout`, { provider: "codex" }],
      ["DELETE", `/api/accounts/${accountId}`, { provider: "codex", action: "logout_and_delete" }],
      ["GET", `/api/jobs/${JOB_ID}`, undefined],
      ["GET", "/api/sync/status", undefined],
      ["PATCH", "/api/sync/apps", { apps: ["ghostty"] }],
      ["POST", "/api/sync/preview", { direction: "backup", apps: ["ghostty"] }],
      ["POST", "/api/sync/execute", { digest: "f".repeat(64) }],
      ["POST", "/api/settings/sync-folder/select", {}],
      ["POST", "/api/settings/app-data/reveal", {}],
      ["POST", "/api/heartbeat", {}],
    ],
  );
});


test("API failures never copy the token or provider payload into errors", async () => {
  const providerSecret = "provider-private-payload";
  const recorder = recordingFetch([
    response(500, {
      error: {
        code: providerSecret,
        message: `${providerSecret}:${TOKEN}`,
      },
    }),
  ]);

  await assert.rejects(
    createApiClient(TOKEN, recorder.fetchImpl).bootstrap(),
    (error) => {
      assert.equal(error instanceof Error, true);
      assert.equal(error.message.includes(TOKEN), false);
      assert.equal(error.message.includes(providerSecret), false);
      assert.equal(String(error).includes(TOKEN), false);
      return true;
    },
  );
});


test("accepted operations expose only canonical job IDs to the poller", async () => {
  const valid = recordingFetch([response(202, { job_id: JOB_ID })]);
  const invalid = recordingFetch([
    response(202, { job_id: "not-a-job", provider: "private" }),
  ]);

  assert.equal(await createApiClient(TOKEN, valid.fetchImpl).login(JOB_ID), JOB_ID);
  await assert.rejects(
    createApiClient(TOKEN, invalid.fetchImpl).login(JOB_ID),
    (error) => error instanceof Error && error.message === "invalid_response",
  );
});
