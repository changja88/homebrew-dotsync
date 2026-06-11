# CLI self-install prompts — design spec (2026-06-11)

## 배경

launcher의 모든 설치 액션(`serena project create`, `graphify install/…`)은
`shutil.which()`로 PATH의 CLI를 전제했다. 그러나 이 머신에서 serena는 Claude
Code 플러그인이 uvx로만 실행해 PATH에 없고, graphify는 과거 `~/.local/bin`
설치본이 사라진 상태였다. 결과: **preflight의 모든 Yes 응답이 조용히 exit 2로
끝나는 무동작 사고** (Kingdom-Slave, 2026-06-11). 사용자가 본 "ACTIVE
PROJECT: None" 대시보드는 별개로, Claude 플러그인의 bare serena 인스턴스가
`web_dashboard_open_on_launch: true`로 자동 오픈한 탭이었다.

## 결정사항

### 1. CLI 해석 레이어 (`external_cli.py`)

| 함수 | 해석 순서 | 근거 |
|---|---|---|
| `graphify_command` | PATH → `~/.local/bin` | **uvx fallback 금지** — graphify는 자기 절대 경로를 프로젝트 hook(`.codex/hooks.json`)에 기록하므로 휘발성 uvx 캐시 경로가 박히면 캐시 정리 후 hook이 죽는다 (이번 사고의 원형). |
| `serena_oneshot_command` | PATH → `~/.local/bin` → uvx | run-and-wait(`project create`)는 uvx 래퍼가 안전. |
| `serena_server_command` | PATH → `~/.local/bin` | **uvx 금지** — uvx는 실제 서버를 자식으로 두므로 registry의 `server_pid`가 래퍼를 가리키고, same-scope orphan cleanup이 자기 서버의 자식을 죽인다 (ps 실측으로 확인: uv 래퍼와 serena가 별개 프로세스). |
| `serena_install_command` / `graphify_install_command` | `uv tool install …` argv (uv 없으면 None) | 영구 설치는 uv tool 표준 위치(`~/.local/bin`)로 — 기존 hook들의 하드코딩 경로와 일치. |

### 2. Self-install 프롬프트 (launcher)

- **serena**: `_run_serena_cli_install_v2` — `serena_server_command()`가 None이면
  Initialize 프롬프트 **직전**에 묻는다. default Yes.
- **graphify**: `_run_graphify_cli_install_v2` — graphify 4행(global/graph/
  integration/hook) 중 하나라도 missing **이고** CLI가 해석 안 될 때, graphify
  질문들 직전에 묻는다. 모든 행이 갖춰져 있으면 묻지 않는다 (제안할 액션이
  없는데 질문만 늘리지 않는다). default Yes.
- 설치 성공 판정은 `uv` exit 0 **그리고 재해석 성공** 둘 다 — uv가 0을
  돌려줘도 binary가 안 보이면 failed.
- 거절/실패/uv 부재 시 흐름은 끊기지 않는다: serena는 기존 degrade(`! serena
  unavailable` 경고 후 bare launch), graphify는 질문 전체 skip + 경고 행 하나.
- **TUI 불변**: 박스에 새 행을 추가하지 않는다. 질문은 기존 `confirm`
  화살표 선택 UI, 결과는 `render_inline_row` — 기존 문법 그대로. CLI가 이미
  해석되는 머신에서는 질문 자체가 나타나지 않아 동작 차이가 없다.

### 3. 검토 후 기각한 대안

- install-shim(Makefile)에서 설치: 셋업 1회로 끝나지만 운용 중 CLI가 사라진
  경우(이번 사고)를 복구하지 못한다.
- 묻지 않는 자동 설치: 외부 저장소 코드의 무단 영구 설치라 부적절.

## 검증

- `tests/test_external_cli.py` — 해석/설치 명령 빌더 단위 스펙.
- `tests/test_launcher_phases.py` — 프롬프트 phase 5상태(present/installed/
  declined/failed/unavailable), `_main_v2` 호출 순서(cli → init), graphify
  질문 gating, degrade 경로.
- 샌드박스 E2E(2026-06-11): PATH 없는 셸에서 4개 Yes 액션 전부 실제 성공
  (project.yml 생성, AGENTS.md+hooks.json, git hooks, `~/.codex/skills/graphify`).
