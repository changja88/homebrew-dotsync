export const initialState = Object.freeze({
  surface: "manager",
  destination: "overview",
  providers: {},
  accounts: [],
  sync: null,
  jobs: {},
  modal: null,
  error: null,
});

const SYNC_DIRECTIONS = new Set(["backup", "apply"]);
const MANAGER_DESTINATIONS = new Set(["overview", "accounts", "sync", "settings"]);

export const MANAGER_SYNC_HANDOFF_EVENT = "dotsync:manager-sync-preview";


export function reduce(state, event) {
  switch (event.type) {
    case "BOOTSTRAP_LOADED":
      return { ...state, providers: event.providers, error: null };
    case "ACCOUNTS_LOADED":
      return { ...state, accounts: [...event.accounts], error: null };
    case "SYNC_LOADED":
      return { ...state, sync: event.sync, error: null };
    case "JOB_UPDATED":
      return {
        ...state,
        jobs: { ...state.jobs, [event.job.id]: event.job },
      };
    case "NAVIGATED":
      return { ...state, destination: event.destination, modal: null };
    case "ERROR_RAISED":
      return { ...state, error: event.error };
    default:
      return state;
  }
}


function frozenPreview(payload, direction, apps) {
  const plans = Array.isArray(payload?.preview?.plans)
    ? payload.preview.plans.map((plan) => Object.freeze({
      app: plan.app,
      changes: Object.freeze(
        Array.isArray(plan.changes)
          ? plan.changes.map((change) => Object.freeze({ ...change }))
          : [],
      ),
    }))
    : [];
  return Object.freeze({
    direction,
    apps,
    digest: payload?.preview?.digest,
    plans: Object.freeze(plans),
  });
}


export function createSyncPreviewCoordinator({
  requestPreview,
  publishPreview,
  failPreview,
}) {
  let generation = 0;

  return Object.freeze({
    invalidate() {
      generation += 1;
    },
    async request(direction, selectedApps) {
      const requestGeneration = ++generation;
      const apps = Object.freeze([...selectedApps]);
      try {
        const payload = await requestPreview(direction, apps);
        if (generation !== requestGeneration) {
          return null;
        }
        const preview = frozenPreview(payload, direction, apps);
        publishPreview(preview);
        return preview;
      } catch (error) {
        if (generation === requestGeneration) {
          failPreview(error);
        }
        return null;
      }
    },
  });
}


export function createNativeMessage(action, destination = null, direction = null) {
  if (action === "open_manager") {
    if (!MANAGER_DESTINATIONS.has(destination)) {
      return null;
    }
    if (destination === "sync") {
      return SYNC_DIRECTIONS.has(direction)
        ? Object.freeze({ action, destination, direction })
        : null;
    }
    return direction === null ? Object.freeze({ action, destination }) : null;
  }
  if (destination !== null || direction !== null) {
    return null;
  }
  if (action === "refresh_summary" || action === "quit_app") {
    return Object.freeze({ action });
  }
  return null;
}


function managerSyncHandoffDirection(event) {
  const detail = event?.detail;
  if (
    detail === null
    || typeof detail !== "object"
    || Array.isArray(detail)
    || Object.keys(detail).length !== 1
    || !SYNC_DIRECTIONS.has(detail.direction)
  ) {
    return null;
  }
  return detail.direction;
}


export function createManagerSyncHandoffHandler(openSyncPreview) {
  return async (event) => {
    const direction = managerSyncHandoffDirection(event);
    if (direction === null) {
      return false;
    }
    await openSyncPreview(direction);
    return true;
  };
}


function terminalJob(job) {
  return job.state === "succeeded" || job.state === "failed";
}


export function createJobPoller({
  loadJob,
  updateJob,
  finishJob,
  failJob,
  setTimer = globalThis.setTimeout,
  clearTimer = globalThis.clearTimeout,
  now = () => performance.now(),
}) {
  const entries = new Map();
  let hiddenSince = null;
  let stopped = false;

  function schedule(entry, delay) {
    if (stopped || entry.timer !== null || !entries.has(entry.jobId)) {
      return;
    }
    entry.timer = setTimer(async () => {
      entry.timer = null;
      await poll(entry);
    }, delay);
  }

  async function poll(entry) {
    if (stopped || entries.get(entry.jobId) !== entry) {
      return;
    }
    if (hiddenSince !== null && now() - hiddenSince >= 30_000) {
      entry.suspended = true;
      return;
    }
    try {
      const payload = await loadJob(entry.jobId);
      if (stopped || entries.get(entry.jobId) !== entry) {
        return;
      }
      updateJob(payload.job);
      if (terminalJob(payload.job)) {
        entries.delete(entry.jobId);
        await finishJob(payload.job);
        return;
      }
    } catch (error) {
      if (stopped || entries.get(entry.jobId) !== entry) {
        return;
      }
      entries.delete(entry.jobId);
      failJob(error, entry.accountId);
      return;
    }
    entry.delay = Math.min(2000, Math.ceil(entry.delay * 1.5));
    schedule(entry, entry.delay);
  }

  return Object.freeze({
    start(jobId, accountId = null) {
      if (stopped || entries.has(jobId)) {
        return;
      }
      const entry = {
        jobId,
        accountId,
        delay: 500,
        timer: null,
        suspended: false,
      };
      entries.set(jobId, entry);
      schedule(entry, entry.delay);
    },
    setHidden(hidden) {
      if (hidden) {
        if (hiddenSince === null) {
          hiddenSince = now();
        }
        return;
      }
      hiddenSince = null;
      for (const entry of entries.values()) {
        if (entry.suspended) {
          entry.suspended = false;
          schedule(entry, 0);
        }
      }
    },
    stop() {
      stopped = true;
      for (const entry of entries.values()) {
        if (entry.timer !== null) {
          clearTimer(entry.timer);
        }
      }
      entries.clear();
    },
  });
}
