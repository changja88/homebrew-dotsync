import test from "node:test";
import assert from "node:assert/strict";

import * as orchestration from "../../../lib/dotsync/web/static/state.mjs";


const APPLY_DIGEST = "a".repeat(64);
const BACKUP_DIGEST = "b".repeat(64);


function deferred() {
  let resolve;
  const promise = new Promise((next) => {
    resolve = next;
  });
  return { promise, resolve };
}


class FakeClock {
  constructor() {
    this.now = 0;
    this.nextId = 1;
    this.timers = new Map();
    this.scheduledDelays = [];
  }

  setTimeout = (callback, delay) => {
    const id = this.nextId;
    this.nextId += 1;
    this.scheduledDelays.push(delay);
    this.timers.set(id, { callback, due: this.now + delay });
    return id;
  };

  clearTimeout = (id) => {
    this.timers.delete(id);
  };

  async runNext() {
    const next = [...this.timers.entries()].sort((left, right) => left[1].due - right[1].due)[0];
    assert.notEqual(next, undefined, "a production timer is pending");
    const [id, timer] = next;
    this.timers.delete(id);
    this.now = Math.max(this.now, timer.due);
    await timer.callback();
  }

  nextDue() {
    return Math.min(...[...this.timers.values()].map((timer) => timer.due));
  }
}


test("a delayed Apply preview cannot publish or confirm under Backup semantics", async () => {
  assert.equal(typeof orchestration.createSyncPreviewCoordinator, "function");
  const delayedApply = deferred();
  const published = [];
  const requests = [];
  const coordinator = orchestration.createSyncPreviewCoordinator({
    requestPreview(direction, apps) {
      requests.push({ direction, apps: [...apps] });
      if (direction === "apply") {
        return delayedApply.promise;
      }
      return Promise.resolve({
        preview: { digest: BACKUP_DIGEST, plans: [{ app: "ghostty", changes: [] }] },
      });
    },
    publishPreview(preview) {
      published.push(preview);
    },
    failPreview(error) {
      throw error;
    },
  });

  const applyRequest = coordinator.request("apply", new Set(["zsh"]));
  coordinator.invalidate();
  const backupPreview = await coordinator.request("backup", new Set(["ghostty"]));
  delayedApply.resolve({
    preview: { digest: APPLY_DIGEST, plans: [{ app: "zsh", changes: [] }] },
  });

  assert.equal(await applyRequest, null);
  assert.deepEqual(requests, [
    { direction: "apply", apps: ["zsh"] },
    { direction: "backup", apps: ["ghostty"] },
  ]);
  assert.deepEqual(published, [backupPreview]);
  assert.equal(published[0].direction, "backup");
  assert.deepEqual(published[0].apps, ["ghostty"]);
  assert.equal(published[0].digest, BACKUP_DIGEST);
  assert.equal(JSON.stringify(published).includes(APPLY_DIGEST), false);
  assert.equal(Object.isFrozen(published[0]), true);
  assert.equal(Object.isFrozen(published[0].apps), true);
  assert.equal(Object.isFrozen(published[0].plans), true);
});


test("native Sync manager messages carry only an allowlisted direction", () => {
  assert.equal(typeof orchestration.createNativeMessage, "function");
  assert.deepEqual(
    orchestration.createNativeMessage("open_manager", "sync", "apply"),
    { action: "open_manager", destination: "sync", direction: "apply" },
  );
  assert.deepEqual(
    orchestration.createNativeMessage("open_manager", "accounts"),
    { action: "open_manager", destination: "accounts" },
  );
  assert.deepEqual(
    orchestration.createNativeMessage("open_manager", "overview"),
    { action: "open_manager", destination: "overview" },
  );
  assert.deepEqual(
    orchestration.createNativeMessage("open_manager", "settings"),
    { action: "open_manager", destination: "settings" },
  );
  assert.deepEqual(
    orchestration.createNativeMessage("refresh_summary"),
    { action: "refresh_summary" },
  );
  assert.deepEqual(
    orchestration.createNativeMessage("quit_app"),
    { action: "quit_app" },
  );
  assert.equal(orchestration.createNativeMessage("open_manager", "sync"), null);
  assert.equal(orchestration.createNativeMessage("open_manager", "sync", "restore"), null);
  assert.equal(orchestration.createNativeMessage("open_manager", "accounts", "apply"), null);
  assert.equal(orchestration.createNativeMessage("refresh_summary", "overview"), null);
  assert.equal(orchestration.createNativeMessage("quit_app", null, "apply"), null);
  assert.equal(orchestration.createNativeMessage("open_url", "sync", "apply"), null);
});


test("the fixed manager handoff accepts only backup or apply and issues that preview", async () => {
  assert.equal(typeof orchestration.MANAGER_SYNC_HANDOFF_EVENT, "string");
  assert.equal(typeof orchestration.createManagerSyncHandoffHandler, "function");
  const directions = [];
  const handler = orchestration.createManagerSyncHandoffHandler(async (direction) => {
    directions.push(direction);
  });

  assert.equal(handler({ detail: { direction: "apply" } }), true);
  assert.equal(handler({ detail: { direction: "backup" } }), true);
  assert.equal(handler({ detail: { direction: "restore" } }), false);
  assert.equal(
    handler({ detail: { direction: "apply", path: "/tmp/private" } }),
    false,
  );
  assert.equal(handler({ detail: "apply" }), false);

  assert.deepEqual(directions, ["apply", "backup"]);
});


test("manager handoff rejects an inherited direction and unsafe object shapes", async () => {
  const directions = [];
  const handler = orchestration.createManagerSyncHandoffHandler(async (direction) => {
    directions.push(direction);
  });
  const inheritedDirection = Object.assign(
    Object.create({ direction: "apply" }),
    { path: "/tmp/private" },
  );
  const accessorDirection = {};
  Object.defineProperty(accessorDirection, "direction", {
    enumerable: true,
    get() {
      return "apply";
    },
  });
  const customPrototype = Object.assign(
    Object.create({ inherited: true }),
    { direction: "apply" },
  );
  const nonEnumerableExtra = { direction: "apply" };
  Object.defineProperty(nonEnumerableExtra, "path", {
    enumerable: false,
    value: "/tmp/private",
  });
  const symbolExtra = { direction: "apply", [Symbol("path")]: "/tmp/private" };
  const nullPrototype = Object.assign(Object.create(null), { direction: "apply" });

  await handler({ detail: inheritedDirection });
  await handler({ detail: accessorDirection });
  await handler({ detail: customPrototype });
  await handler({ detail: nonEnumerableExtra });
  await handler({ detail: symbolExtra });
  await handler({ detail: nullPrototype });

  assert.deepEqual(directions, []);
});


test("polling uses one timer with a 500ms-to-2s cadence and stops on terminal state", async () => {
  assert.equal(typeof orchestration.createJobPoller, "function");
  const clock = new FakeClock();
  const states = ["running", "running", "running", "running", "running", "succeeded"];
  const updates = [];
  const finished = [];
  const poller = orchestration.createJobPoller({
    loadJob: async (jobId) => ({ job: { id: jobId, state: states.shift() } }),
    updateJob: (job) => updates.push(job.state),
    finishJob: async (job) => finished.push(job.state),
    failJob: (error) => { throw error; },
    setTimer: clock.setTimeout,
    clearTimer: clock.clearTimeout,
    now: () => clock.now,
  });

  poller.start("job-1");
  assert.equal(clock.timers.size, 1);
  while (finished.length === 0) {
    await clock.runNext();
    assert.ok(clock.timers.size <= 1);
  }

  assert.deepEqual(updates, ["running", "running", "running", "running", "running", "succeeded"]);
  assert.deepEqual(finished, ["succeeded"]);
  assert.deepEqual(clock.scheduledDelays, [500, 750, 1125, 1688, 2000, 2000]);
  assert.equal(clock.timers.size, 0);
});


test("polling clears its single pending timer on unload", async () => {
  assert.equal(typeof orchestration.createJobPoller, "function");
  const clock = new FakeClock();
  let requests = 0;
  const poller = orchestration.createJobPoller({
    loadJob: async () => {
      requests += 1;
      return { job: { id: "job-2", state: "running" } };
    },
    updateJob() {},
    async finishJob() {},
    failJob(error) { throw error; },
    setTimer: clock.setTimeout,
    clearTimer: clock.clearTimeout,
    now: () => clock.now,
  });

  poller.start("job-2");
  assert.equal(clock.timers.size, 1);
  poller.stop();

  assert.equal(clock.timers.size, 0);
  assert.equal(requests, 0);
});


test("polling ignores an in-flight response after unload", async () => {
  assert.equal(typeof orchestration.createJobPoller, "function");
  const clock = new FakeClock();
  const response = deferred();
  const updates = [];
  const poller = orchestration.createJobPoller({
    loadJob: async () => response.promise,
    updateJob: (job) => updates.push(job.state),
    async finishJob() {},
    failJob(error) { throw error; },
    setTimer: clock.setTimeout,
    clearTimer: clock.clearTimeout,
    now: () => clock.now,
  });

  poller.start("job-in-flight");
  const pendingPoll = clock.runNext();
  await Promise.resolve();
  poller.stop();
  response.resolve({ job: { id: "job-in-flight", state: "running" } });
  await pendingPoll;

  assert.deepEqual(updates, []);
  assert.equal(clock.timers.size, 0);
});


test("a job suspended after 30 seconds hidden resumes and reconciles when visible", async () => {
  assert.equal(typeof orchestration.createJobPoller, "function");
  const clock = new FakeClock();
  const updates = [];
  const finished = [];
  const poller = orchestration.createJobPoller({
    loadJob: async (jobId) => ({ job: { id: jobId, state: "succeeded" } }),
    updateJob: (job) => updates.push(job.state),
    finishJob: async (job) => finished.push(job.state),
    failJob(error) { throw error; },
    setTimer: clock.setTimeout,
    clearTimer: clock.clearTimeout,
    now: () => clock.now,
  });

  poller.start("job-3", "account-3");
  poller.setHidden(true);
  clock.now = 30_000;
  await clock.runNext();

  assert.equal(clock.timers.size, 0);
  assert.deepEqual(updates, []);
  assert.deepEqual(finished, []);

  poller.setHidden(false);
  assert.equal(clock.timers.size, 1);
  await clock.runNext();

  assert.deepEqual(updates, ["succeeded"]);
  assert.deepEqual(finished, ["succeeded"]);
  assert.equal(clock.timers.size, 0);
});


test("visible before the pending timer reconciles immediately after 30 seconds hidden", async () => {
  const clock = new FakeClock();
  let visible = false;
  const updates = [];
  const finished = [];
  const poller = orchestration.createJobPoller({
    loadJob: async (jobId) => ({
      job: { id: jobId, state: visible ? "succeeded" : "running" },
    }),
    updateJob: (job) => updates.push(job.state),
    finishJob: async (job) => finished.push(job.state),
    failJob(error) { throw error; },
    setTimer: clock.setTimeout,
    clearTimer: clock.clearTimeout,
    now: () => clock.now,
  });

  poller.start("job-visible-order", "account-visible-order");
  poller.setHidden(true);
  while (clock.nextDue() < 30_000) {
    await clock.runNext();
  }
  assert.ok(clock.nextDue() > 30_000);

  clock.now = 30_000;
  visible = true;
  poller.setHidden(false);

  assert.equal(clock.timers.size, 1);
  assert.equal(clock.nextDue(), 30_000);
  assert.equal(clock.scheduledDelays.at(-1), 0);
  await clock.runNext();
  assert.equal(updates.at(-1), "succeeded");
  assert.deepEqual(finished, ["succeeded"]);
  assert.equal(clock.timers.size, 0);
});
