# Serena MCP Lifecycle Spec

작성일: 2026-05-10

이 문서는 내부 도구인 `local_dev/serena_mcp_management` launcher가
Serena MCP 서버를 어떻게 시작, 공유, 정리해야 하는지 정의한다.

이 문서는 목표 lifecycle spec이다. 일부 항목은 현재 구현되어 있고, 일부는
아직 구현 gap으로 남아 있다. 현재 구현과 목표 상태의 차이는 `현재 Known Gap`
섹션에 명시한다.

목표는 다음과 같다.

- `codex`와 `claude` 세션별 Serena MCP 서버를 안전하게 관리한다.
- 같은 프로젝트의 여러 `codex` 또는 여러 `claude` 세션은 서버를 공유한다.
- 마지막 세션이 끝나면 해당 scope의 Serena MCP 서버를 정리한다.
- registry가 깨지거나 프로세스 상태와 어긋나도 다음 실행에서 복구한다.
- 다른 프로젝트나 다른 클라이언트의 Serena MCP 서버는 건드리지 않는다.

## 전제 조건

이 문서는 다음 운영 전제를 둔다.

- 이 lifecycle의 대상은 interactive no-argument `codex` 또는 `claude` shell
  명령이다.
- 이 환경에서 Serena MCP 서버는 위 `codex` 또는 `claude` launcher flow를
  통해서만 시작된다.
- 사용자가 `serena start-mcp-server`를 직접 실행하거나, 다른 도구가 같은
  project/context의 Serena MCP 서버를 별도로 띄우는 흐름은 지원하지 않는다.
- argument가 있는 `codex ...` / `claude ...` 호출이나 non-interactive 호출이
  shim에서 real binary로 직접 우회되는 경우는 이 lifecycle의 대상이 아니다.
- 따라서 현재 scope와 같은 `--project`, 같은 `--context`를 가진
  `serena start-mcp-server` process는 launcher-managed process로 간주한다.
- 이 전제는 cleanup 로직을 단순하게 만든다. 같은 scope의 registry 없는
  upstream Serena process는 ambiguous manual process가 아니라 orphan
  candidate다.

## Scope

Serena MCP 서버는 다음 scope 단위로 관리한다.

```text
scope = (canonical project_root, client_type)
```

예시:

```text
(/Users/hyun/Desktop/Kingdom-Server, codex)
(/Users/hyun/Desktop/Kingdom-Server, claude)
(/Users/hyun/Desktop/Other-Project, codex)
```

위 세 항목은 모두 서로 다른 scope다. 한 scope의 cleanup이나 reuse가 다른
scope에 영향을 주면 안 된다.

## 용어

- `launcher`: shell의 `codex` 또는 `claude` 함수가 실행하는 wrapper process.
- `child agent`: launcher가 실행하는 실제 `/opt/homebrew/bin/codex` 또는
  `claude` process.
- `upstream Serena server`: 실제 `serena start-mcp-server` process.
- `proxy`: child agent가 접속하는 local streamable HTTP proxy.
- `registry`: scope별 상태 파일.
  `<project>/.serena/dotsync-mcp/<client>/registry.json`에 저장된다.
- `lease`: registry에 기록되는 하나의 살아 있는 launcher session.
- `heartbeat`: 살아 있는 launcher가 lease를 주기적으로 갱신하는 동작.
- `watchdog`: stale lease를 제거하고, 사용되지 않는 서버를 종료하는
  background process.
- `managed server`: 이 launcher flow가 관리하는 Serena MCP 서버.
- `orphan server`: 살아 있지만 유효한 live launcher lease가 없는 managed
  Serena MCP 서버.
- `stale registry`: 죽었거나, 잘못됐거나, unhealthy하거나, 더 이상 소유권을
  증명할 수 없는 process를 가리키는 registry 상태.

## 핵심 불변식

1. 하나의 scope에는 healthy managed Serena MCP 서버가 최대 1개만 있어야 한다.
2. 하나의 scope에서는 여러 `codex` 세션 또는 여러 `claude` 세션이 같은
   서버를 공유할 수 있다.
3. `codex`와 `claude`는 같은 프로젝트라도 서로 Serena MCP 서버를 공유하지
   않는다. 두 클라이언트는 별도 scope다.
4. scope에 live lease가 1개 이상 있으면 managed server는 살아 있어야 한다.
5. scope에 live lease가 0개이면 managed server는 종료되어야 한다.
6. 한 프로젝트의 cleanup은 다른 프로젝트의 서버를 종료하면 안 된다.
7. 한 client type의 cleanup은 다른 client type의 서버를 종료하면 안 된다.
8. child agent에는 upstream Serena MCP URL이 아니라 proxy MCP URL을 전달한다.
9. registry는 조정과 최적화를 위한 상태 파일이지 유일한 진실 공급원이
   아니다. startup은 registry와 실제 process 상태를 reconcile해야 한다.
10. registry 상태와 process 상태가 어긋나면 해당 scope는 위 불변식으로
    수렴해야 한다.

## 정상 Lifecycle

### Scope의 첫 세션

```text
사용자가 interactive no-argument codex 또는 claude 실행
launcher가 project_root와 client_type 추론
launcher가 lease 생성
launcher가 기존 scope 상태 reconcile
launcher가 upstream Serena server 시작
launcher가 proxy 시작
launcher가 health check 대기
launcher가 server_pid, proxy_pid, URL, lease를 registry에 기록
launcher가 watchdog 실행 보장
launcher가 heartbeat 시작
launcher가 proxy MCP URL을 넘겨 child agent 시작
```

### 같은 Scope의 추가 세션

```text
사용자가 같은 project/client scope에서 두 번째 codex 또는 claude 실행
launcher가 기존 scope 상태 reconcile
launcher가 healthy registered server 1개 확인
launcher가 두 번째 lease 추가
launcher가 기존 proxy MCP URL 재사용
```

live lease가 남아 있는 동안 기존 server는 재시작되거나 종료되면 안 된다.

### 세션 종료

```text
child agent 종료
launcher가 heartbeat 중지
launcher가 자기 lease만 제거
다른 lease가 남아 있으면:
  proxy와 upstream server 유지
남은 lease가 없으면:
  proxy 종료
  upstream Serena server 종료
  registry 제거
```

## Reconciliation 규칙

Reconciliation은 scope-local 작업이다. 실제 process를 조회할 수는 있지만,
현재 scope에 해당하는 process에만 조치해야 한다.

### Process Match

실행 중인 upstream Serena process가 어떤 scope와 일치하려면 아래 조건을
모두 만족해야 한다.

- command에 `serena start-mcp-server`가 포함되어 있다.
- `--project`가 canonical `project_root`와 정확히 같다.
- `--context`가 mapped client context와 정확히 같다.
  - `codex` -> `codex`
  - `claude` -> `claude-code`

다른 프로젝트나 다른 context의 process는 out of scope다.

구현은 가능한 경우 argv 수준 정보를 사용해 `--project <path>`와
`--project=<path>`, `--context <value>`와 `--context=<value>`를 파싱해야 한다.
command text만 사용할 수 있다면 quoting, 공백 포함 path, truncated output,
누락된 option처럼 정확한 scope 판별이 어려운 경우에는 fail closed 해야 한다.
즉, 애매한 process는 종료하지 않는다.

### Healthy Registered Server

registry record가 healthy하려면 아래 조건을 모두 만족해야 한다.

- `project_root`가 현재 canonical project root와 같다.
- `client_type`이 현재 client type과 같다.
- `server_pid`가 살아 있다.
- `proxy_pid`가 존재하고 살아 있다.
- proxy MCP endpoint가 initialize 요청에 응답한다.
- Serena dashboard가 기대한 active project를 보고한다.
- live lease가 있거나, 새 launcher가 attach 중인 상태다.

### Startup Reconciliation

startup에서 새 서버를 시작하기 전에 다음 순서로 reconcile한다.

1. 현재 scope의 registry를 읽는다.
2. registry record의 `project_root` 또는 `client_type`이 현재 scope와 다르면
   wrong-scope record로 본다. 이 경우 registry만 비우고 기록된 PID는 절대
   종료하지 않는다.
3. registry가 healthy server를 가리키면 재사용한다.
4. registry가 현재 scope의 unhealthy server를 가리키면 기록된 proxy/upstream PID가 살아
   있을 경우 종료하고 registry를 비운다.
5. 실제 upstream Serena process 중 현재 scope와 같은 process를 찾는다.
6. healthy registered server가 아닌 same-scope orphan 후보를 종료한다.
7. scope에 충돌하는 orphan server가 없어진 뒤에만 새 서버를 시작한다.

### Watchdog Reconciliation

watchdog은 주기적으로 다음 작업을 수행한다.

1. 현재 scope의 registry를 읽는다.
2. registry record의 `project_root` 또는 `client_type`이 현재 scope와 다르면
   wrong-scope record로 본다. 이 경우 registry만 비우고 기록된 PID는 절대
   종료하지 않는다.
3. launcher process identity가 더 이상 일치하지 않는 stale lease를 제거한다.
4. launcher identity가 여전히 일치하는 stale lease는 refresh한다. macOS
   sleep/wake로 heartbeat와 watchdog이 함께 멈춘 상황을 오탐하지 않기 위해서다.
5. 남은 lease가 없으면 기록된 proxy/upstream PID를 종료하고 registry를 비운다.
6. registry record가 없어지면 watchdog도 종료한다.

### Exit Reconciliation

정상 launcher 종료 시 다음 순서를 따른다.

1. 현재 launcher의 lease만 제거한다.
2. sibling lease가 남아 있으면 server를 유지한다.
3. 남은 lease가 없으면 기록된 proxy/upstream PID를 종료하고 registry를 비운다.
4. 다른 scope의 process는 종료하지 않는다.

exit cleanup은 같은 scope에 한해 orphan cleanup을 수행할 수 있다. 다만
live sibling lease가 남은 healthy registered server는 반드시 보존해야 하며,
다른 scope process는 건드리지 않는다. registry 밖 same-scope orphan 정리는
startup reconciliation에서 우선 수행한다.

모든 cleanup 경로는 동일한 termination primitive를 사용해야 한다. 기본 동작은
process group에 SIGTERM을 보내고, 짧게 대기한 뒤 살아 있으면 SIGKILL로
escalate하는 것이다. process group kill이 권한 문제로 실패하면 individual
PID kill로 fallback한다.

## Edge Case와 기대 동작

| Case | 기대 동작 |
|---|---|
| 프로젝트에서 첫 `codex` 실행 | `codex` scope Serena server와 proxy를 1개 시작한다. |
| 같은 프로젝트에서 두 번째 `codex` 실행 | 같은 `codex` scope server를 재사용하고 lease를 추가한다. |
| `codex`가 실행 중일 때 첫 `claude` 실행 | 별도 `claude-code` scope server를 시작하고 `codex`는 건드리지 않는다. |
| 다른 `codex` lease가 남은 상태에서 `codex` 하나 종료 | 종료한 lease만 제거하고 server는 유지한다. |
| 마지막 `codex`가 정상 종료 | proxy, upstream Serena, registry 순서로 정리한다. |
| child agent가 non-zero code로 종료 | 그래도 lease release와 정상 shutdown 규칙을 적용한다. |
| launcher가 SIGINT/SIGTERM/SIGHUP 수신 | 필요하면 child를 종료하고 `finally`에서 lease release를 수행한다. |
| registry write 후 launcher가 `kill -9`로 종료 | registry와 lease가 일시적으로 남는다. watchdog이 timeout 후 stale lease를 제거하고 server를 종료해야 한다. |
| registry write 전에 launcher가 종료 | 다음 startup reconciliation이 same-scope orphan upstream process를 찾아 종료해야 한다. |
| registry write 후 watchdog 시작 전에 launcher가 종료 | 다음 launch가 healthy server를 재사용하거나 watchdog을 다시 시작해야 한다. |
| session이 남아 있는데 watchdog이 죽음 | 같은 scope의 다음 launcher가 watchdog을 다시 시작하고 live lease는 보존한다. |
| watchdog이 죽고 모든 launcher도 종료 | registry가 다음 startup reconciliation 전까지 stale 상태로 남을 수 있다. |
| registry가 죽은 `server_pid`를 가리킴 | stale registry를 비우고 새 server를 시작한다. |
| registry의 `proxy_pid`는 죽었고 upstream만 살아 있음 | upstream을 종료하고 registry를 비운 뒤 clean server/proxy pair를 시작한다. |
| registry JSON이 깨짐 | registry를 신뢰하지 않는다. registry 안의 PID는 종료하지 않고, process scan으로 확인된 same-scope orphan만 reconcile한다. |
| registry의 project path가 현재 scope와 다름 | wrong-scope registry로 보고 registry만 비운다. 기록된 PID는 종료하지 않는다. |
| registry의 client type이 현재 scope와 다름 | wrong-scope registry로 보고 registry만 비운다. 기록된 PID는 종료하지 않는다. |
| 같은 scope upstream server가 있는데 registry가 없음 | orphan 후보로 보고 새 server 시작 전에 종료한다. |
| 같은 scope upstream server가 여러 개 있음 | healthy registered server가 있으면 보존하고, unregistered same-scope 후보는 종료한다. |
| 다른 project upstream server가 있음 | 현재 scope에서 절대 종료하지 않는다. |
| 같은 project의 다른 client upstream server가 있음 | 현재 scope에서 절대 종료하지 않는다. |
| sleep/wake로 heartbeat가 timeout보다 오래 멈춤 | launcher identity가 여전히 일치하면 lease를 refresh하고 server를 유지한다. |
| PID가 다른 process에 재사용됨 | stale lease 보존 여부는 PID만 보지 말고 process identity를 비교한다. |
| startup 중 health check 실패 | 해당 attempt에서 시작한 proxy/upstream을 retry 전에 정리한다. |
| dashboard가 wrong active project를 보고 | 해당 server는 현재 scope에서 unhealthy로 보고 교체한다. |
| proxy MCP endpoint는 죽었지만 upstream은 응답 | child에게 broken URL을 주지 않도록 server/proxy pair 전체를 교체한다. |
| startup 중 port collision 발생 | 새 port로 retry하고 실패 attempt의 partial process를 정리한다. |
| process group kill이 PermissionError | individual PID kill로 fallback한다. |
| SIGTERM으로 process가 종료되지 않음 | 짧게 대기한 뒤 SIGKILL로 escalate한다. |
| proxy PID가 PID reuse로 unrelated live process를 가리킴 | 가능하면 PID만 믿지 말고 process identity를 확인한다. |
| 같은 project/context의 registry 없는 `serena start-mcp-server`가 살아 있음 | 운영 전제상 launcher-managed orphan으로 보고 startup reconciliation에서 종료한다. |

## Ownership Policy

운영 전제상 같은 scope의 Serena MCP 서버는 launcher가 관리한다. 따라서
같은 scope process에는 엄격하게 cleanup 정책을 적용하고, 다른 scope
process는 보수적으로 무시한다.

ownership evidence:

- 같은 canonical project root
- 같은 mapped Serena context
- command가 `serena start-mcp-server`
- 현재 healthy registered server가 아님

process ownership 판별은 fail closed 원칙을 따른다. project/context를 정확히
파싱할 수 없거나 canonical path 비교가 불가능하면 같은 scope로 간주하지 않고
종료하지 않는다.

목표 정책은 다음과 같다.

```text
same scope + healthy registered server + live lease 있음 => preserve/reuse
same scope + registry 없음 또는 unhealthy registry => cleanup
same scope + extra upstream server => cleanup
different scope => ignore
```

## Diagnostics

launcher는 수동 `ps` 추적 없이 lifecycle 상태를 볼 수 있어야 한다.

유용한 diagnostic field:

- scope project root
- scope client type
- registry path
- registered server PID
- registered proxy PID
- registered watchdog PID
- lease count
- stale lease count
- live launcher identities
- same-scope orphan candidates
- reconciliation 중 수행한 action

수동 확인 명령:

```bash
ps -axo pid,ppid,command | rg 'serena start-mcp-server'
lsof -nP -iTCP -sTCP:LISTEN | rg '<port>'
```

## Test Matrix

lifecycle 구현은 최소한 아래 테스트를 가져야 한다.

1. healthy registry server 재사용.
2. 한 scope 안의 multiple lease.
3. 마지막 lease 종료 시 shutdown 순서: proxy 먼저, upstream 나중.
4. 죽은 registered upstream cleanup.
5. 죽은 registered proxy cleanup.
6. startup에서 registry 없는 same-scope orphan cleanup.
7. 같은 project의 other-client server 보존.
8. 다른 project server 보존.
9. 기록된 watchdog PID가 죽었을 때 watchdog restart.
10. stale dead lease 제거.
11. stale live identity-matched lease 보존.
12. process identity 비교를 통한 PID reuse 보호.
13. corrupt registry 처리.
14. startup health failure에서 partial process cleanup.
15. 여러 failed attempt 사이의 startup retry cleanup.
16. registry 없는 same-scope Serena process를 orphan으로 cleanup.
17. sleep/wake heartbeat delay 처리.
18. SIGTERM 이후 SIGKILL escalation.
19. wrong-project registry record가 live PID를 가리켜도 PID를 종료하지 않고 registry만 비움.
20. wrong-client registry record가 live PID를 가리켜도 PID를 종료하지 않고 registry만 비움.
21. process command parsing이 애매하거나 truncated이면 fail closed.
22. startup, watchdog, exit cleanup이 같은 termination primitive를 사용.

## 현재 Known Gap

현재 lifecycle 코드는 registry, lease, watchdog, startup process reconciliation,
shared termination, lifecycle snapshot diagnostics를 기준으로 scope-local Serena
MCP 서버를 관리한다.

남은 gap:

- shell shim 적용 범위는 interactive no-argument `codex` / `claude` 호출로
  제한된다.
- process table parsing은 운영체제의 command text 표현에 의존하므로,
  project/context를 정확히 파싱할 수 없는 process는 fail closed로 보존한다.
