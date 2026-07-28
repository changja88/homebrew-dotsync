# local_dev 테스트·레거시 코드 정리 설계

## 목표

`local_dev`의 현행 런타임 동작을 보장하는 최소 회귀망만 남기고, 다음 항목을
삭제한다.

- 현재 사용자 흐름에서 도달할 수 없는 레거시 프로덕션 코드
- 레거시 코드만을 위해 존재하는 테스트
- 같은 계약을 여러 계층에서 반복 검증하는 테스트
- private helper의 구현 순서나 표현을 고정하는 테스트
- RGB 값, gradient 모양, dataclass 기본값처럼 핵심 동작과 무관한 테스트
- 프로덕션에서 참조되지 않고 테스트만을 위해 남아 있는 함수와 타입

테스트 개수나 줄 수를 목표값으로 먼저 정하지 않는다. 삭제 후 남은 각
테스트는 아래의 “필수 테스트 판정 기준” 중 하나를 만족해야 한다.

## 현행 기준선

- `local_dev/tests`: 30개 테스트 파일, 614개 테스트
- 기준선 실행 결과: `614 passed in 18.01s`
- 프로덕션 코드: `local_dev/serena_mcp_management/`
- 런타임 진입점:
  - `serena_agent_launcher.main`
  - `serena_zsh_shim.main`
  - `serena_mcp.proxy.main`
  - `serena_mcp.watchdog.run_watchdog`

구조 분석 결과 Codex는 현재 `keep` 또는 확인된 `reset_all`만 선택할 수
있지만, 예전의 per-session `all_inactive` inventory 및
`cleanup_codex_inventory` 경로가 런처와 테스트에 남아 있다.
`_main_v2`에서 `delete_inactive`는 Claude만 반환하므로 다음 Codex 분기는
현행 사용자 흐름에서 도달할 수 없다.

- `_run_launch_prep_v2`의 Codex cleanup 분기
- `_run_explicit_session_cleanup_v2`의 Codex cleanup 분기
- `session_inventory.py`의 Codex rollout grouping/delete-plan 구현
- `session_cleanup.py`의 공식 `codex delete` 기반 개별 삭제 구현

## 범위

### 포함

- `local_dev/serena_mcp_management/`의 도달 불가능하거나 미참조인 코드
- `local_dev/tests/`의 비필수 테스트 함수, helper 및 파일
- 삭제된 내부 계약 때문에 필요한 import, 타입, 호출부 정리
- 현행 동작을 설명하는 `local_dev/README.md`가 실제 코드와 달라지는 경우의
  최소 수정

### 제외

- 공개 `dotsync` 런타임인 `lib/dotsync/`
- `Formula/dotsync.rb`
- 과거 의사결정 기록인 기존 specs/plans의 일괄 삭제
- 안정 런타임 미러인 `~/Desktop/dotsync_config/agent_launcher/`
- `~/.zshrc`
- 새 기능 추가, 출력 문구 변경, cleanup 정책 변경

`make -C local_dev install-shim`은 이 작업에서 실행하지 않는다. 개발
체크아웃 정리와 외부 안정 런타임 반영은 별도 상태 변경이다.

## 필수 테스트 판정 기준

다음 중 하나라도 만족하면 유지한다.

1. **파괴적 동작의 안전 불변식**
   - 허용된 root 밖을 삭제하지 않는다.
   - symlink를 따라가지 않는다.
   - wrong-type target과 불명확한 inventory는 fail closed로 처리한다.
   - 스캔 이후 path, fingerprint, process identity가 바뀌면 삭제하지 않는다.
   - 부분 mutation이 발생하면 성공으로 오인하지 않고 제한된 진단을 남긴다.
   - 관련 없는 config, auth, plugin, skill, automation, 사용자 파일을 보존한다.

2. **사용자가 관찰하는 런처 계약**
   - 기본 선택은 비파괴적인 Keep이다.
   - Codex 전체 reset은 두 단계 확인 후에만 실행된다.
   - reset 실패는 새 Codex launch를 중단한다.
   - Claude의 memory 선택, native 5-day retention 및 inactive-session 삭제가
     올바른 순서로 실행된다.
   - Ctrl+C, child exit code, bare-launch degrade 및 경고 요약이 보존된다.
   - notification guard가 interactive/non-interactive 경계에 맞게 실행된다.

3. **외부 경계의 호환성**
   - zsh shim이 관리 대상 명령과 bypass 명령을 올바르게 구분한다.
   - 설치/제거가 managed zshrc block 밖을 손대지 않는다.
   - Serena MCP registry, lease, proxy, watchdog 및 process identity의 핵심
     lifecycle 계약이 유지된다.
   - proxy가 요청/응답을 전달하고 금지된 DELETE를 upstream으로 보내지 않는다.
   - 외부 CLI 해석 규칙에서 direct binary와 허용된 fallback의 차이가 유지된다.
   - Homebrew formula가 `local_dev`를 설치하지 않는다.

4. **독립적인 오류 채널**
   - 같은 happy path의 반복이 아니라, 호출자가 별도로 처리해야 하는 실패
     모드를 검증한다.
   - 해당 실패가 상위 수준 테스트에서 같은 관찰 결과로 이미 충분히
     검증된다면 하위 helper 테스트는 삭제한다.

## 삭제할 레거시 프로덕션 경로

### Codex per-session cleanup

`session_inventory.py`에서 Claude inventory에 필요하지 않은 Codex 전용
타입과 함수들을 제거한다.

- `CodexSessionFile`
- `OwnerDeletePlan`
- `CodexCleanupTarget`
- Codex rollout 파일 수집, ancestry grouping, delete-plan 생성 함수
- `_scan_codex_inventory`
- Codex 전용 `snapshot_open_rollouts` 사용 경로

`session_cleanup.py`에서는 `cleanup_codex_inventory`와 그 전용 helper 및
subprocess 계약을 제거한다. Claude quarantine/no-follow 삭제 구현과
`claude_retention_args`는 유지한다.

`serena_agent_launcher.py`에서는 다음을 정리한다.

- `cleanup_codex_inventory` import
- `_run_launch_prep_v2`의 도달 불가능한 Codex 분기와 그 때문에 필요했던
  `real_binary` 인자
- `_run_explicit_session_cleanup_v2`의 도달 불가능한 Codex 분기와
  `real_binary` 인자
- Claude inactive cleanup 전에 불필요하게 real binary를 해석하는 흐름

Codex preflight count는 계속 `scan_codex_session_catalog`를 사용하고,
실제 삭제는 계속 `reset_all_codex_data`만 사용한다.

### 테스트만 사용하는 프로덕션 심볼

프로덕션 참조가 없고 현행 외부 계약이 아닌 다음 심볼을 제거한다.

- `ui.style_count`
- `agent_paths.effective_claude_config_dir`
- `diagnostics.LifecycleSnapshot`
- `diagnostics.snapshot_lifecycle`
- `watchdog.shutdown_if_no_leases`
- `registry.remove_lease`
- `registry.stale_lease_ids`
- `codex_reset._parse_codex_process_environment`
- `codex_reset._process_codex_environment`

삭제 직전에 전체 프로덕션 참조를 다시 검색한다. 새로운 참조가 발견되면 해당
심볼은 삭제 대상에서 제외한다.

## 테스트 정리 전략

### 통째로 삭제 가능한 파일

- `test_ui_state.py`: dataclass/enum의 구조를 그대로 반복한다.
- `test_ui_style.py`: exact palette와 contrast/gradient 상수를 고정한다.
- `test_ui_progress.py`: spinner의 핵심 종료·직렬화 계약은 launcher cleanup
  흐름 테스트에서 검증한다.
- `test_session_cleanup.py`: Codex per-session cleanup 제거 후 남는 Claude
  retention 계약은 child command/launcher 흐름 테스트가 검증한다.

`test_launcher_node_runtime.py`는 `test_node_preflight.py`,
`test_external_cli.py`, launcher preflight 테스트와 계약이 완전히 겹치는지
확인한 뒤 파일 삭제를 우선한다. 고유한 adapter 계약이 있으면 그 계약 하나만
launcher 테스트로 이동한다.

### 대폭 축소할 파일

- `test_launcher_phases.py`
  - Keep/reset confirmation, reset failure abort, Claude 선택 순서, cancellation,
    child exit, bare-launch degrade, notification guard 및 대표 preflight 경로를
    남긴다.
  - 동일한 graphify/installer 상태를 각 private helper와 `_main_v2`에서
    반복하는 테스트, exact progress 문구, 도달 불가능한 Codex cleanup 테스트는
    삭제한다.
- `test_codex_reset.py`
  - 전체 reset happy path, 보존 항목, runtime termination/respawn,
    symlink/wrong type, configured external roots, SQLite/global-state 검증을
    대표하는 테스트를 남긴다.
  - 동일한 root-validation 규칙을 config source별로 반복하는 조합은
    하나의 대표 계약으로 축소한다.
- `test_memory_management.py`
  - Codex/Claude discovery 대표 경로, symlink/path traversal, running process,
    immediate revalidation, partial failure를 남긴다.
  - 같은 validation 결과를 alias 표현이나 malformed 문자열별로 반복하는
    테스트는 축소한다.
- `test_claude_session_cleanup.py`와 Claude inventory 테스트
  - exact inactive bundle, active/open preservation, manifest/identity race,
    no-follow quarantine, partial mutation을 남긴다.
  - 동일한 fail-closed 결과를 내부 단계별로 반복하는 테스트는 축소한다.
- `test_notification_guard.py`
  - 전체 orchestrator의 clean/repair/error 경로, notify·hook·Claude 핵심
    invariant, atomic replace/concurrent write를 남긴다.
  - parser의 동일 변환을 quoting/header 배치별로 반복하는 테스트는 삭제한다.
- `test_ui_renderer.py`와 `test_ui_prompts.py`
  - 의미 있는 status marker, multiline layout, redraw/clear, default 선택,
    raw navigation 및 Ctrl+C terminal 복원을 남긴다.
  - exact RGB, banner texture, glyph별 gradient 및 단순 yes/no 동의어 조합은
    삭제한다.
- Serena MCP 하위 테스트
  - lifecycle 단위별 happy path와 안전 실패 모드를 남긴다.
  - 단순 dataclass persistence field, private delegation, 같은 parser 입력의
    표기 변형은 대표 사례로 축소한다.

### 테스트 helper 정리

테스트 함수 제거 후 다음 순서로 helper를 정리한다.

1. 남은 테스트에서 참조되지 않는 fixture/helper/class를 삭제한다.
2. 사용하지 않는 import와 상수를 삭제한다.
3. 한 테스트에서만 쓰이는 짧은 fixture는 읽기 쉬운 경우 테스트 안으로
   이동한다.
4. 위험한 filesystem/process 시나리오를 명확히 만드는 fixture는 공유 상태로
   유지한다.

## 동작 보존과 오류 처리

- 현행 도달 가능한 런처 분기의 반환값, 출력 의미, 파일 mutation 및 subprocess
  호출 순서는 바꾸지 않는다.
- 레거시 Codex per-session 함수는 compatibility shim 없이 삭제한다. 이
  디렉터리는 내부 도구이고 프로덕션 호출자가 없으며 현행 UI에서도 해당
  선택을 만들 수 없다.
- 테스트 삭제 중 현행 프로덕션 버그를 발견하더라도 이 작업에 기능 수정을
  섞지 않는다. 버그와 관련 테스트를 남기고 별도 작업으로 보고한다.
- 기존 미커밋 변경은 사용자 작업으로 취급한다. 삭제 대상과 겹치는 경우 현재
  working-tree 내용을 기준으로 필요한 현행 계약을 먼저 보존한다.

## 검증

구현 후 다음을 새로 실행한다.

1. 제거 심볼과 Codex per-session 경로의 잔여 참조 검색
2. `local_dev/serena_mcp_management` compile 검사
3. 남은 `local_dev` 전체 테스트
4. 공개 `dotsync` 전체 테스트
5. `git diff --check`
6. 변경 diff에서 기존 미커밋 작업의 비대상 변경이 보존됐는지 확인

기본 명령:

```bash
.venv/bin/python3 -m compileall -q local_dev/serena_mcp_management
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m pytest tests -q
git diff --check
```

## 완료 조건

- 모든 남은 테스트가 필수 테스트 판정 기준 중 하나에 해당한다.
- 도달 불가능한 Codex per-session inventory/cleanup 코드와 테스트가 없다.
- 테스트만 사용하는 것으로 확인된 프로덕션 심볼이 없다.
- 현행 Codex full reset, Claude cleanup, launcher, shim 및 Serena MCP 핵심
  계약을 검증하는 테스트가 남아 있다.
- `local_dev`와 공개 `dotsync` 전체 테스트가 통과한다.
- 테스트 파일 수, 테스트 수, 테스트 코드 줄 수의 전후 차이를 완료 보고에
  포함한다.
- 안정 런타임 미러와 `~/.zshrc`는 변경하지 않는다.
