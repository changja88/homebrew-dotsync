import test from "node:test";
import assert from "node:assert/strict";

import { initialState, reduce } from "../../../lib/dotsync/web/static/state.mjs";


test("initial state is immutable and begins on the management overview", () => {
  assert.equal(Object.isFrozen(initialState), true);
  assert.equal(initialState.surface, "manager");
  assert.equal(initialState.destination, "overview");
  assert.deepEqual(initialState.jobs, {});
});


test("one account job update cannot replace another account", () => {
  const start = {
    ...initialState,
    accounts: [
      { id: "a", label: "Personal" },
      { id: "b", label: "Work" },
    ],
    jobs: {},
  };

  const next = reduce(start, {
    type: "JOB_UPDATED",
    job: { id: "j", account_id: "a", state: "running" },
  });

  assert.deepEqual(next.accounts, start.accounts);
  assert.equal(next.jobs.j.account_id, "a");
});


test("loaded account snapshots are copied before entering state", () => {
  const accounts = [{ id: "a", label: "Personal" }];

  const next = reduce(initialState, { type: "ACCOUNTS_LOADED", accounts });

  assert.deepEqual(next.accounts, accounts);
  assert.notEqual(next.accounts, accounts);
  assert.equal(next.error, null);
});


test("navigation closes a pending modal without erasing independent state", () => {
  const start = {
    ...initialState,
    accounts: [{ id: "a", label: "Personal" }],
    sync: { apps: [] },
    modal: { kind: "delete", accountId: "a" },
  };

  const next = reduce(start, { type: "NAVIGATED", destination: "settings" });

  assert.equal(next.destination, "settings");
  assert.equal(next.modal, null);
  assert.deepEqual(next.accounts, start.accounts);
  assert.equal(next.sync, start.sync);
});


test("unknown events preserve object identity", () => {
  assert.equal(reduce(initialState, { type: "UNKNOWN" }), initialState);
});
