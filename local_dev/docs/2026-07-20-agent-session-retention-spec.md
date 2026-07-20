# Agent Session Retention Spec

작성일: 2026-07-20

이 문서는 내부 도구인 `local_dev/serena_mcp_management` launcher가
Codex와 Claude Code의 저장된 세션을 5일 보존 정책으로 정리하는 목표 동작을
정의한다. `local_dev/` 전용 기능이며 공개 `dotsync` CLI와 Homebrew formula에는
포함하지 않는다.

## 목표

- 사용자가 launcher-managed interactive `codex`를 실행할 때 기본 Codex 홈과
  Orca-managed Codex 홈을 포함한 알려진 모든 Codex 홈에서 5일 넘게 사용되지
  않은 세션을 정리한다.
- 사용자가 launcher-managed interactive `claude`를 실행할 때 현재 Claude
  설정 루트의 모든 프로젝트 세션에 Claude Code의 공식 5일 보존 정책을
  적용한다.
- 세션 파일 수가 늘어도 preflight 인벤토리 자체가 눈에 띄는 시작 지연을
  만들지 않게 한다.
- 세션과 함께 저장되는 메타데이터의 일관성을 각 CLI의 공식 삭제 경로에
  맡긴다.

## 범위 밖

- Codex memory와 Claude auto-memory의 검색, 초기화, 삭제
- 사용자가 명시적으로 보관한 Codex `archived_sessions`
- Orca의 `orchestration.db`, task/message/dispatch 기록
- Claude 인증, 계정, 캐시, command history
- 임의의 과거 `CODEX_HOME` 또는 `CLAUDE_CONFIG_DIR`을 디스크 전체에서
  추측해 찾는 기능
- 공개 `dotsync` 명령, README, formula의 변경

## 공식 저장소와 소유권

### Codex

Codex는 `CODEX_HOME` 아래의 `sessions/`와 상태 DB에 세션을 저장한다. Orca는
자체 managed Codex 홈을 만들고, 기본 `~/.codex/sessions`의 과거 세션을
hard link 우선, symbolic link fallback 방식으로 managed 홈에 연결한다.
따라서 같은 논리 세션이 두 Codex 홈에 동시에 나타날 수 있다.

Codex의 현재 공식 CLI는 `codex delete --force <SESSION_UUID>`를 제공한다.
그 기반인 `thread/delete`는 대상 thread의 rollout과 메타데이터뿐 아니라 그
thread가 만든 descendant thread도 함께 삭제한다. launcher는 JSONL이나
SQLite row를 직접 삭제하지 않고 이 공식 명령만 사용한다.

참고:

- [Codex app-server thread/delete](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
- [Orca Codex runtime home](https://github.com/stablyai/orca/blob/main/src/main/codex-accounts/runtime-home-service.ts)
- [Orca Codex session bridge](https://github.com/stablyai/orca/blob/main/src/main/codex/codex-session-bridge.ts)

### Claude Code

Claude Code는 현재 설정 루트의 `projects/<project>/<session>.jsonl`에 resumable
session을 저장한다. 설정 루트는 `CLAUDE_CONFIG_DIR`이 있으면 그 값이고,
없으면 `~/.claude`다. Orca는 현재 Claude transcript용 별도 설정 루트를
주입하지 않으므로 Orca 터미널에서 수동 실행한 Claude도 같은 저장소를 쓴다.

Claude Code는 startup retention sweep을 공식 제공한다. `cleanupPeriodDays`의
기본값은 30일이며, `--settings`로 실행별 추가 설정을 전달할 수 있다. launcher는
`cleanupPeriodDays: 5`를 추가 설정으로 전달하고 실제 삭제는 Claude Code에
맡긴다.

참고:

- [Claude Code storage and cleanup](https://code.claude.com/docs/en/claude-directory)
- [Orca Claude runtime paths](https://github.com/stablyai/orca/blob/main/src/main/claude-accounts/runtime-paths.ts)

## 적용되는 명령

정리 기능은 Serena lifecycle과 같은 launcher-managed interactive 호출에
적용한다.

- Codex: 인자 없는 `codex`, interactive `codex resume`, `codex fork`
- Claude: 인자 없는 `claude`, `claude -c`/`--continue`,
  `claude -r`/`--resume`

`codex exec`, `claude -p`, `--help`, `--version` 같은 non-interactive 또는
관리 명령은 실제 binary로 바로 전달한다. 사용자가 자신의 `--settings`를
명시한 Claude 호출도 인자 의미를 바꾸지 않기 위해 바로 전달한다.

## 공통 보존 기준

- 보존 기간은 정확히 `5 * 24시간`이다. 지역 날짜 경계가 아니라 파일의 epoch
  modification time과 현재 시각의 차이로 판정한다.
- cutoff와 같은 시각은 보존하고, cutoff보다 엄격하게 오래된 세션만
  삭제 후보로 삼는다.
- 정리 단위는 물리 JSONL 파일 한 개가 아니라 사용자가 resume하는 논리
  top-level session이다.
- 삭제 여부는 root와 모든 descendant 중 가장 최근 modification time으로
  판정한다. descendant가 최근에 활동했다면 전체 session group을 보존한다.
- 실행 중인 session, 스캔 중 변경된 session, 구조를 안전하게 해석할 수 없는
  session은 삭제하지 않는다.

## Codex 설계

### 검색할 Codex 홈

다음 후보를 canonical path로 정규화하고 중복을 제거한다.

1. 기본 `~/.codex`
2. 현재 process의 `CODEX_HOME`
3. Orca의 알려진 managed home
   `~/Library/Application Support/orca/codex-runtime-home/home`

각 홈의 `sessions/`만 검색한다. `archived_sessions/`는 사용자가 명시적으로
보관한 데이터로 보고 자동 정리에서 제외한다. 존재하지 않거나 읽을 수 없는
후보는 warning으로 기록하고 나머지 홈은 계속 처리한다.

### 빠른 인벤토리

각 JSONL은 전체를 parsing하지 않는다. 첫 번째 `session_meta` record에서 다음
식별 정보만 읽고 즉시 파일을 닫는다.

- session UUID
- parent thread UUID
- 저장된 cwd

mtime, device/inode, owner Codex home은 filesystem metadata로 수집한다. 같은
UUID 또는 같은 inode로 여러 홈에 보이는 hard-linked 파일은 하나의 논리
record로 합치되, 삭제에 필요한 owner home 목록은 유지한다. 인벤토리는
preflight 표시와 실제 cleanup에서 한 번만 만들고 재사용한다.

### 논리 session group

`parent_thread_id`를 따라 transitive descendant를 top-level root에 묶는다.
다음 경우는 자동 삭제하지 않고 warning 대상으로 둔다.

- 첫 `session_meta`가 없거나 손상됨
- UUID가 없거나 유효하지 않음
- parent가 어느 검색 대상 홈에도 없음
- parent cycle이 있음
- 같은 UUID가 서로 모순되는 parent를 가짐

UI의 `total`, `to delete`, `to keep`은 JSONL 개수가 아니라 top-level logical
session group 개수다. 이 수치는 현재 cwd가 아니라 모든 검색 대상 홈을
포괄한다.

### 실행 중 session 보호

Codex process가 오래 열려 있어 mtime만으로 active 여부를 판단할 수 없는
경우를 보호한다.

1. 인벤토리 시점에 macOS `lsof`로 열려 있는 rollout 파일의 device/inode를
   한 번 수집한다.
2. group의 root 또는 descendant 중 하나라도 열려 있으면 전체 group을
   보존한다.
3. 삭제 직전에 해당 group의 mtime, inode, open-file 상태를 다시 확인한다.
4. 최초 스캔 뒤 파일이 생기거나 바뀌었거나 열렸다면 그 group을 건너뛴다.
5. active-file 확인 자체가 실패하면 destructive cleanup은 fail closed하고
   그 실행에서는 Codex 세션을 삭제하지 않는다.

### 공식 삭제 절차

삭제 가능한 logical group마다 owner home 안에서 확인되는 최상위 UUID를
사용한다. 정상적인 home에는 global top-level root가 있으므로 명령 한 번으로
descendant가 함께 삭제된다. 다른 홈에서 root만 보이고 현재 home에는
descendant fragment만 남은 예외 상황에서는, 그 home에 존재하는 local
top-level fragment UUID 각각을 공식 삭제 명령으로 처리한다.

1. resolved real Codex binary가 `delete --force`를 지원하는지 한 번 확인한다.
2. 같은 group을 가진 owner home마다 환경의 `CODEX_HOME`만 해당 홈으로
   바꾸어 `codex delete --force <LOCAL_ROOT_UUID>`를 실행한다.
3. Orca bridge와 hard link로 공유된 group은 기본/source 홈을 먼저 삭제하고
   Orca managed 홈을 나중에 삭제한다. source 삭제가 실패하면 managed copy는
   삭제하지 않아 다음 실행에서 안전하게 재시도할 수 있게 한다.
4. owner home 하나 안에서는 명령을 순차 실행해 같은 state DB의 동시 write를
   피한다.
5. 명령 실패, timeout, unsupported CLI는 직접 파일 삭제로 fallback하지 않는다.
   warning을 표시하고 실제 Codex 실행은 계속한다.

공식 삭제가 성공한 뒤 별도 JSONL unlink나 SQLite 조작은 하지 않는다.

## Claude 설계

### 적용 범위

현재 Claude 설정 루트 아래의 모든 `projects/*`가 대상이다. 이는 현재 Claude
process가 `/resume`에서 볼 수 있는 모든 프로젝트 세션을 뜻한다. Orca 전용
Claude transcript 저장소는 없으므로 별도 Orca 경로를 추가하지 않는다.

### 네이티브 retention 사용

launcher는 실제 Claude binary를 실행할 때 다음과 동등한 추가 설정을 전달한다.

```text
--settings {"cleanupPeriodDays":5}
```

이 설정은 실행별로만 적용한다. `~/.claude/settings.json`이나 project
`.claude/settings*.json`은 수정하지 않는다. Claude Code가 startup에서 모든
project session을 sweep하고 parent session과 관련 subagent data를 자체 규칙으로
정리한다.

launcher는 Claude JSONL 또는 subagent directory를 직접 삭제하지 않는다.
따라서 Claude의 파일 배치나 메타데이터 계약이 바뀌어도 native cleanup이
진실 공급원이다.

### preflight 표시

preflight count는 현재 Claude 설정 루트의 top-level session JSONL만 `stat`해
계산한다. subagent JSONL은 parent와 함께 관리되므로 별도 session으로 세지
않는다. 이 값은 native sweep의 예상치이며 실제 삭제 결과의 진실 공급원은
Claude Code다.

## 메모리 정책

launcher는 이번 변경부터 Codex와 Claude memory를 검색하거나 삭제하지 않는다.
기존의 `reset all` 동작과 memory count row를 제거한다. preflight criteria에는
session retention만 표시한다.

- Codex memory: Codex 자체 동작에 맡긴다.
- Claude auto-memory: Claude Code 자체 동작에 맡긴다.

향후 memory retention은 저장 형식, last-used 의미, 제품별 native 정책을 별도
설계한 뒤 추가한다.

## preflight와 오류 표시

Codex 예시:

```text
· sessions    codex 58 total . 41 to delete . 17 to keep
· criteria    sessions: all known homes + inactive longer than 5d
```

Claude 예시:

```text
· sessions    claude 108 total . 74 native cleanup . 34 to keep
· criteria    sessions: all projects + native retention 5d
```

정상적인 삭제 대상 0건은 성공 상태다. 손상된 metadata, unreadable home,
active-file 검사 실패, 공식 CLI 삭제 실패, Claude native setting 주입 불가 등은
warning으로 요약하되 agent 실행 자체를 막지 않는다. 절대 조용히 raw deletion으로
전환하지 않는다.

## 성능 요구

- 같은 invocation에서 session tree를 두 번 전체 스캔하지 않는다.
- Codex JSONL은 첫 metadata record 이후 읽지 않는다.
- Claude는 top-level JSONL의 filesystem metadata만 조회한다.
- active-file 확인은 session별 process 실행이 아니라 한 번의 snapshot으로
  수집한다.
- cleanup 대상이 없는 정상 startup에서 session inventory가 기존 전체 JSONL
  parsing처럼 수 초 단위 지연을 만들면 회귀로 본다.

## 테스트 전략

모든 destructive test는 임시 `HOME`, 임시 `CODEX_HOME`, fake external CLI를
사용한다. 실제 사용자 세션은 테스트에서 읽거나 삭제하지 않는다.

### 단위 테스트

- default, active, Orca Codex home 검색과 canonical path dedup
- 첫 metadata record 이후의 큰/손상된 JSONL body를 읽지 않는 인벤토리
- root/descendant의 transitive grouping과 group 최신 mtime 판정
- UUID/inode 기반 cross-home dedup과 owner 보존
- orphan, cycle, conflicting parent의 fail-closed 처리
- `archived_sessions` 제외
- cutoff 경계와 정확한 5일 판정
- open rollout, scan 이후 변경, `lsof` 실패 시 보존
- source-before-Orca delete 순서와 source 실패 시 managed copy 보존
- 공식 delete unsupported/failure/timeout 시 raw unlink 미실행
- Claude `--settings` 인자 주입과 사용자 `--settings` 호출 bypass
- Claude 모든 project top-level session count와 subagent 제외
- memory 경로 미검색·미삭제
- preflight가 하나의 inventory snapshot을 표시와 cleanup에 재사용

### 통합 테스트

- fake Codex binary가 owner별 `CODEX_HOME`과
  `delete --force <ROOT_UUID>` 호출을 정확히 받는지 검증
- temporary Claude config에서 설치된 Claude Code가
  `cleanupPeriodDays: 5` 추가 설정으로 오래된 parent/subagent session을
  native cleanup하는지 안전하게 검증
- interactive resume/fork/continue는 launcher를 통하고 non-interactive
  command는 직접 전달되는지 shim 검증
- `local_dev` 전체 test suite와 기존 Serena lifecycle regression 검증

## 문서와 배포

구현 시 `local_dev/README.md`의 session cleanup 설명과 preflight 예시를 함께
갱신한다. 코드와 테스트가 통과한 뒤 `graphify update .`로 지식 그래프를
갱신하고, `make -C local_dev install-shim`으로 runtime copy와 managed zsh block을
한 번에 설치한다.

설치 과정 자체는 실제 세션 cleanup을 실행하지 않는다. 실제 cleanup은 다음
launcher-managed interactive `codex` 또는 `claude` 실행에서만 일어난다.
