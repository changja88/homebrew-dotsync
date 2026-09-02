import { createApiClient, readLaunchContext } from "./api-client.mjs";
import {
  MANAGER_SYNC_HANDOFF_EVENT,
  createJobPoller,
  createManagerSyncHandoffHandler,
  createSyncPreviewCoordinator,
  initialState,
  reduce,
} from "./state.mjs";
import {
  confirmationDialog,
  errorCopy,
  renderManager,
  showFatal,
  showStatus,
  syncExecutionConfirmation,
} from "./render.mjs";
let launch;
try {
  launch = readLaunchContext(window.location, window.history);
} catch {
  showFatal("invalid_launch");
}


if (launch) {
  const api = createApiClient(launch.token);
  let state = { ...initialState, destination: launch.destination };
  let preview = null;
  let activeDirection = "backup";
  let selectedApps = new Set();
  let lastSyncMessage = "";
  const accountErrors = {};
  const provisionalAccounts = new Map();
  let stopped = false;

  const manager = document.querySelector('[data-surface="manager"]');
  const managerContent = document.querySelector("#manager-content");

  function model() {
    return {
      ...state,
      preview,
      activeDirection,
      selectedApps,
      lastSyncMessage,
      accountErrors,
    };
  }

  function render() {
    renderManager(managerContent, model());
  }

  function revealManager() {
    manager.hidden = false;
    document.body.setAttribute("data-active-surface", "manager");
    render();
  }

  function safeCode(error) {
    return typeof error?.code === "string" ? error.code : "unknown_error";
  }

  function raise(error, accountId = null) {
    const code = safeCode(error);
    if (accountId) {
      accountErrors[accountId] = code;
    }
    state = reduce(state, { type: "ERROR_RAISED", error: code });
    render();
    showStatus(errorCopy(code));
  }

  const previewCoordinator = createSyncPreviewCoordinator({
    requestPreview: (direction, apps) => api.syncPreview(direction, apps),
    publishPreview(value) {
      preview = value;
      render();
    },
    failPreview: raise,
  });

  const jobPoller = createJobPoller({
    loadJob: (jobId) => api.job(jobId),
    updateJob(job) {
      state = reduce(state, { type: "JOB_UPDATED", job });
      render();
    },
    finishJob,
    failJob: raise,
    setTimer: (callback, delay) => window.setTimeout(callback, delay),
    clearTimer: (timer) => window.clearTimeout(timer),
    now: () => performance.now(),
  });

  function clearPreview() {
    previewCoordinator.invalidate();
    preview = null;
  }

  async function loadBootstrap() {
    try {
      const payload = await api.bootstrap();
      state = reduce(state, { type: "BOOTSTRAP_LOADED", providers: payload.providers });
      render();
      return payload;
    } catch (error) {
      raise(error);
      return null;
    }
  }

  async function loadAccounts() {
    try {
      const payload = await api.accounts();
      state = reduce(state, { type: "ACCOUNTS_LOADED", accounts: payload.accounts });
      render();
    } catch (error) {
      raise(error);
    }
  }

  async function loadSyncStatus() {
    try {
      const payload = await api.syncStatus();
      const sync = { ...payload.sync, configured: true };
      state = reduce(state, { type: "SYNC_LOADED", sync });
      selectedApps = new Set(sync.apps.map((app) => app.name));
      clearPreview();
      render();
    } catch (error) {
      if (safeCode(error) === "sync_not_configured") {
        state = reduce(state, {
          type: "SYNC_LOADED",
          sync: { configured: false, apps: [] },
        });
        selectedApps = new Set();
        clearPreview();
        render();
        return;
      }
      raise(error);
    }
  }

  async function navigate(destination, { focus = true } = {}) {
    state = reduce(state, { type: "NAVIGATED", destination });
    revealManager();
    if (destination === "sync" && state.sync === null) {
      await loadSyncStatus();
    }
    if (focus) {
      managerContent.focus({ preventScroll: true });
    }
  }

  async function routePreview(direction) {
    if (direction !== "backup" && direction !== "apply") {
      return;
    }
    activeDirection = direction;
    clearPreview();
    await navigate("sync");
    if (state.sync?.configured && selectedApps.size > 0) {
      await createSyncPreview();
    }
  }

  async function finishJob(job) {
    if (job.account_id) {
      const provisionalProvider = provisionalAccounts.get(job.account_id);
      const account = state.accounts.find((item) => item.id === job.account_id);
      const provider = provisionalProvider ?? account?.provider;

      if (provisionalProvider && job.kind === "account_login") {
        if (job.state === "succeeded") {
          provisionalAccounts.delete(job.account_id);
          delete accountErrors[job.account_id];
          await loadAccounts();
        } else {
          showStatus(errorCopy(job.error_code));
          const cleanedUp = await removeLocalAccount(job.account_id, provisionalProvider);
          if (!cleanedUp) {
            provisionalAccounts.delete(job.account_id);
            await loadAccounts();
          }
        }
        return;
      }

      if (provisionalProvider && job.kind === "account_delete_force_local") {
        provisionalAccounts.delete(job.account_id);
        if (job.state === "failed") {
          accountErrors[job.account_id] = job.error_code || "unknown_error";
        } else {
          delete accountErrors[job.account_id];
        }
        await loadAccounts();
        return;
      }

      if (job.kind === "account_delete" && job.state === "failed" && provider) {
        await removeLocalAccount(job.account_id, provider);
        return;
      }

      if (job.state === "failed") {
        accountErrors[job.account_id] = job.error_code || "unknown_error";
      } else {
        delete accountErrors[job.account_id];
      }
      await loadAccounts();
      return;
    }

    if (job.state === "succeeded") {
      const direction = job.result?.direction;
      lastSyncMessage = direction === "apply"
        ? "Apply 완료 · 로컬 원본은 selected sync folder/.backups에 백업했습니다."
        : "Backup 완료 · 선택한 동기화 폴더를 업데이트했습니다.";
    } else {
      lastSyncMessage = errorCopy(job.error_code);
    }
    clearPreview();
    await loadSyncStatus();
  }

  async function startJob(submit, accountId = null) {
    try {
      const jobId = await submit();
      const job = {
        id: jobId,
        account_id: accountId,
        kind: accountId ? "account_pending" : "sync_execute",
        state: "queued",
      };
      state = reduce(state, { type: "JOB_UPDATED", job });
      render();
      jobPoller.start(jobId, accountId);
      return true;
    } catch (error) {
      raise(error, accountId);
      return false;
    }
  }

  async function removeLocalAccount(accountId, provider) {
    try {
      await api.remove(accountId, provider, "remove_local_profile_anyway");
      provisionalAccounts.delete(accountId);
      delete accountErrors[accountId];
      await loadAccounts();
      return true;
    } catch (error) {
      raise(error, accountId);
      return false;
    }
  }

  async function addAccount(provider) {
    const providerName = provider === "claude" ? "Claude" : "Codex";
    const isolatedHome = provider === "claude" ? "CLAUDE_CONFIG_DIR" : "CODEX_HOME";
    const labels = new Set(
      state.accounts
        .filter((account) => account.provider === provider)
        .map((account) => account.label),
    );
    const baseLabel = `새 ${providerName} 계정`;
    let suggestedLabel = baseLabel;
    let suffix = 2;
    while (labels.has(suggestedLabel)) {
      suggestedLabel = `${baseLabel} ${suffix}`;
      suffix += 1;
    }
    const result = await confirmationDialog({
      title: `${providerName} 계정 추가`,
      copy: `기존 ~/.${provider}를 가져오지 않고 DotSync 전용 ${isolatedHome}에서 공식 로그인을 시작합니다.`,
      confirmText: "공식 로그인 계속",
      inputLabel: "계정 이름",
      inputValue: suggestedLabel,
    });
    if (!result.confirmed) {
      return;
    }
    try {
      const payload = await api.createAccount(provider, result.value);
      provisionalAccounts.set(payload.account.id, provider);
      const started = await startJob(
        () => api.login(payload.account.id, provider),
        payload.account.id,
      );
      if (!started) {
        const cleanedUp = await removeLocalAccount(payload.account.id, provider);
        if (!cleanedUp) {
          provisionalAccounts.delete(payload.account.id);
          await loadAccounts();
        }
      }
    } catch (error) {
      raise(error);
    }
  }

  async function renameAccount(accountId) {
    const account = state.accounts.find((item) => item.id === accountId);
    if (!account) {
      return;
    }
    const result = await confirmationDialog({
      title: "계정 이름 변경",
      copy: "격리 프로필의 표시 이름만 바꿉니다.",
      confirmText: "이름 변경",
      inputLabel: "계정 이름",
      inputValue: account.label,
    });
    if (!result.confirmed) {
      return;
    }
    try {
      await api.rename(accountId, result.value);
      await loadAccounts();
    } catch (error) {
      raise(error, accountId);
    }
  }

  async function confirmAccountJob(accountId, kind) {
    const account = state.accounts.find((item) => item.id === accountId);
    if (!account) {
      return;
    }
    const providerName = account.provider === "claude" ? "Claude" : "Codex";
    const details = {
      logout: {
        title: "로그아웃할까요?",
        copy: `${account.label}의 격리된 ${providerName} 세션만 로그아웃합니다.`,
        confirmText: "로그아웃",
      },
      delete: {
        title: "계정을 삭제할까요?",
        copy: "DotSync가 만든 전용 프로필과 캐시만 제거합니다. ~/.claude와 ~/.codex는 변경하지 않습니다.",
        confirmText: "계정 삭제",
      },
    }[kind];
    const result = await confirmationDialog({ ...details, danger: true });
    if (!result.confirmed) {
      return;
    }
    if (kind === "logout") {
      await startJob(() => api.logout(accountId, account.provider), accountId);
    } else {
      await removeLocalAccount(accountId, account.provider);
    }
  }

  async function refreshAll() {
    await Promise.all(state.accounts.map((account) => startJob(
      () => api.refresh(account.id, account.provider),
      account.id,
    )));
  }

  async function saveSyncApps() {
    try {
      await api.syncApps([...selectedApps]);
      await loadSyncStatus();
      showStatus("추적 앱 선택을 저장했습니다.");
    } catch (error) {
      raise(error);
    }
  }

  async function createSyncPreview() {
    await previewCoordinator.request(activeDirection, selectedApps);
  }

  async function executeSync() {
    if (!preview) {
      return;
    }
    const syncPreview = preview;
    const result = await confirmationDialog(syncExecutionConfirmation(syncPreview));
    if (!result.confirmed || preview !== syncPreview) {
      return;
    }
    const digest = syncPreview.digest;
    clearPreview();
    render();
    await startJob(() => api.syncExecute(digest));
  }

  async function selectSyncFolder() {
    try {
      const result = await api.selectSyncFolder();
      if (result.selected) {
        await loadBootstrap();
        await loadSyncStatus();
        showStatus("동기화 폴더를 선택했습니다.");
      }
    } catch (error) {
      raise(error);
    }
  }

  async function handleAction(target) {
    const action = target.dataset.action;
    const accountId = target.dataset.accountId;
    if (action === "navigate") {
      await navigate(target.dataset.destination);
    } else if (action === "refresh-all") {
      await refreshAll();
    } else if (action === "refresh-account") {
      const account = state.accounts.find((item) => item.id === accountId);
      if (account) {
        await startJob(() => api.refresh(accountId, account.provider), accountId);
      }
    } else if (action === "add-claude") {
      await addAccount("claude");
    } else if (action === "add-codex") {
      await addAccount("codex");
    } else if (action === "rename-account") {
      await renameAccount(accountId);
    } else if (action === "login-account") {
      const account = state.accounts.find((item) => item.id === accountId);
      if (account) {
        await startJob(() => api.login(accountId, account.provider), accountId);
      }
    } else if (action === "logout-account") {
      await confirmAccountJob(accountId, "logout");
    } else if (action === "delete-account") {
      await confirmAccountJob(accountId, "delete");
    } else if (action === "preview-direction") {
      const direction = target.dataset.direction;
      if (direction === "backup" || direction === "apply") {
        activeDirection = direction;
        clearPreview();
        render();
      }
    } else if (action === "save-sync-apps") {
      await saveSyncApps();
    } else if (action === "create-sync-preview") {
      await createSyncPreview();
    } else if (action === "execute-sync") {
      await executeSync();
    } else if (action === "load-sync-status") {
      await loadSyncStatus();
    } else if (action === "select-sync-folder") {
      await selectSyncFolder();
    } else if (action === "reveal-app-data") {
      try {
        await api.revealAppData();
        showStatus("Finder에서 DotSync 앱 데이터 위치를 열었습니다.");
      } catch (error) {
        raise(error);
      }
    }
  }

  document.addEventListener("click", async (event) => {
    const previewTarget = event.target.closest("[data-preview-direction]");
    if (previewTarget) {
      await routePreview(previewTarget.dataset.previewDirection);
      return;
    }

    const destinationTarget = event.target.closest("nav [data-destination]");
    if (destinationTarget) {
      await navigate(destinationTarget.dataset.destination);
      return;
    }

    const actionTarget = event.target.closest("[data-action]");
    if (actionTarget) {
      await handleAction(actionTarget);
    }
  });

  document.addEventListener("change", (event) => {
    const target = event.target.closest('[data-action="toggle-sync-app"]');
    if (!target) {
      return;
    }
    if (target.checked) {
      selectedApps.add(target.dataset.app);
    } else {
      selectedApps.delete(target.dataset.app);
    }
    clearPreview();
    render();
  });

  document.addEventListener("visibilitychange", () => {
    jobPoller.setHidden(document.hidden);
  });

  const receiveManagerSyncHandoff = createManagerSyncHandoffHandler(routePreview);
  let activeManagerSyncReceipt = null;
  window.addEventListener(MANAGER_SYNC_HANDOFF_EVENT, (event) => {
    const accepted = receiveManagerSyncHandoff(event) === true;
    if (activeManagerSyncReceipt !== null) {
      activeManagerSyncReceipt.accepted = accepted;
    }
  });
  Object.defineProperty(window, "__dotsyncReceiveManagerSyncHandoff", {
    configurable: false,
    enumerable: false,
    writable: false,
    value(direction) {
      if (
        activeManagerSyncReceipt !== null
        || (direction !== "backup" && direction !== "apply")
      ) {
        return false;
      }
      const receipt = { accepted: false };
      activeManagerSyncReceipt = receipt;
      try {
        window.dispatchEvent(new CustomEvent(
          MANAGER_SYNC_HANDOFF_EVENT,
          { detail: { direction } },
        ));
        return receipt.accepted === true;
      } finally {
        activeManagerSyncReceipt = null;
      }
    },
  });

  window.addEventListener("beforeunload", () => {
    stopped = true;
    jobPoller.stop();
  });

  jobPoller.setHidden(document.hidden);

  revealManager();
  await Promise.allSettled([loadBootstrap(), loadAccounts(), loadSyncStatus()]);
  window.setInterval(() => {
    if (!stopped && !document.hidden) {
      api.heartbeat().catch(() => {});
    }
  }, 5 * 60 * 1000);
}
