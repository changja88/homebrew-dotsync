# Notification Guard 설계 명세

> 상태: 승인 대기 (적대 리뷰 1회 반영, v2) · 작성 2026-07-23 ·
> 2026-07-24 요구사항 확정 개정(v5): #2 폐기, #3 부재 시 공허 충족, #5 재정의 ·
> 2026-07-24 v6: 요구 3 "구조적 보장" 반증 — 불변식 #6(subagent 훅 비활성) 추가,
> #3·#6을 user 홈(`~/.codex`)까지 확장 (orca 07-23 업데이트로 전제 2개 붕괴)

## 요구사항 (2026-07-24 사용자 확정)

1. 알림은 **입력 필요**(권한·승인·질문)와 **메인 에이전트 작업 완료**(턴 종료,
   입력 대기 복귀) 두 순간에만 울린다.
2. **포커스 무관 항상** — Orca 창이 포커스를 갖고 있어도 위 두 알림은 온다.
3. **서브에이전트 완료는 어떤 경우에도 알림 금지.**
4. Terminal 벨 계열 설정(orca `terminalBell`, codex `[tui]`의
   `notifications`/`notification_method`/`notification_condition`)은 사용자가
   직접 관리한다 — 가드는 벨 관련 점검·경고·수리를 하지 않는다.

알림의 실제 발화 주체는 **Orca 앱**이다: claude/codex의 훅은 이벤트를 Orca
데몬(127.0.0.1 HTTP POST)으로 전달만 하고, Orca가 orca-data.json의
`settings.notifications` 값으로 발화 여부를 결정한다.

- 요구 1·2의 Orca 쪽 스위치: `enabled=true` + `agentTaskComplete=true` +
  `suppressWhenFocused=false`. 불변식 #5가 경고로 감시한다(수리 불가 —
  실행 중 Orca가 외부 수정을 되돌림, 07-22 실측).
- ~~요구 3은 구조적으로 보장된다~~ **(v6 반증, 2026-07-24 app.asar 실측)**:
  Orca가 "메인 pane working→idle 전이 시에만 발화"하는 것 자체는 사실이지만,
  codex의 `SubagentStop` 이벤트가 그 전이를 **만들 수 있다**. Orca의 codex
  상태 머신(`normalizeCodexEvent`)은 lead `Stop`에서 subagent roster를 통째로
  삭제하고 done을 발화하는데, 그 뒤 늦게 도착한 서브에이전트 이벤트(payload에
  `agent_id` 포함 — 훅 POST는 개별 curl이라 순서 보장 없음, 배경 subagent도
  turn 경계를 넘음)가 roster를 되살려 pane을 다시 working으로 만들고, 마지막
  `SubagentStop`이 roster를 비우는 순간 working→done 전이가 재발생 →
  `observeHookStatus`가 새 `stateStartedAt`으로 알림을 발화한다(서브에이전트
  완료 알림). 따라서 요구 3은 구조 의존이 아니라 **불변식 #6**(subagent 훅
  비활성)으로 보장한다. 네이티브 우회 경로는 기존대로 닫혀 있다 — claude
  자체 채널은 #4(`notifications_disabled`)가, codex 외부 notify 프로그램은
  #1(`notify = []`)이 차단한다.

## 배경과 목적

에이전트 알림 정책은 위 요구사항 절과 같다. 이를 위해
2026-07-22에 codex/claude/orca 설정을 정리했으나, 외부 프로세스가 이 설정을
주기적으로 되돌린다는 것이 실측됐다:

1. **ChatGPT 데스크톱 앱**(Sparkle 업데이터)이 codex `notify` 줄에
   SkyComputerUseClient(매 턴 알림)를 재주입 — 3회 실측 (07-19, 07-22, 07-23
   재부팅 직후). 최근에는 기존 값을 `--previous-notify "[]"` 인자로 보존하며
   재주입하므로 주입 형태가 계속 진화한다. user config뿐 아니라 orca 관리 홈
   미러에도 재주입됐다.
2. **codex 신뢰 재기록 플로우**가 `[hooks.state]`의 `enabled = false` 줄을
   제거 — 1회 실측 (07-23, legacy 미러).
3. **Orca 관리 홈 재생성** — orca가 계정 마이그레이션 시 관리 홈을 새로 만들
   수 있고, 이때 계정 ID(경로)가 바뀐다. **주의: orca는 seed/merge 시 user
   config의 `[hooks.state]` 블록을 전부 제거하고 복사한다** (app.asar에서
   확인) — user config에 template 엔트리를 두는 방식은 생존 불가.

launcher는 우리가 소유한 개입 지점 중 에이전트 실행에 가장 가까운 곳이므로,
관리되는 launch마다 불변식을 점검하고 어긋나 있으면 자동 수리한다.

## 커버리지 (정직한 서술)

가드는 **zsh shim이 관리하기로 판정한 launch에서만** 실행된다. shim은
interactive tty + 인자 allowlist(무인자, `resume`/`fork`/`-c`/`--continue`/
`-r`/`--resume`)를 통과한 호출만 launcher로 보낸다.

- **커버됨**: Orca 터미널 패널의 일반적인 `codex`/`claude` 실행 (실측: 현재
  orca 패널들은 로그인 interactive zsh + 사용자 zshrc 소싱 경유로 shim을
  통과한다), 일반 터미널의 interactive 실행.
- **커버 안 됨 (비범위)**: `codex exec`, `claude -p`, 스크립트/cron 호출,
  allowlist 밖 인자를 가진 호출. 이 경로들은 가드 없이 실행되므로 드리프트
  창이 남는다. shim의 관리 판정 **이전**(모든 셸 호출)으로 가드를 옮기는
  대안은 비대화식 스크립트 루프마다 python 기동 비용을 물리므로 기각.
- **커버 안 됨 (2026-07-24 실측 추가)**: orca **worktree 패널**은
  `bash -lc 'deadline=…; … exec codex'` 형태(setup 대기 루프 + bash 로그인
  셸)로 에이전트를 실행해 zsh shim을 타지 않는다. 즉 "orca 패널은 shim을
  통과한다"는 v2의 전제는 일반 터미널 패널에만 성립한다. 가드는 사용자가
  직접 치는 interactive launch에서 실행될 때마다 전체 config를 수렴시키는
  방식으로 이 구멍을 보완한다(수리 대상 발견은 launch 경로와 무관).
- **전제의 취약성**: orca가 pty/셸 구성(login zsh, ZDOTDIR 래퍼)이나 실행
  인자 형태를 바꾸면 커버리지가 통째로 사라질 수 있다. 롤아웃 절의 스모크
  검증으로 실제 동작을 확인하고, 재발 시 이 전제부터 의심한다.
  (실제로 07-23 orca 업데이트가 이 방식으로 전제 2개를 무너뜨렸다 — 위
  worktree 패널 실측과, 아래 "user 홈" 절.)

## 불변식 (5종 유효 · #2는 폐기)

| # | 파일 | 원하는 형태 | 드리프트 시 |
|---|---|---|---|
| 1 | codex config들의 최상위 `notify` | 정확히 `[]` | 수리 (제거한 내용을 로그에 남김) |
| 2 | ~~codex `[tui] notification_condition`~~ | **폐기 (2026-07-24)** — 벨 채널 설정은 사용자 관리(요구사항 4). 가드는 읽지도 고치지도 않는다 | — |
| 3 | codex config들의 `[hooks.state."<홈>/hooks.json:permission_request:<g>:<h>"]` | `enabled = false` | 수리 (줄 삽입, 엔트리 없으면 생성) |
| 4 | `~/.claude/settings.json`의 `preferredNotifChannel` | `"notifications_disabled"` | 수리 |
| 5 | orca-data.json의 `notifications.enabled` / `agentTaskComplete` / `suppressWhenFocused` | `true` / `true` / `false` | **경고만** (수리 안 함). `terminalBell`은 점검 대상 아님 |
| 6 | codex config들의 `[hooks.state."<홈>/hooks.json:subagent_start:<g>:<h>"]`와 `…:subagent_stop:<g>:<h>` | `enabled = false` (**무조건** — `approvals_reviewer` 무관) | 수리 (줄 삽입, 엔트리 없으면 생성) |

불변식 #6이 요구 3의 실질 보장 장치다(요구사항 절의 v6 반증 참조).
SubagentStart/SubagentStop이 Orca에 도달하지 않으면 subagent roster의
부활→소진이 만드는 두 번째 working→done 전이 자체가 불가능해진다. 부작용:
Orca 사이드바의 서브에이전트 활동 표시가 사라지고, `agent_id`가 딸린 도구
훅 이벤트(pre/post_tool_use는 lead 상태 추적에 필요해 살려둠)가 lead Stop
이후 pane을 일시적으로 working으로 되살릴 수 있으나, `SubagentStop`이 없으면
done 전이가 뒤따르지 않으므로 **알림은 발생하지 않고** 다음 turn의 lead
Stop(roster 전체 삭제)에서 자연 수렴한다.

### codex config 파일 발견 (동적)

하드코딩된 계정 ID 없이 glob으로 발견한다. 존재하는 것만 대상으로 한다.

- `~/.codex/config.toml` — #1, #3, #6 (hooks.json은 `~/.codex/hooks.json`)
- `~/Library/Application Support/orca/codex-accounts/*/home/config.toml` — #1, #3, #6
- `~/Library/Application Support/orca/codex-runtime-home/home/config.toml` — #1, #3, #6

**user 홈 포함 근거 (v6 개정)**: 07-23 orca 업데이트 후 orca 패널의 codex는
`CODEX_HOME` 주입 없이 **user 홈으로 실행**되고(실행 중 프로세스 env +
`~/.codex/sessions` rollout 실측), orca가 `~/.codex/hooks.json`을 설치했다.
즉 v5의 "user 홈에 hooks.json 없음" 전제가 깨졌고, 현재 실제로 발화하는
훅은 user 홈의 것이다. orca 재미러링이 user config의 hooks.state를 제거하는
동작(배경 3)은 여전하므로 이 엔트리는 지워질 수 있다 — 가드가 launch마다
재수리해 수렴시킨다(그게 가드의 존재 이유다).

불변식 #3·#6의 키는 각 홈의 hooks.json을 **파싱해서 도출**한다:
해당 이벤트(`PermissionRequest`/`SubagentStart`/`SubagentStop`)의 실제
(group, handler) 인덱스로 `<홈>/hooks.json:<snake_case 이벤트>:<g>:<h>` 키를
만든다 (인덱스 하드코딩 금지 — orca가 핸들러를 추가/재배열하면 `:0:0`이
stale해진다). **hooks.json이 없으면 그 홈의 #3·#6은 공허 충족으로 조용히
건너뛴다** (2026-07-24 개정) — 훅 파일이 없다 = 해당 훅이 0개 = 알림 원인이
없다. 로그인 잔재 홈(orca가 브라우저 로그인 시 만드는
`codex-accounts/*/home`에 config.toml만 남는 경우)이 매 launch마다 고칠 수
없는 경고를 반복하던 문제의 해소. 파싱 불가면(파일이 있는데 깨짐) 실제
이상이므로 경고 행을 남기고 건너뛴다. hooks.json에 해당 이벤트가 없으면
그 이벤트의 불변식도 공허 충족이다.

~~user config에는 #3을 적용하지 않는다~~ (v6 폐기 — 위 "user 홈 포함 근거"
참조). **관리 홈 재생성 직후 첫 launch 동안은 훅이 살아 있는 창이
존재한다** — 그 다음 launch에서 glob이 새 홈을 발견해 수리하는 것이 이
설계의 한계다.

불변식 #5의 파일은 `~/Library/Application Support/orca/profiles/*/orca-data.json`
glob으로 발견한다.

### 조건부 규칙

- **#3은 그 config의 `approvals_reviewer`가 `"guardian_subagent"`일 때만
  적용한다.** 값이 `"user"` 등이면 가드는 #3을 수리하지 않는다 — 승인이
  실제로 사용자에게 오는 구성에서는 PermissionRequest 훅이 진짜 "입력 필요"
  신호다. 이때 기존 `enabled = false`가 남아 있으면 경고 행으로만 알린다
  (자동 삭제 금지 — 사용자가 직접 지운다).
- **#6은 무조건 적용한다** — 요구 3이 "어떤 경우에도 알림 금지"이므로
  `approvals_reviewer` 값과 무관하다. 서브에이전트의 진짜 "입력 필요"
  신호는 subagent 훅이 아니라 `agent_id`가 딸린 PreToolUse(AskUserQuestion)
  → waiting 전이로 전달되므로 #6이 요구 1을 해치지 않는다.
- **#5는 절대 파일을 수정하지 않는다.** orca-data.json은 실행 중인 Orca가
  메모리 상태로 덮어쓰므로 외부 수정이 유실된다(07-22 실측). 어긋나 있으면
  Orca 설정 UI에서 바꾸라는 경고 행만 출력한다.

## 동작 규율

- **가시성 (2026-07-23 개정, v4)**: interactive launch에서는 가드 결과를
  **preflight 박스 안 맨 위 `notif guard` 행**으로 표시한다 (`✓ serena`,
  `✓ graphify global` 등과 같은 `Item`). 값은 clean이면 `clean`(status done),
  드리프트가 있으면 `N repaired · M warning(s)` 요약(경고가 하나라도 있으면
  status warn, 아니면 done), 가드 자체 오류면 `check failed — launch continues`
  (warn). 실제 수리/경고가 있을 때의 상세 행은 **박스 아래**에 이어 출력한다
  (버퍼에 받아 박스 렌더 후 flush). 박스가 그려진 뒤에 이후 선택지가 이어지므로
  순서는 자연히 보장된다. **비대화식 launch는 기존 silent-when-clean 유지**
  (박스 없음 — 가드가 직접 stdout에 위임, 수리/경고 시에만 행 출력).
  (v3의 박스 밖 스피너 행 방식은 폐기 — 박스 안 정적 항목으로 대체.)
- **수리/경고 상세는 항목당 한 줄**, 기존 `ui.render_inline_row` 사용
  (수리 = `done`, 경고 = `warn`). 예:
  - `↳ notif guard  codex notify 재주입 제거 (~/.codex/config.toml)`
  - `⚠ notif guard  orca 알림 토글이 어긋남 — Orca 설정 › Notifications에서 조정 필요`
- **수리 절차 (순서 엄수)**: ① 원본 읽기(내용+mtime+size 기록) → ② 수리본을
  같은 디렉토리의 임시 파일에 작성 → ③ **임시 파일을** `tomllib`/`json`으로
  파싱 검증, 실패 시 임시 파일 삭제 + 경고 (원본 무접촉) → ④ 원본의
  mtime+size가 ①과 다르면 동시 수정 감지: 처음부터 1회 재시도, 재차 다르면
  경고로 강등 → ⑤ 원본 파일 모드를 임시 파일에 복사(`chmod`) → ⑥ `os.replace`.
- **동시 writer 한계 문서화**: 같은 파일을 ChatGPT 앱, orca daemon(4개 키
  역전파), codex 자신이 쓴다. ④의 재확인으로 창을 줄이지만 원리상 제거는
  불가하며, orca 미러 동작과의 순서에 따라 같은 launch 안에서 수리가
  되돌려질 수 있다 — 다음 launch에서 다시 수리되는 최종 수렴을 보장하는
  것이 이 가드의 목표다.
- **best-effort**: 가드의 어떤 실패(파일 없음, 파싱 불가, 권한)도 launch를
  중단시키지 않는다. 예외는 삼키고 경고 행으로 강등한다.
- **수리 방식은 라인 보존**: TOML 전체 재직렬화 금지(주석·포맷 파괴).
  라인 단위 치환/삽입만 한다.
  - #1: **첫 테이블 헤더(`[`로 시작하는 줄) 이전 구간**의 `notify = ...` 줄만
    대상으로 한다 (`[mcp_servers.computer-use]`의 SkyComputerUseClient 경로
    줄 오폭 방지). 값이 `[]`가 아니면 무엇이든 `notify = []`로 치환하고
    제거한 내용을 로그 행에 포함한다.
  - #3: 해당 hooks.state 블록에 `enabled = false` 줄이 없으면 블록 끝에 삽입
    (`trusted_hash` 등 기존 줄 보존). 블록 자체가 없으면 EOF에 헤더 +
    `enabled = false` 추가. guardian 판단은 그 config 파일 자신의
    `approvals_reviewer` 값.
  - #4: JSON은 `json.load` → 키 수정 → `json.dump(indent=2,
    ensure_ascii=False)` (claude settings.json은 기계 생성 파일이라
    재직렬화 허용).

## 선행 검증 과제 (구현 첫 단계)

**`enabled = false`가 훅 실행을 실제로 억제하는지 e2e로 1회 확정한다.**
현재 근거는 codex 바이너리의 `HookStateToml{enabled, trusted_hash}` 필드
존재와 orca asar가 이 필드를 왕복시킨다는 정황뿐이다. 방법: scratch
`CODEX_HOME`에 orca hooks.json을 그대로 복사(핸들러 내용이 같으면
trusted_hash가 동일하다는 것이 active/legacy 홈 비교로 확인됨)하고 기존
hooks.state 엔트리(키 경로만 scratch로 치환)를 seed한 뒤, `ORCA_AGENT_HOOK_PORT`
등 환경변수를 로컬 리스너로 향하게 해 `codex exec`를 2회(enabled=false
유/무) 실행 — 리스너에 POST가 도달하는지 비교한다. 억제가 확인되지 않으면
#3 불변식을 폐기하고 대안(예: hooks.json 경로 리다이렉트)으로 회귀한다.

**검증 결과 (2026-07-23):** scratch `CODEX_HOME`에서 `codex exec`를 2회
실행해 확정. run A(baseline, session_start enabled 미설정): 리스너 POST
3건(SessionStart/UserPromptSubmit/Stop 각 1). run B(session_start
hooks.state에 `enabled = false` 추가): POST 2건(UserPromptSubmit/Stop만) —
SessionStart POST가 사라졌을 뿐 아니라 codex 자신의 stderr 트레이스에서도
`hook: SessionStart` 줄 자체가 나타나지 않아, 억제가 POST 실패가 아니라
훅 호출 자체의 스킵임을 확인. **판정: 억제 확인 — 불변식 #3 유효, Task 4
그대로 진행.** (부수 확인: 실행 환경에 `ORCA_AGENT_HOOK_ENDPOINT`가 이미
설정돼 있으면 codex-hook.sh가 이를 우선해 명시적으로 지정한
`ORCA_AGENT_HOOK_PORT`/`TOKEN`을 덮어쓴다 — 재현 시 해당 변수를 빈 문자열로
같이 넘겨야 함.)

## 통합 지점

`serena_agent_launcher.py`의 `_main_v2` **함수 최상단** — interactive 분기와
preflight abort early-return보다 앞, 모든 child 실행 경로(`_launch_bare_child`
2곳, `Popen` 1곳)의 공통 조상 위치. client 종류(claude/codex)와 무관하게
launch당 한 번 실행한다.

## 모듈 구조

- `local_dev/serena_mcp_management/notification_guard.py`
  - 불변식 정의(선언적 리스트)와 점검·수리 순수 함수들
  - 진입점: `run_notification_guard(*, stream) -> list[GuardAction]`
    (수행한 수리/경고의 구조화된 기록을 반환 — 테스트와 UI 출력 공용)
  - 파일 경로 발견 함수는 홈 디렉토리 주입 가능하게 (`home: Path` 파라미터)
    — 테스트가 tmp_path로 대체
- `local_dev/tests/test_notification_guard.py` — 짝 테스트 (TDD)

## 테스트 계획

tmp_path에 가짜 홈 구조를 만드는 픽스처를 둔다. **가짜 orca 관리 홈 경로에는
공백을 포함시킨다** (`Application Support` 대응 검증 — 공백 없는 경로로
우회되면 안 됨). 각 관리 홈에는 hooks.json도 넣는다 (#3 키 도출용).

1. 전부 정상 → 수리 0건, 출력 0줄
2. notify에 SkyComputerUseClient 재주입(`--previous-notify` 변형 포함) →
   해당 줄만 `notify = []`로, 주석/다른 줄/`[mcp_servers.computer-use]`의
   경로 줄 보존
3. notify에 미지의 프로그램 → 역시 `[]`로 수리 + 제거 내용 로그
4. (폐기 2026-07-24) codex `notification_condition`은 가드 비관여 —
   `"always"`여도 무접촉임을 검증
5. permission_request 블록에서 `enabled = false` 제거됨 → 재삽입
6. permission_request 블록 자체가 없음 → hooks.json 인덱스로 키 도출해 생성;
   hooks.json의 핸들러가 `:0:1`에 있는 변형 → 키가 따라감
7. hooks.json 부재 → #3 조용히 건너뜀(경고 없음, 공허 충족); 파싱 불가 →
   건너뛰고 경고
8. `approvals_reviewer = "user"`인 config → #3 수리 안 함; 기존
   `enabled = false` 잔존 시 경고만
8b. (#6) subagent_start/subagent_stop 블록에서 `enabled = false` 누락 →
    재삽입/생성; `approvals_reviewer = "user"`여도 수리(무조건 적용);
    hooks.json에 Subagent 이벤트가 없으면 공허 충족
8c. user 홈(`~/.codex`)에 hooks.json 존재 → #3·#6이 user config에도 적용;
    부재 → 공허 충족 (기존 user 홈 동작 유지)
9. claude `preferredNotifChannel` 드리프트 → 수리 (`ensure_ascii=False`
   왕복 확인)
10. orca 토글 어긋남(master `enabled=false` / `agentTaskComplete=false` /
    `suppressWhenFocused=true`) → 수리 없이 경고 액션만; `terminalBell`은
    어떤 값이든 무액션; `profiles/*/` glob 다중 프로파일 커버
11. 임시 파일 파싱 실패(인위적 파손 주입) → 원본 무접촉 + 경고
12. 원본 mtime/size가 수리 중 변경됨 → 1회 재시도, 재차 변경 시 경고 강등
13. 대상 파일 부재 → 무시(에러 없음)
14. launch 비중단: 가드 내부 예외 → 반환은 정상, 경고 액션 포함

## 비범위

- 비대화식/allowlist 밖 launch 경로의 가드 (커버리지 절 참조)
- orca-data.json 자동 수리 (실행 중 클로버 — 경고만)
- `~/.orca/agent-hooks/*.sh`, 관리 홈 `hooks.json` 재패치 (orca가 재설치하는
  파일 — hooks.state 층이 대체)
- user config에 #3 template 엔트리 유지 (orca가 seed 시 제거 — 배경 3)
- codex `approvals_reviewer` 값 자체의 관리 (사용자 소관)
- launcher 밖 주기 실행(cron/LaunchAgent)

## 롤아웃

1. 구현 후 `make -C local_dev install-shim`으로 runtime mirror 반영.
2. **스모크 검증 (필수)**: 아무 codex config에 드리프트를 인위 주입한 뒤
   실제 Orca 패널에서 `codex`를 한 번 실행해 가드의 수리 로그 한 줄이
   출력되는지 확인 — "orca 패널이 shim을 통과한다"는 커버리지 전제의 실증.
3. `local_dev/README.md`에 가드 섹션 추가.
