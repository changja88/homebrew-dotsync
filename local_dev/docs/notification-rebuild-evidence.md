# 알림 재설계 — 현장 증거 로그 (2026-07-25)

기존 notification-guard-spec.md(v6)는 **폐기 전제**로 다시 검증한다.
이 문서는 추측 없이 실측으로 확인된 것만 기록한다. 계획은 이 위에서만 세운다.

## 목표 (사용자 확정, 재해석 금지)

1. 포커스 유무와 **무관하게 항상** 울린다.
2. 울려야 하는 순간은 딱 둘: **사용자 입력이 필요할 때**, **전체 작업이 끝났을 때**.
3. **서브에이전트 관련 알림은 어떤 경우에도 금지.**

## 증상 (사용자 보고)

- **codex**: 배너 알림은 안 오는데 **소리만 아주 많이** 난다.
- **claude**: 입력 필요할 때도, 작업 완료 때도 **아무 알림이 없다**.

## 파이프라인 (실측 확인)

```
codex TUI  ──hooks.json──▶ codex-hook.sh ──POST /hook/codex──┐
                                                              ├─▶ Orca 앱 ─▶ 패널 상태머신 ─▶ 알림(배너+소리)
claude CLI ──settings.json hooks──▶ claude-hook.sh ──POST /hook/claude──┘
```

- 훅 서버: `127.0.0.1:$ORCA_AGENT_HOOK_PORT` (Orca PID가 LISTEN 중, 실측)
- 인증: `X-Orca-Agent-Hook-Token`. 잘못된 토큰 → **HTTP 403**, 올바른 토큰 + 미지 이벤트 → **HTTP 204**.
  → **전송 경로 자체는 정상 동작한다** (claude 패널에서 직접 POST해 확인).

## 확정 사실

### F1. 소리의 출처는 codex 터미널 벨이 **아니다**

`~/.codex/config.toml`은 `notifications = ["approval-requested"]`, `notification_method = "bel"`.
그런데 codex 로그 DB(`~/.codex/logs_2.sqlite`, 2026-07-23 03:44 ~ 07-25 03:14, 67,537행) 기준
**최근 24시간 승인 요청 이벤트 = 0건**.

```sql
SELECT COUNT(*) FROM logs WHERE ts > strftime('%s','now')-86400
  AND feedback_log_body LIKE '%ApprovalRequest%';   -- → 0
```

→ codex TUI가 켤 수 있는 유일한 알림이 한 번도 발화하지 않았다. **소리는 Orca가 내고 있다.**

### F2. Orca 알림 설정 현재값

`~/Library/Application Support/orca/profiles/local-default/orca-data.json` → `settings.notifications`:

```json
{"enabled": true, "agentTaskComplete": true, "terminalBell": false,
 "suppressWhenFocused": false, "customSoundId": "blop",
 "customSoundPath": null, "customSoundVolume": 100}
```

`suppressWhenFocused: false`는 목표 1과 이미 일치. 소리는 "blop" 100%.

### F3. codex 멀티에이전트가 대량으로 돌고 있다

최근 24시간 로그에서 `op.dispatch.inter_agent_communication` 스팬이 스레드당 30~59회씩,
다수 스레드에서 관측. 서브에이전트 활동이 알림 이벤트의 주 공급원일 가능성이 높다.

### F4. 실행 중 codex 세션 대부분이 **수정 전 설정으로 떠 있다**

불변식 #6(subagent 훅 비활성) 커밋: **2026-07-24 23:53:23**.
실행 중 codex TUI 세션 시작 시각: 07-23 17:47 / 07-24 03:56 / 07-24 21:24 / 07-24 23:20 / 07-25 02:15.
→ **6개 중 5개가 fix 이전에 시작**. codex가 config를 프로세스 시작 시점에 고정한다면
이 세션들에는 수정이 반영되지 않았다. (config 재로딩 여부는 확인 중)

### F5. codex는 **실제 홈(`~/.codex`)에서 돈다**

`codexManagedAccounts = []`, `activeCodexManagedAccountId = null`.
`~/Library/Application Support/orca/codex-real-home-hooks/hooks.json.pre-orca` 존재 →
Orca가 `~/.codex/hooks.json`을 자기 것으로 덮어쓰면서 원본을 백업해둔 것.
→ `codex-runtime-home/`, `codex-accounts/*/`는 **휴면**. 세션 rollout 0건.

### F6. codex가 Orca에 보내는 훅 이벤트 (orca가 설치한 `~/.codex/hooks.json`)

`SessionStart, UserPromptSubmit, PreToolUse, PermissionRequest, PostToolUse, SubagentStart, SubagentStop, Stop`

현재 `[hooks.state]`에서 비활성: `subagent_start`, `subagent_stop` (2개).
**`PreToolUse`/`PostToolUse`는 살아 있고, 서브에이전트의 도구 호출마다 발화한다** —
이것이 상태 flapping을 만드는지 여부가 미해결 핵심.

### F7. claude 쪽 설정

`~/.claude/settings.json`:
- `preferredNotifChannel = "notifications_disabled"` ← claude 자체 알림 채널 OFF
- `inputNeededNotifEnabled = true`, `agentPushNotifEnabled = false`
- 설치된 훅: `SessionStart, PreToolUse, UserPromptSubmit, Stop, StopFailure, SubagentStart, SubagentStop, TeammateIdle, PostToolUse, PostToolUseFailure, PermissionRequest`
- **`Notification` 훅 없음** ← claude의 표준 "입력 필요" 신호가 orca로 전달되지 않을 가능성

### F8. macOS 26.5.2 — 알림 권한 상태는 프로그램으로 확인 불가

`~/Library/Group Containers/group.com.apple.usernoted/db2/db`는 존재하지만
Full Disk Access 없이 읽을 수 없음. `com.apple.ncprefs.plist`는 이 버전에 없음.
→ **Orca의 배너 권한 여부는 사용자가 시스템 설정에서 직접 확인해야 한다.**

## Orca 코드 분석 결과 (app.asar 추출, v1.4.152) — O1~O4, O7 해결

### R1. 소리와 배너는 **완전히 별개 경로**다 (O1 해결)

- 배너: main 프로세스의 Electron `new Notification()` — `out/main/index.js:129046`
- 소리: preload/renderer의 Chromium `<audio>` 엘리먼트 — `out/preload/index.js:1209,1234`

결정적인 두 줄:

```js
// out/main/index.js:129040-129045 — 커스텀 사운드면 배너를 음소거한다
if (getEffectiveNotificationSoundId(settings) !== "system") {
  notificationOptions.silent = true;
}
// out/main/index.js:129114-129126 — show() 직후 확인 없이 delivered:true 반환
notification.show();
return { delivered: true };
```

→ `customSoundId`가 `"system"`이 아니면(현재 `"blop"`) **모든 소리가 Chromium `<audio>`에서 나온다.**
이 소리는 macOS 알림 설정·집중 모드·방해금지의 통제를 **전혀 받지 않는다.**
그리고 Orca는 배너가 실제로 떴는지 확인하지 않고 `delivered:true`로 간주한 뒤 소리를 재생한다.

macOS 게이트는 `authorization`만 보고 `alert`(alertSetting)는 **버린다** (`:128689-128705`).
따라서 "알림 허용 ON + 배너 스타일 없음" 또는 "집중 모드 활성" 상태에서
**배너 없음 + 소리 남**이 정확히 재현된다.

이 기기에서 헬퍼(`/Applications/Orca.app/Contents/MacOS/orca-notification-status`)는
`{"authorization":"authorized","alert":"enabled"}`를 반환 → 권한 자체는 살아 있다.

### R2. 코덱스 반복 알림 = **Orca의 확인된 버그** (O2·O3 해결)

```js
// out/main/index.js:8257-8267
function codexRosterEffectiveState(roster, leadState) {
  if (!roster || roster.size === 0) { return leadState; }
  ...
  return leadState === "done" ? "working" : leadState;   // ← roster 비어있지 않으면 done을 working으로 되돌림
}
```

lead `Stop`은 roster를 지운다(`:10551-10553`). 그런데 **`agent_id`가 붙은 아무 이벤트나** roster를 되살린다
(`:10535-10547`) — `SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, Stop` 전부.
이 분기는 `Stop`의 roster-삭제 분기보다 **먼저** 실행된다.

발화 시퀀스:

| # | 이벤트 | roster | 상태 | 결과 |
|---|---|---|---|---|
| 1 | lead `Stop` | 삭제됨 | `done` | **알림 #1 + 소리** |
| 2 | 늦은 서브에이전트 이벤트(`agent_id`) | 재생성 | `done`→**`working`** | 중복 가드 전부 리셋 |
| 3 | 서브에이전트 종료 | 비워짐 | **`done`** | **알림 #2 + 소리** |

뒤늦게 도착하는 서브에이전트 쌍마다 사이클이 하나씩 더 붙는다.

> **스펙 v6의 "pre/post_tool_use는 알림을 만들지 않으므로 안전하다"는 단정이 코드로 반증됐다.**
> `subagent_start`/`subagent_stop`만 끄는 불변식 #6으로는 이 버그를 막을 수 없다.

Orca의 **원격(SSH) 경로에는 되살림을 막는 가드가 있는데**(`:10439-10441`) 로컬 경로에는 없다.
명백한 상류 버그다.

### R3. claude 상태머신 — `Notification` 훅은 애초에 불필요했다 (O4 해결)

| claude 훅 | 상태 | 알림 |
|---|---|---|
| `PermissionRequest` | `waiting` | **울림 (needs input)** |
| `PreToolUse`(AskUserQuestion/RequestUserInput) | `waiting` | **울림** |
| `Stop` / `StopFailure` | `done` | **울림 (finished)** |
| `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure` | `working` | 안 울림 |
| `SubagentStart`/`SubagentStop`/`TeammateIdle` | roster만 갱신 | 안 울림 |
| **`Notification`** | 매핑 없음 → **무시** | 절대 안 울림 |

Orca는 claude의 `Notification` 이벤트를 **아예 처리하지 않는다.** 설치하지도 않는다.
→ F7의 "`Notification` 훅 없음"은 **문제가 아니었다.**

또한 claude 쪽은 codex와 같은 버그가 없다. `Stop`이 roster를 지우지 않고,
서브에이전트가 살아 있으면 단순히 `working`으로 **붙잡기만** 한다(`:10110`) — 진동하지 않는다.

### R4. Orca에는 "입력 필요 + 메인 완료만" 설정이 **없다**

"needs input"과 "task complete"가 **같은 source 문자열** `"agent-task-complete"`를 쓰고
(`renderer/index-AV0ztbgr.js:16906`, `:16918`), 게이트도 단일 토글
`settings.notifications.agentTaskComplete` 하나다(`main/index.js:129010`).
→ 구조적으로 분리 불가. 설정 UI 문구("코딩 에이전트가 작업을 마치고 유휴 상태가 됨")는
이 토글이 "입력 필요" 알림까지 끈다는 사실을 알려주지 않는다.

`excludeSubagent`, `notifyOnSubagent`, `leadOnly`, `mainAgentOnly` 등 관련 키 **전부 NOT FOUND**.

### R5. `terminalBell`은 패널 안 BEL을 죽이지 않는다 (O7 해결)

xterm.js의 bell 구독 자체가 없다(`onBell(` NOT FOUND). Orca는 PTY 출력 바이트를 직접 스캔해
(`out/shared/terminal-bell-detector.js`) `source:"terminal-bell"` 알림을 쏜다.
`terminalBell:false`는 **그 알림만** 막고, 트레이 점·미확인 배지는 그대로 뜬다.

부수 발견: **Orca는 입력 필요 시 PTY에 진짜 BEL을 직접 주입한다**(`main/index.js:222705-222711`).
`sendSyntheticTitle(... "\x07" + (needsUserInput ? "\x07" : ""))` — 두 번째 `\x07`이 실제 벨.
`terminalBell:false`라 지금은 게이트되지만, 켜면 승인 프롬프트마다 소리가 하나 더 붙는다.

### R6. 쿨다운은 **워크트리 단위 5초**

`dedupeKey = args.worktreeId ?? args.worktreeLabel ?? "global"` (`main/index.js:129015`, `:129032`),
`NOTIFICATION_COOLDOWN_MS = 5000` (`:128712`).
→ 같은 워크트리의 여러 패널이 **하나의 5초 예산을 공유**한다. 워크트리가 N개면 예산도 N개라
동시 에이전트 N개는 간격 없이 N번 울 수 있다.
**claude가 조용한 원인 후보 1순위** (같은 워크트리 코덱스 소음이 claude 알림을 먹는다). 확인 요청 중.

### R7. 포커스 억제는 창 단위이고, 비활성 워크트리에는 적용되지 않는다

```js
// main/index.js:129028-129030
if (settings.suppressWhenFocused && args.isActiveWorktree && browserWindow && browserWindow.isFocused())
```
`BrowserWindow.isFocused()` — 창 포커스(앱 활성도, 패널 포커스 아님).
사용자는 `suppressWhenFocused:false`라 무관하지만, 목표 1(포커스 무관)은 이 값으로 이미 충족된다.

## codex 공식 문서·소스 조사 결과 (O5 해결)

- **`[tui]` 알림 설정은 TUI 시작 시점 스냅샷이다.** `Tui::set_notification_settings` 호출부는
  `App::new`와 resume/fork 경로 둘뿐이고 config.toml 감시자가 없다. → **`[tui]` 변경은 codex 재시작 필요.**
  (반면 core/thread 단위 설정 — `approvals_reviewer` 등 — 은 턴마다 다시 읽힌다. 실측 확인됨.)
- **`notification_condition = "unfocused"`는 DECSET 1004 포커스 리포팅에 의존**하고
  `terminal_focused` 초깃값이 `true`다. 터미널이 포커스 이벤트를 안 보내면 **영원히 focused로 남아
  알림이 0건**이 된다. 목표 1(포커스 무관)에는 `"always"`가 맞다.
- **`notification_method`에 `"none"`은 없다.** 끄려면 `notifications = false`.
  `bel`=진짜 벨(가청), `osc9`=데스크톱 토스트(Ghostty/iTerm2/Kitty/Warp/WezTerm만).
- **이벤트 이름은 셋**: `agent-turn-complete`, `approval-requested`, `plan-mode-prompt`.
  기본값은 `true`(전부). **오타는 조용히 통과되어 전체를 꺼버린다.**
  현재 사용자 설정은 `["approval-requested"]` → **turn-complete는 애초에 꺼져 있었다.**
- **`approvals_reviewer = guardian_subagent|auto_review`의 실제 효과** (소스 `tools/approvals.rs:203-262`):
  TUI `approval-requested` 알림은 **안 뜨지만**, `permission_request` **훅은 그대로 발화한다.**
  → 기존 가드가 말한 "가짜 needs-input 알림"은 실재했다. `guardian_subagent`는 부분 차단기일 뿐이다.
- **`notify`는 서브에이전트 턴에도 발화**하고 `[features] hooks = false`로도 안 꺼진다.
  사용자는 `notify = []`라 무관.
- **`SubagentStart`/`SubagentStop`은 사용자가 띄운 서브에이전트에만 발화**한다.
  guardian reviewer, `/review` 같은 내부 서브에이전트는 훅을 만들지 않는다(상류 테스트로 확인).
- `[hooks.state] enabled = false`는 **실제로 실행을 막는다**(`discovery.rs:544,566`).
  단 System/MDM 관리 훅은 이 값을 무시한다. 키의 `:group:handler`는 **위치 기반**이라
  hooks.json 순서가 바뀌면 조용히 다른 훅을 가리킨다.

## claude 공식 문서·바이너리 조사 결과 (O6 해결)

- **`preferredNotifChannel = "notifications_disabled"`는 훅을 막지 않는다.** 확정.
  디스패처가 `await YG(e)`(= Notification 훅 실행)를 **채널 분기 이전에 무조건** 호출한다.
  → claude 자체 알림만 꺼지고 Orca로 가는 훅은 전부 정상 발화한다.
- `Stop`(메인)과 `SubagentStop`(서브)은 **상호 배타**다(`let l = o ? "SubagentStop" : "Stop"`).
  `Stop` 페이로드에는 `agent_id`가 **없다** → 메인 완료 신호로 그 자체가 신뢰 가능.
  **codex와 달리 claude는 이벤트 이름만으로 메인/서브 구분이 확실하다.**
- claude에는 **"메인 턴 완료" 알림 자체가 없다.** 완료 인접 신호는 60초 뒤의 `idle_prompt`뿐.
  즉시 감지에는 `Stop` 훅이 유일한 정답이다.
- **6초 상호작용 게이트**(미문서화): 사용자가 최근 6초 내 입력했으면 `permission_prompt`
  계열 `Notification`이 발화하지 않는다. 단 **Orca는 `Notification` 이벤트를 아예 안 쓰므로 무관**하다.
- Orca가 `PreToolUse`의 `AskUserQuestion`을 waiting으로 매핑하므로,
  "Notification 훅 없음"이 Orca 경로에서 만드는 실제 공백은
  **idle 60초 · plan 승인 · MCP elicitation · 세션 일시정지**로 한정된다.

## 내 03:03 수정은 소음 원인이 아니다 (기각)

동일 길이 24분 창 비교: 수정 직전(02:39-03:03) 22건 vs 직후(03:03-03:27) 23건 — 차이 없음.
게다가 그 매칭은 실제 승인 이벤트가 아니라 프롬프트 본문에 포함된 단어의 오탐이었다.
→ **`approvals_reviewer` 변경과 `permission_request` 훅 재활성화는 소음 급증을 만들지 않았다.**

## 설계 결론 (확정)

> **알림 발화 권한자는 Orca 하나로 못박는다.**
> codex TUI, claude 자체 채널, codex `notify`는 전부 침묵시키고,
> 각 계층은 Orca에 **정확한 신호만 전달**하는 역할만 한다.

근거: 세 계층이 각자 알림을 내면 (a) 중복이 나고, (b) codex `[tui]`는 재시작해야 반영되며,
(c) codex의 포커스 감지는 Orca 터미널에서 신뢰할 수 없고, (d) Orca만이 배너+소리를 함께 낸다.

## Orca 후속 조사 결과 (O8~O11 해결) + 적용

### O8. codex 이벤트→상태 표 (코드 확인)

`agent_id` **없으면** 리드 상태, **있으면** 명부에 upsert 후 child-driven:

| 이벤트 | agent_id 없음 | agent_id 있음 | 알림 |
|---|---|---|---|
| SessionStart | working, 명부 삭제 | upsert working | 없음 |
| UserPromptSubmit | working | upsert working | 없음 |
| PreToolUse(일반) | working | upsert working | 없음 |
| **PreToolUse(ask-user)** | **waiting** | upsert waiting | **울림** |
| **PermissionRequest** | **waiting** | upsert waiting | **울림** |
| PostToolUse | working | upsert working | 없음 |
| **Stop** | **done**, 명부 삭제 | **upsert working**(!) | 없음→진동 |
| SubagentStart/Stop | agent_id 없으면 무시 | 명부 갱신/삭제 | (Stop시 리드 done이면 울림) |

→ **codex `request_user_input`(0.145+)은 자동 허용이라 PreToolUse(ask-user)로만 waiting이 온다.**
`PreToolUse`를 끄면 요구 1이 깨진다. **끄지 않는다.**

### O9. claude 무음 — 단일 원인 아님, 복합

1. **채널 비대칭**: codex는 가청 채널 3개(TUI bel + Orca 합성 BEL + 훅 알림), claude는 1개
   (`notifications_disabled`로 자체 벨 OFF). "codex는 폭주, claude는 조용"의 큰 몫.
2. **워크트리 5초 쿨다운**(`main/index.js:128918-128927`): 같은 워크트리에서 먼저 예약한 쪽이
   이기고 나중 쪽은 버려짐. codex가 진동으로 예약을 독점하면 claude가 밀린다. **간헐적** 손실.
3. **유령 서브에이전트 pin**(`:10110`): claude `Stop`은 명부를 안 지운다. `SubagentStop` 하나가
   유실되고 `Stop` 페이로드에 `background_tasks`가 없으면 명부 항목이 영구히 남아 그 패널은
   **완료 알림을 다시는 못 낸다.** 구조적 무음 후보.
4. ~~"현재 패널만 알림"~~ **반증됨**: `:16525`는 죽은/재부모화된 패널만 거른다. 숨은 패널도 알림 온다.

→ claude 훅은 정상 설치돼 있다(10개 전부). **"Notification 훅 없음"은 Orca 경로에서 무관**
(Orca가 claude Notification 이벤트를 아예 안 씀).

### O10. codex 훅 다이어트 — 검증 결과 + 채택안

훅 다이어트로 요구 1·2는 충족된다. 단 요구 3은 **완전히는** 안 된다:
서브에이전트 `PermissionRequest`(agent_id)는 설계상 여전히 waiting 알림을 만들고(진짜 사람 답이
필요하므로 오히려 바람직), 서브에이전트 `Stop`(agent_id)은 명부에 working으로 등록돼 진동을
한 번 더 만들 수 있다.

**채택: `PostToolUse`, `SubagentStart`, `SubagentStop` 끄기. `SessionStart` 유지.**
- `SessionStart` 유지 이유: codex 프로세스당 명부를 한 번 리셋(`:10549-10550`)해
  "working에 영구히 갇히는" 실패 모드를 닫는다.
- `PreToolUse` **유지**(O8 — request_user_input 경로).
- `SubagentStop`을 끄면 명부에서 항목을 지우는 유일한 함수가 사라지므로, 리드 `Stop`의
  명부 삭제(`:10551-10552`)와 `SessionStart` 리셋이 청소를 맡는다.

### O11. 수정의 내구성 — **여기가 이 문제의 핵심**

- **codex `[hooks.state] enabled = false`는 Orca 재작성에도 보존된다** — Orca가 `[hooks.state.*]`
  섹션을 유지하고 기존 `enabled=false`를 이어간다(`HC:2447,2500` / `codex-app-server…:740`).
  단 **테이블 안의 다른 키는 삭제되고 `trusted_hash`는 매번 재작성**된다.
- **그러나 실측: Orca가 03:37 `~/.codex/config.toml`을 재작성하며 기존 `enabled=false`를 지웠다.**
  → 보존이 항상 보장되진 않는다(재작성 타이밍/경로에 따라). **가드가 매 launch마다 재적용해야 한다.**
- `~/.claude/settings.json`·런타임 `hooks.json`은 매 PTY spawn마다 재설치 → 훅 삭제는 안 남는다.
  (하지만 우리는 훅을 지우는 게 아니라 codex config의 state만 끄므로 무관.)

**결론: 이 수정은 가드 없이는 오래 못 간다. `notification_guard`가 유일한 지속 장치다.**

## 적용 완료 (2026-07-25)

| 계층 | 항목 | 상태 |
|---|---|---|
| Orca | `customSoundId: "system"` | ✅ 사용자가 앱에서 변경 (소리=배너 재결합) |
| Orca | `suppressWhenFocused: false` / `terminalBell: false` | ✅ 이미 맞음 (bell은 켜면 안 됨) |
| codex | `~/.codex/hooks.json`의 `post_tool_use`/`subagent_start`/`subagent_stop` = `enabled false` | ✅ config 반영 |
| codex | `pre_tool_use`/`permission_request`/`stop`/`session_start`/`user_prompt_submit` = 켜짐 | ✅ (요구 1·2 신호) |
| codex | `approvals_reviewer = "user"` | ✅ 사용자 결정 (직접 승인) |
| codex | `notify = []` | ✅ 이미 맞음 |
| claude | `preferredNotifChannel: "notifications_disabled"` | ✅ 이미 맞음 (훅은 정상 발화) |
| guard | 불변식 #6 대상을 `SubagentStart/Stop`+`PostToolUse`로 확장 | ✅ 코드+테스트(571 pass) |

## 남은 한계 (사용자 통제 밖)

- **진짜 버그는 Orca 안에 있다**(로컬 경로 roster revive, 소리/배너 분리, 워크트리 쿨다운).
  우리 수정은 **버그를 자극하는 입력을 줄이는 우회**다. Orca 업데이트 시 재확인 필요.
  Orca 원격(SSH) 경로엔 이미 revive 가드가 있으므로(`:10439-10441`) 상류 수정도 쉬운 종류.
- 서브에이전트 `PermissionRequest`(진짜 사람 답 필요)는 여전히 알림 — 요구 1과 상충하지 않음.
- **실행 중이던 codex 세션들은 재시작해야 반영된다**(`[tui]`·훅 state는 프로세스 시작 시 스냅샷).

## 기존 접근의 문제

`notification_guard.py`는 훅을 **개별로 끄는** 방식으로 증상을 눌러왔다.
그 결과 "설정했는데 안 먹는" 죽은 설정과, 전제가 바뀌면 조용히 깨지는 규칙이 쌓였다.
재설계는 **어느 계층이 알림의 단일 권한자인지 먼저 정하고**, 나머지 계층은
그 권한자에게 정확한 신호만 보내도록 최소 구성해야 한다.
