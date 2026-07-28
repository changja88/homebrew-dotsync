# Codex 자동 승인 검토 전환 설계

상태: 사용자 승인 · 작성 2026-07-28

## 목적

Codex가 `workspace-write` 샌드박스 경계를 벗어나려 할 때 매번 사용자에게
승인 프롬프트를 띄우는 대신 공식 `auto_review` 검토자가 요청을 평가하게 한다.
사용자가 작업 흐름에서 반복적으로 중단되지 않도록 하되 샌드박스, 쓰기 가능
경로, 네트워크 경계는 넓히지 않는다.

## 확인된 현행 동작

- 사용자 Codex 설정은 `approval_policy = "on-request"`,
  `approvals_reviewer = "user"`, `sandbox_mode = "workspace-write"`다.
- 런처의 `build_child_command()`는 scoped Serena MCP 주소만 `-c`로 추가하고
  승인 정책은 사용자 설정에서 그대로 상속한다.
- graphify AST 추출은 macOS 샌드박스 안에서 프로세스 세마포어 정보를 읽다가
  `PermissionError`가 발생했고, `on-request` 정책이 이를 사용자 승인
  프롬프트로 전환했다.
- Codex 공식 문서와 기존 Orca 실측 모두 `auto_review`가 TUI의 수동 승인
  프롬프트를 대체하지만 `PermissionRequest` 훅 자체는 계속 발화함을 보여
  준다. 이 훅을 그대로 두면 Orca가 실제 사용자 입력이 필요하지 않은 자동
  검토를 “입력 필요”로 잘못 알릴 수 있다.

## 결정

### 승인과 샌드박스

- `approval_policy = "on-request"`를 유지한다.
- `sandbox_mode = "workspace-write"`를 유지한다.
- `[sandbox_workspace_write]`의 `writable_roots`와 `network_access`를 유지한다.
- `approvals_reviewer`만 `"user"`에서 `"auto_review"`로 바꾼다.
- `danger-full-access`, `--yolo`, `approval_policy = "never"`는 사용하지 않는다.

따라서 자동 검토는 승인자를 바꿀 뿐 권한 경계를 넓히지 않는다. 검토가
거절되면 메인 에이전트는 더 안전한 방법을 찾고, 안전한 대안이 없을 때만
사용자에게 판단을 요청한다.

### 설정의 지속성

동기화 원본과 현재 실행 설정을 같은 값으로 유지한다.

- 동기화 원본: `~/Desktop/dotsync_config/codex/config.toml`
- 현재 실행 설정: `~/.codex/config.toml`

이 설정은 런처 전용 임시 override가 아니라 Codex 홈의 기본값이다. 따라서
런처로 여는 대화형 세션뿐 아니라 같은 Codex 홈을 읽는 직접 실행과
`codex exec`에도 `auto_review`가 적용된다. 호출별 `-c` override로 다시
`user`를 선택하는 동작과 그때의 Orca 승인 알림 복구는 이번 범위에 포함하지
않는다.

두 파일의 설명 주석도 `auto_review`의 실제 의미와 비용을 반영한다. 자동
검토가 추가 모델 호출을 사용한다는 점, 샌드박스 경계를 확장하지 않는다는
점, `approval_policy = "never"`에서는 검토 요청 자체가 없다는 점을 명시한다.

### 알림 가드

`notification_guard.py`는 승인자를 다음 두 부류로 판정한다.

- 사용자 검토자: `"user"`
- 자동 검토자: 공식 `"auto_review"`와 기존 설정 호환용
  `"guardian_subagent"`

자동 검토자일 때 `PermissionRequest` 훅의 `enabled = false`를 launch마다
복구한다. 자동 검토가 이미 요청을 처리하므로 이 훅은 Orca 관점에서 가짜
“입력 필요” 신호다.

사용자 검토자일 때는 기존 동작을 보존한다. `PermissionRequest` 훅을 자동으로
끄지 않으며, 과거의 `enabled = false`가 남아 있으면 실제 승인 알림이
사라졌다는 경고를 계속 표시한다.

`SubagentStart`, `SubagentStop`, `PostToolUse` 알림 억제 규칙과 `notify = []`
복구 규칙은 변경하지 않는다.

## 범위

포함:

- Codex 동기화 원본과 현재 실행 설정의 reviewer 변경 및 주석 갱신
- notification guard의 자동 검토자 판정 갱신
- 관련 단위 테스트와 README/notification guard 문서 갱신
- 검증 후 `make -C local_dev install-shim`으로 런처 실행본 반영

제외:

- 샌드박스 모드 변경
- 쓰기 가능 루트 추가
- 네트워크 정책 변경
- 로컬 `[auto_review].policy` 커스터마이징
- Claude 승인 정책 변경
- 개별 명령 prefix rule 추가
- `local_dev`와 무관한 `dotsync` 공개 CLI 변경

## 오류 처리

- Codex 버전 또는 관리 정책이 `auto_review`를 허용하지 않으면 자동으로
  `user`나 `never`로 강등하지 않는다. 설정 오류를 그대로 노출해 보안 정책이
  조용히 바뀌지 않게 한다.
- notification guard의 파일 파싱·수리 실패는 기존처럼 launch를 막지 않고
  경고로 표시한다.
- 자동 검토 거절은 Codex의 공식 거절 흐름을 따른다. 런처가 거절을 우회하거나
  같은 명령을 무조건 재시도하지 않는다.

## 테스트

1. `approvals_reviewer = "auto_review"`인 Codex 설정에서
   `PermissionRequest` 훅이 비활성화되는 회귀 테스트를 추가한다.
2. `"guardian_subagent"` 호환 동작이 유지되는지 확인한다.
3. `"user"`일 때 훅을 끄지 않고, 잘못 비활성화된 훅을 경고하는 기존 테스트를
   유지한다.
4. `notify`와 subagent/PostToolUse 훅 불변식 테스트가 계속 통과하는지
   확인한다.
5. notification guard 대상 테스트를 먼저 실행한 뒤 `local_dev` 전체 테스트를
   실행한다.
6. 동기화 원본과 현재 설정을 TOML로 파싱해 두 곳 모두
   `approvals_reviewer = "auto_review"`인지 확인한다.

## 완료 조건

- 런처로 시작한 새 Codex 세션에서 승인 요청이 사용자 프롬프트 대신
  Auto-review로 전달된다.
- `workspace-write` 샌드박스와 기존 writable roots가 그대로다.
- 자동 검토 요청 때문에 Orca의 가짜 “입력 필요” 알림이 발생하지 않는다.
- 실제 사용자 검토 모드의 승인 알림 안전장치는 퇴행하지 않는다.
- 관련 테스트와 `local_dev` 전체 테스트가 통과한다.
- 런처의 안정 실행본이 갱신된다.
