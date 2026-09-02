const COPY = Object.freeze({
  genericError: "요청을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  errors: Object.freeze({
    account_conflict: "같은 이름이거나 이미 로그인된 공급자 계정입니다.",
    cli_missing: "공식 CLI를 찾을 수 없습니다.",
    invalid_request: "요청 내용을 다시 확인해 주세요.",
    invalid_sync_folder: "안전한 DotSync 동기화 폴더를 선택해 주세요.",
    provider_policy_disabled: "현재 정책상 이 공급자 기능을 사용할 수 없습니다.",
    login_cancelled: "로그인이 취소되었습니다.",
    provider_unavailable: "공식 로그인 서비스를 사용할 수 없습니다.",
    reauth_required: "다시 로그인해 주세요.",
    service_unavailable: "DotSync가 종료 중입니다. 앱을 다시 열어 주세요.",
    stale_sync_plan: "파일 상태가 바뀌었습니다. 미리보기를 다시 만들어 주세요.",
    sync_not_configured: "Settings에서 동기화 폴더를 먼저 선택해 주세요.",
  }),
});


function node(tag, attributes = {}, children = []) {
  const value = document.createElement(tag);
  for (const [name, attribute] of Object.entries(attributes)) {
    if (attribute === undefined || attribute === null || attribute === false) {
      continue;
    }
    if (name === "text") {
      value.textContent = String(attribute);
    } else if (name === "class") {
      value.setAttribute("class", String(attribute));
    } else if (name === "disabled") {
      value.disabled = Boolean(attribute);
    } else if (name === "checked") {
      value.checked = Boolean(attribute);
    } else {
      value.setAttribute(name, String(attribute));
    }
  }
  for (const child of children) {
    if (child !== null && child !== undefined) {
      value.append(child);
    }
  }
  return value;
}


function button(text, action, attributes = {}) {
  return node("button", {
    type: "button",
    text,
    "data-action": action,
    ...attributes,
  });
}


function safeErrorCopy(code) {
  return COPY.errors[code] ?? COPY.genericError;
}


function formatPercentage(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return `${Math.round(value)}%`;
}


function formatObservedAt(value) {
  if (typeof value !== "string") {
    return "조회 기록 없음";
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) {
    return "조회 기록 없음";
  }
  const ageMinutes = Math.max(0, Math.round((Date.now() - parsed) / 60_000));
  if (ageMinutes < 1) {
    return "방금";
  }
  if (ageMinutes < 60) {
    return `${ageMinutes}분 전`;
  }
  return new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(parsed));
}


function formatReset(value) {
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) {
    return "초기화 시각 없음";
  }
  return `${new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value))} 초기화`;
}


function windowFor(account, name) {
  return account.usage?.windows?.find((window) => window.name === name) ?? null;
}


function usageMeter(window, fallbackLabel) {
  const used = window?.used_percent;
  const percentage = formatPercentage(used);
  const progress = node("progress", {
    max: "100",
    value: typeof used === "number" ? Math.max(0, Math.min(100, used)) : 0,
    class: typeof used === "number" && used >= 80 ? "high" : undefined,
    "aria-label": `${fallbackLabel} 사용량 ${percentage}`,
  });
  return node("div", { class: "meter" }, [
    node("label", {}, [
      node("span", { text: window?.label || fallbackLabel }),
      node("b", { text: percentage }),
    ]),
    progress,
    node("p", { class: "meter-reset", text: formatReset(window?.resets_at) }),
  ]);
}


function activeJob(model, accountId) {
  return Object.values(model.jobs).find(
    (job) => job.account_id === accountId
      && ["queued", "running", "waiting_for_user"].includes(job.state),
  );
}


function accountStatus(account, model) {
  const job = activeJob(model, account.id);
  if (job) {
    return job.state === "waiting_for_user" ? "브라우저 확인 중" : "작업 중";
  }
  if (model.accountErrors[account.id]) {
    return safeErrorCopy(model.accountErrors[account.id]);
  }
  if (account.state === "ready") {
    return account.usage ? formatObservedAt(account.usage.observed_at) : "사용량 없음";
  }
  const values = {
    logged_out: "로그인 필요",
    reauth_required: "재인증 필요",
    unsupported: "지원되지 않는 버전",
    error: "확인 필요",
  };
  return values[account.state] ?? "상태 알 수 없음";
}


function pageHeading(title, copy, actions = []) {
  return node("header", { class: "page-heading" }, [
    node("div", {}, [node("h2", { text: title }), node("p", { text: copy })]),
    node("div", { class: "page-actions" }, actions),
  ]);
}


function overviewAccount(account, model) {
  return node("div", { class: "overview-account" }, [
    node("div", {}, [
      node("b", { text: account.label }),
      node("small", {
        text: [account.identity?.plan, account.identity?.email].filter(Boolean).join(" · ") || accountStatus(account, model),
      }),
    ]),
    usageMeter(windowFor(account, "five_hour"), "5시간"),
    usageMeter(windowFor(account, "seven_day"), "7일"),
    button("새로고침", "refresh-account", {
      "data-account-id": account.id,
      disabled: Boolean(activeJob(model, account.id)),
    }),
  ]);
}


function syncCounts(sync) {
  const result = { clean: 0, attention: 0, missing: 0, unknown: 0 };
  for (const app of sync?.apps ?? []) {
    if (app.state === "clean") {
      result.clean += 1;
    } else if (app.state === "missing") {
      result.missing += 1;
    } else if (app.state === "unknown") {
      result.unknown += 1;
    } else {
      result.attention += 1;
    }
  }
  return result;
}


function syncOverview(model) {
  if (!model.sync?.configured) {
    return node("article", { class: "glass-card empty-state" }, [
      node("strong", { text: "동기화 폴더를 선택해 주세요" }),
      document.createTextNode("Settings에서 폴더를 선택하면 Backup과 Apply 미리보기를 사용할 수 있습니다."),
      button("Settings 열기", "navigate", { "data-destination": "settings" }),
    ]);
  }
  const counts = syncCounts(model.sync);
  return node("article", { class: "glass-card setting-card" }, [
    node("div", { class: "glass-heading" }, [
      node("strong", { text: "Config Sync" }),
      node("span", { text: `${model.sync.apps.length} tracked apps` }),
    ]),
    node("div", { class: "setting-line" }, [node("b", { text: "Clean" }), node("span", { text: counts.clean })]),
    node("div", { class: "setting-line" }, [node("b", { text: "Changed" }), node("span", { text: counts.attention })]),
    node("div", { class: "setting-line" }, [node("b", { text: "Missing" }), node("span", { text: counts.missing })]),
    node("div", { class: "setting-line" }, [node("b", { text: "Unknown" }), node("span", { text: counts.unknown })]),
    button("Config Sync 열기", "navigate", { "data-destination": "sync" }),
  ]);
}


function renderOverview(model) {
  const accounts = model.accounts;
  const usage = node("article", { class: "glass-card usage-panel" }, [
    node("div", { class: "glass-heading" }, [
      node("strong", { text: "Subscription usage" }),
      node("span", { text: `${accounts.length} accounts · cached snapshots` }),
    ]),
  ]);
  if (accounts.length === 0) {
    usage.append(node("div", { class: "empty-state" }, [
      node("strong", { text: "사용량 기록이 없습니다" }),
      document.createTextNode("Accounts에서 계정을 추가한 뒤 명시적으로 새로고침해 주세요."),
      button("Accounts 열기", "navigate", { "data-destination": "accounts" }),
    ]));
  } else {
    usage.append(...accounts.map((account) => overviewAccount(account, model)));
  }
  return [
    pageHeading("Overview", "계정 캐시와 마지막으로 확인한 동기화 상태를 함께 봅니다.", [
      button("전체 새로고침", "refresh-all"),
      button("Backup preview", "navigate", { class: "primary", "data-destination": "sync" }),
    ]),
    node("div", { class: "overview-grid" }, [usage, syncOverview(model)]),
  ];
}


function accountRow(account, model) {
  const job = activeJob(model, account.id);
  const actions = [
    button("이름", "rename-account", { "data-account-id": account.id, disabled: Boolean(job) }),
    button("재인증", "login-account", { "data-account-id": account.id, disabled: Boolean(job) }),
    button("로그아웃", "logout-account", { "data-account-id": account.id, disabled: Boolean(job) }),
    button("삭제", "delete-account", { class: "danger", "data-account-id": account.id, disabled: Boolean(job) }),
  ];
  const providerName = account.provider === "claude" ? "Claude" : "Codex";
  const isolatedHome = account.provider === "claude" ? "CLAUDE_CONFIG_DIR" : "CODEX_HOME";
  return node("div", { class: "account-row" }, [
    node("div", { class: "account-id" }, [
      node("span", { class: "service-icon", text: providerName.slice(0, 1), "aria-hidden": "true" }),
      node("div", {}, [
        node("b", { text: account.label }),
        node("small", { text: account.identity?.email || `격리된 ${isolatedHome}` }),
      ]),
    ]),
    node("div", {}, [node("span", { class: "tiny-label", text: "Plan" }), node("span", { class: "tiny-value", text: account.identity?.plan || "—" })]),
    node("div", {}, [node("span", { class: `status-pill${model.accountErrors[account.id] ? " error" : ""}`, text: accountStatus(account, model) })]),
    node("div", { class: "account-actions" }, actions),
  ]);
}


function providerAccountTable(model, provider, name, isolatedHome) {
  const accounts = model.accounts.filter((account) => account.provider === provider);
  const table = node("article", { class: "glass-card" }, [
    node("div", { class: "glass-heading" }, [
      node("strong", { text: `${name} · ${accounts.length} accounts` }),
      node("span", { text: `각 계정마다 독립된 ${isolatedHome}` }),
    ]),
  ]);
  if (accounts.length === 0) {
    table.append(node("div", { class: "empty-state" }, [
      node("strong", { text: "관리 중인 계정이 없습니다" }),
      document.createTextNode(`기존 ~/.${provider} 프로필은 가져오지 않습니다.`),
    ]));
  } else {
    table.append(...accounts.map((account) => accountRow(account, model)));
  }
  return table;
}


function renderAccounts(model) {
  return [
    pageHeading("Accounts", "DotSync가 새로 만든 격리 프로필만 관리합니다.", [
      button("+ Claude account", "add-claude"),
      button("+ Codex account", "add-codex", { class: "primary" }),
    ]),
    providerAccountTable(model, "claude", "Claude", "CLAUDE_CONFIG_DIR"),
    providerAccountTable(model, "codex", "Codex", "CODEX_HOME"),
  ];
}


function syncStateCopy(app) {
  if (app.state === "clean") {
    return "Clean";
  }
  if (app.state === "missing") {
    return "Missing";
  }
  if (app.state === "unknown") {
    return "Unknown";
  }
  const directions = {
    "local-newer": "Local newer",
    "folder-newer": "Folder newer",
    diverged: "Diverged",
  };
  return directions[app.direction] ?? "Changed";
}


function syncAppRow(app, selected) {
  const checkbox = node("input", {
    type: "checkbox",
    checked: selected,
    "data-action": "toggle-sync-app",
    "data-app": app.name,
    "aria-label": `${app.name} 선택`,
  });
  return node("div", { class: "sync-app-row" }, [
    checkbox,
    node("div", { class: "sync-app-name" }, [
      node("span", { class: "app-glyph", text: app.name.slice(0, 2).toUpperCase(), "aria-hidden": "true" }),
      node("div", {}, [node("b", { text: app.name }), node("small", { text: "사용자 선택 동기화 항목" })]),
    ]),
    node("span", {
      class: app.state === "clean" ? "app-state" : "app-state attention",
      text: syncStateCopy(app),
    }),
  ]);
}


function previewRows(preview) {
  if (!preview) {
    return [node("li", {}, [node("b", { text: "미리보기" }), node("span", { text: "아직 만들지 않음" })])];
  }
  const rows = [];
  for (const plan of preview.plans ?? []) {
    const changed = (plan.changes ?? []).filter((change) => change.kind !== "unchanged");
    rows.push(node("li", {}, [
      node("b", { text: plan.app }),
      node("span", { text: changed.length ? `${changed.length}개 변경` : "변경 없음" }),
    ]));
  }
  return rows.length ? rows : [node("li", {}, [node("b", { text: "선택" }), node("span", { text: "변경 없음" })])];
}


export function syncPreviewPresentation(model) {
  const direction = model.preview?.direction ?? model.activeDirection;
  return Object.freeze({
    direction,
    title: `${direction === "apply" ? "Apply" : "Backup"} preview`,
    warning: direction === "apply"
    ? "Apply는 로컬 설정을 덮어쓰기 전에 미리보기 digest를 다시 확인하고 selected sync folder/.backups에 백업합니다."
      : "Backup은 선택한 앱의 로컬 설정을 동기화 폴더로 보냅니다.",
    executeText: `${direction === "apply" ? "Apply" : "Backup"} 실행`,
    appCount: model.preview?.apps.length ?? model.selectedApps.size,
  });
}


export function syncExecutionConfirmation(preview) {
  const isApply = preview.direction === "apply";
  return Object.freeze({
    title: isApply ? "Apply를 실행할까요?" : "Backup을 실행할까요?",
    copy: isApply
      ? "미리보기와 동일한 파일만 로컬에 적용합니다. 덮어쓰기 전에 selected sync folder/.backups에 백업합니다."
      : "미리보기와 동일한 파일만 선택한 동기화 폴더에 기록합니다.",
    confirmText: isApply ? "백업 후 Apply" : "Backup 실행",
    danger: isApply,
  });
}


function syncPreviewPanel(model) {
  const presentation = syncPreviewPresentation(model);
  return node("aside", { class: "glass-card preview-panel" }, [
    node("div", { class: "panel-heading" }, [node("strong", { text: presentation.title }), node("span", { text: `${presentation.appCount} apps` })]),
    node("div", { class: "preview-tabs" }, [
      button("Backup", "preview-direction", { class: presentation.direction === "backup" ? "active" : undefined, "data-direction": "backup" }),
      button("Apply", "preview-direction", { class: presentation.direction === "apply" ? "active" : undefined, "data-direction": "apply" }),
    ]),
    node("ul", { class: "preview-lines" }, previewRows(model.preview)),
    node("div", { class: "preview-warning", text: presentation.warning }),
    model.preview
      ? button(presentation.executeText, "execute-sync", { class: "primary" })
      : button("미리보기 만들기", "create-sync-preview", { class: "primary", disabled: model.selectedApps.size === 0 }),
  ]);
}


function renderSync(model) {
  if (!model.sync?.configured) {
    return [
      pageHeading("Config Sync", "Backup과 Apply는 항상 미리보기부터 시작합니다.", [
        button("Settings 열기", "navigate", { class: "primary", "data-destination": "settings" }),
      ]),
      node("section", { class: "empty-state" }, [
        node("strong", { text: "동기화 폴더가 아직 선택되지 않았습니다" }),
        document.createTextNode("오류가 아닙니다. Settings에서 사용할 폴더를 선택해 주세요."),
        button("폴더 선택", "select-sync-folder", { class: "primary" }),
      ]),
    ];
  }
  const apps = node("article", { class: "glass-card" }, [
    node("div", { class: "glass-heading" }, [
      node("strong", { text: "선택한 동기화 폴더" }),
      node("span", { text: `${model.sync.apps.length} tracked apps` }),
    ]),
    ...model.sync.apps.map((app) => syncAppRow(app, model.selectedApps.has(app.name))),
    node("div", { class: "sync-actions setting-card" }, [button("선택 저장", "save-sync-apps")]),
  ]);
  return [
    pageHeading("Config Sync", "항상 변경 내용을 미리 보고 Backup 또는 Apply를 실행합니다.", [
      button("폴더 변경", "select-sync-folder"),
      button("상태 검사", "load-sync-status"),
    ]),
    model.lastSyncMessage ? node("p", { class: "privacy-boundary", text: model.lastSyncMessage }) : null,
    node("div", { class: "sync-workspace" }, [apps, syncPreviewPanel(model)]),
  ];
}


function cacheDetails(accounts) {
  const snapshots = accounts.map((account) => account.usage).filter(Boolean);
  const last = snapshots.map((snapshot) => snapshot.observed_at).sort().at(-1);
  return {
    age: formatObservedAt(last),
    versions: [...new Set(snapshots.map((snapshot) => snapshot.provider_version).filter(Boolean))].join(", ") || "기록 없음",
  };
}


function renderSettings(model) {
  const details = cacheDetails(model.accounts);
  return [
    pageHeading("Settings", "저장 위치와 개인정보 경계를 확인합니다."),
    node("div", { class: "settings-grid" }, [
      node("article", { class: "glass-card setting-card" }, [
        node("h3", { text: "동기화 폴더" }),
        node("p", { text: "Backup과 Apply가 사용하는 사용자 선택 폴더입니다." }),
        node("div", { class: "path-field" }, [
          node("span", { text: "⌁", "aria-hidden": "true" }),
          node("code", { text: model.sync?.configured ? "선택한 동기화 폴더" : "아직 선택하지 않음" }),
          button("변경", "select-sync-folder"),
        ]),
      ]),
      node("article", { class: "glass-card setting-card" }, [
        node("h3", { text: "DotSync 앱 데이터" }),
        node("p", { text: "계정 프로필과 사용량 캐시는 동기화되지 않습니다." }),
        node("div", { class: "path-field" }, [
          node("span", { text: "⌂", "aria-hidden": "true" }),
          node("code", { text: "~/Library/Application Support/DotSync" }),
          button("Finder", "reveal-app-data"),
        ]),
      ]),
      node("article", { class: "glass-card setting-card" }, [
        node("h3", { text: "Refresh cache" }),
        node("p", { text: "CLI를 탐색하지 않고 검증된 캐시만 표시합니다." }),
        node("div", { class: "setting-line" }, [node("b", { text: "신선도 기준" }), node("span", { text: "15분" })]),
        node("div", { class: "setting-line" }, [node("b", { text: "최근 캐시" }), node("span", { text: details.age })]),
        node("div", { class: "setting-line" }, [node("b", { text: "마지막 관찰 CLI" }), node("span", { text: details.versions })]),
      ]),
      node("article", { class: "glass-card setting-card" }, [
        node("h3", { text: "Privacy" }),
        node("p", { text: "사용량 계정과 기존 앱 설정 동기화 권한을 분리합니다." }),
        node("div", { class: "setting-line" }, [node("b", { text: "~/.claude · ~/.codex" }), node("span", { text: "계정 기능에서 읽기·쓰기 안 함" })]),
        node("div", { class: "setting-line" }, [node("b", { text: "Provider access" }), node("span", { text: "공식 CLI · 격리 프로필만" })]),
        node("div", { class: "setting-line" }, [node("b", { text: "Apply" }), node("span", { text: "미리보기 + digest 확인 + 백업" })]),
      ]),
    ]),
  ];
}


export function renderManager(root, model) {
  const destinations = {
    overview: renderOverview,
    accounts: renderAccounts,
    sync: renderSync,
    settings: renderSettings,
  };
  const render = destinations[model.destination] ?? renderOverview;
  root.replaceChildren(...render(model).filter(Boolean));
  document.querySelectorAll("[data-surface=\"manager\"] nav [data-destination]").forEach((item) => {
    item.classList.toggle("active", item.dataset.destination === model.destination);
    if (item.dataset.destination === model.destination) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  });
}


export function showStatus(message) {
  const target = document.querySelector("#status-message");
  target.textContent = message;
  target.hidden = false;
  window.setTimeout(() => {
    target.hidden = true;
    target.textContent = "";
  }, 4000);
}


export function showFatal(code) {
  const target = document.querySelector("body");
  target.replaceChildren(node("main", { class: "error-card" }, [
    node("strong", { text: "DotSync UI를 열 수 없습니다" }),
    document.createTextNode(safeErrorCopy(code)),
  ]));
}


export function confirmationDialog({ title, copy, confirmText, danger = false, inputLabel = null, inputValue = "" }) {
  const dialog = document.querySelector("#confirmation-dialog");
  const titleNode = document.querySelector("#confirmation-title");
  const copyNode = document.querySelector("#confirmation-copy");
  const submit = document.querySelector("#confirmation-submit");
  const label = document.querySelector("#confirmation-label");
  const input = document.querySelector("#confirmation-input");
  titleNode.textContent = title;
  copyNode.textContent = copy;
  submit.textContent = confirmText;
  submit.className = danger ? "danger" : "primary";
  if (inputLabel === null) {
    label.hidden = true;
    input.hidden = true;
    input.value = "";
  } else {
    label.textContent = inputLabel;
    label.hidden = false;
    input.hidden = false;
    input.value = inputValue;
  }
  dialog.returnValue = "cancel";
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => {
      resolve({ confirmed: dialog.returnValue === "confirm", value: input.value });
    }, { once: true });
  });
}


export function errorCopy(code) {
  return safeErrorCopy(code);
}
