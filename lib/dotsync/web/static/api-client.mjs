const SURFACES = new Set(["popover", "manager"]);
const DESTINATIONS = new Set(["overview", "accounts", "sync", "settings"]);
const ACCOUNT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const JOB_ID = ACCOUNT_ID;
const SAFE_ERROR_CODES = new Set([
  "account_conflict",
  "forbidden",
  "internal_error",
  "invalid_request",
  "invalid_sync_folder",
  "method_not_allowed",
  "not_found",
  "provider_policy_disabled",
  "service_unavailable",
  "stale_sync_plan",
  "sync_not_configured",
]);


export function readLaunchContext(location, history) {
  const search = location.search;
  history.replaceState(null, "", location.pathname || "/");
  const values = new URLSearchParams(search);
  const keys = [...values.keys()];
  const allowed = new Set(["token", "surface", "destination"]);
  if (keys.some((key) => !allowed.has(key))) {
    throw new Error("invalid_launch");
  }
  for (const key of allowed) {
    if (values.getAll(key).length !== 1) {
      throw new Error("invalid_launch");
    }
  }
  const token = values.get("token");
  const surface = values.get("surface");
  const destination = values.get("destination");
  if (
    !/^[A-Za-z0-9_-]{43}$/.test(token ?? "")
    || !SURFACES.has(surface)
    || !DESTINATIONS.has(destination)
  ) {
    throw new Error("invalid_launch");
  }
  return Object.freeze({ token, surface, destination });
}


function safeApiError(status, payload) {
  const error = new Error("api_request_failed");
  const code = payload?.error?.code;
  Object.defineProperties(error, {
    code: {
      value: SAFE_ERROR_CODES.has(code) ? code : "unknown_error",
      enumerable: true,
    },
    status: {
      value: Number.isInteger(status) ? status : 0,
      enumerable: true,
    },
  });
  return error;
}


function invalidResponse() {
  return new Error("invalid_response");
}


function fixedAccountId(value) {
  if (!ACCOUNT_ID.test(value)) {
    throw new Error("invalid_account");
  }
  return value;
}


function fixedJobId(value) {
  if (!JOB_ID.test(value)) {
    throw new Error("invalid_job");
  }
  return value;
}


export function createApiClient(token, fetchImpl = globalThis.fetch) {
  if (!/^[A-Za-z0-9_-]{43}$/.test(token ?? "")) {
    throw new Error("invalid_capability");
  }

  const request = async (method, path, body) => {
    let response;
    try {
      response = await fetchImpl(path, {
        method,
        cache: "no-store",
        credentials: "omit",
        headers: {
          "Content-Type": "application/json",
          "X-DotSync-Token": token,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new Error("network_error");
    }

    let payload;
    try {
      payload = await response.json();
    } catch {
      throw invalidResponse();
    }
    if (!response.ok) {
      throw safeApiError(response.status, payload);
    }
    if (response.status === 202) {
      if (
        payload === null
        || typeof payload !== "object"
        || Array.isArray(payload)
        || Object.keys(payload).length !== 1
        || !JOB_ID.test(payload.job_id ?? "")
      ) {
        throw invalidResponse();
      }
      return payload.job_id;
    }
    return payload;
  };

  return Object.freeze({
    bootstrap: () => request("GET", "/api/bootstrap"),
    menuSummary: () => request("GET", "/api/menu-summary"),
    accounts: () => request("GET", "/api/accounts"),
    createCodex: (label) => request("POST", "/api/accounts", { provider: "codex", label }),
    rename: (id, label) => request("PATCH", `/api/accounts/${fixedAccountId(id)}`, { label }),
    login: (id) => request("POST", `/api/accounts/${fixedAccountId(id)}/login`, { provider: "codex" }),
    refresh: (id) => request("POST", `/api/accounts/${fixedAccountId(id)}/refresh`, { provider: "codex" }),
    logout: (id) => request("POST", `/api/accounts/${fixedAccountId(id)}/logout`, { provider: "codex" }),
    remove: (id, action) => request("DELETE", `/api/accounts/${fixedAccountId(id)}`, { provider: "codex", action }),
    job: (id) => request("GET", `/api/jobs/${fixedJobId(id)}`),
    syncStatus: () => request("GET", "/api/sync/status"),
    syncApps: (apps) => request("PATCH", "/api/sync/apps", { apps }),
    syncPreview: (direction, apps) => request("POST", "/api/sync/preview", { direction, apps }),
    syncExecute: (digest) => {
      if (!DIGEST.test(digest ?? "")) {
        throw new Error("invalid_digest");
      }
      return request("POST", "/api/sync/execute", { digest });
    },
    selectSyncFolder: () => request("POST", "/api/settings/sync-folder/select", {}),
    revealAppData: () => request("POST", "/api/settings/app-data/reveal", {}),
    heartbeat: () => request("POST", "/api/heartbeat", {}),
  });
}
