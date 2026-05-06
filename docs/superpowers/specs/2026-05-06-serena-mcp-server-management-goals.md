# Serena MCP Server Management Goals

작성일: 2026-05-06

## 배경

현재 Serena MCP 서버 관리 흐름은 전역 Codex/Claude 설정, 프로젝트별 registry,
dashboard active project, 동적 MCP 포트가 서로 어긋날 수 있다. 그 결과 새
Codex/Claude 세션이 이미 죽은 MCP URL을 물거나, dashboard가 `Active Project:
None` 상태로 남는 문제가 발생할 수 있다.

이 문서는 구현 계획이 아니라, Serena MCP 서버 관리 로직을 전면 재작성하기
전에 고정해야 할 목표와 성공 기준을 정리한다.

## 핵심 목표

Serena MCP 서버는 프로젝트 루트와 agent client type의 조합마다 하나만 유지한다.

공유 단위:

```text
(project root, client type)
```

예:

```text
/Users/hyun/Desktop/homebrew-dotsync + codex  -> Serena server A
/Users/hyun/Desktop/homebrew-dotsync + claude -> Serena server B
/Users/hyun/Desktop/TicketRanking + codex     -> Serena server C
```

같은 프로젝트라도 `codex`와 `claude`는 서로 다른 Serena MCP 서버를 사용한다.
같은 프로젝트의 여러 `codex` 세션은 codex용 Serena 서버 하나를 공유하고, 같은
프로젝트의 여러 `claude` 세션은 claude용 Serena 서버 하나를 공유한다.

## 예시 시나리오

같은 프로젝트에서 네 개의 터미널이 실행된 경우:

```text
프로젝트 1

터미널 1: codex 실행
터미널 2: codex 실행
터미널 3: claude 실행
터미널 4: claude 실행
```

기대 상태:

```text
프로젝트 1 + codex:
  Serena MCP server: 1개
  leases:
    - terminal 1 / codex
    - terminal 2 / codex

프로젝트 1 + claude:
  Serena MCP server: 1개
  leases:
    - terminal 3 / claude
    - terminal 4 / claude
```

결과:

```text
Serena MCP server 총 2개
codex 세션 2개 -> codex용 서버 1개 공유
claude 세션 2개 -> claude용 서버 1개 공유
```

## 서버 종료 조건

Serena MCP 서버는 자기 scope에 속한 active lease가 0개가 되면 즉시 종료한다.

scope:

```text
(project root, client type)
```

정상 종료:

```text
child process 종료 감지
-> 해당 session lease 제거
-> 같은 scope의 lease가 0개이면 Serena MCP 서버 즉시 종료
```

신호 기반 종료:

```text
SIGINT / SIGTERM / SIGHUP
-> launcher가 child 종료를 유도
-> lease 제거
-> 같은 scope의 lease가 0개이면 Serena MCP 서버 즉시 종료
```

cleanup 코드를 실행할 수 없는 비정상 종료:

```text
kill -9 / 터미널 강제 종료 등
-> heartbeat timeout으로 stale lease 판정
-> stale lease 제거
-> 같은 scope의 lease가 0개이면 Serena MCP 서버 즉시 종료
```

`codex` lease는 codex용 서버 종료 조건에만 영향을 주고, `claude` lease는
claude용 서버 종료 조건에만 영향을 준다.

## 실행 진입점 목표

사용자가 같은 프로젝트에서 여러 터미널로 `codex` 또는 `claude`를 실행해도
동일한 scope의 Serena MCP 서버를 공유해야 한다.

이를 위해 실제 실행은 Serena-aware launcher 또는 wrapper를 통과해야 한다.
launcher는 다음을 보장한다.

- 현재 작업 디렉토리에서 정규화된 프로젝트 루트를 찾는다.
- client type을 `codex` 또는 `claude`로 구분한다.
- `(project root, client type)` scope의 live Serena MCP 서버를 찾거나 시작한다.
- 해당 실행에 대한 session lease를 등록한다.
- child process 실행 중 heartbeat를 갱신한다.
- child process 종료 시 lease를 제거한다.
- lease 제거 후 같은 scope의 active lease가 0개이면 서버를 즉시 종료한다.
- child process에는 검증된 live MCP URL만 주입한다.
- launcher가 child process를 실행하기 전에 Serena dashboard의 active project가
  해당 project root로 올바르게 표시되는지 확인한다.

## 피해야 할 상태

다음 상태는 설계상 발생하지 않아야 한다.

- `~/.codex/config.toml` 또는 sync folder에 죽은 동적 Serena MCP URL이 저장됨
- dotsync가 일회성 Serena MCP 포트를 저장하거나 복원함
- registry에는 한 포트가 기록되어 있는데 새 Codex/Claude 세션은 다른 죽은 포트를 사용함
- dashboard endpoint만 살아 있고 MCP endpoint는 죽었는데 healthy로 판단함
- repair 실패 후에도 Codex/Claude를 실행함
- dashboard `Active Project: None` 상태를 `"repaired"`로 간주함
- child process 실행 후 dashboard에 잘못된 active project가 표시됨
- 같은 `(project root, client type)` scope에서 Serena MCP 서버가 여러 개 생김
- registry write race로 서로 다른 launcher가 서버 정보를 덮어씀
- 마지막 lease가 사라졌는데 Serena MCP 서버가 계속 살아 있음

## 성공 기준

다음 조건을 만족하면 목표를 달성한 것으로 본다.

- 같은 프로젝트의 여러 `codex` 세션은 codex용 Serena MCP 서버 하나를 공유한다.
- 같은 프로젝트의 여러 `claude` 세션은 claude용 Serena MCP 서버 하나를 공유한다.
- 같은 프로젝트라도 `codex`와 `claude`는 서로 다른 Serena MCP 서버를 사용한다.
- 다른 프로젝트는 서로 다른 Serena MCP 서버를 사용한다.
- 마지막 lease가 정상 종료되면 해당 scope의 Serena MCP 서버가 즉시 종료된다.
- 터미널 강제 종료나 비정상 종료 후 stale lease가 제거되면, lease가 0개인 서버가 종료된다.
- 죽은 MCP URL, 죽은 PID, endpoint 불일치, active project 불일치는 live server로 인정하지 않는다.
- launcher는 unhealthy server를 child process에 주입하지 않는다.
- launcher가 Codex/Claude를 넘기기 전에 dashboard active project가 해당 project root로 표시된다.
- dotsync는 Serena의 동적 MCP URL을 sync 대상으로 취급하지 않는다.

## 아직 설계하지 않은 항목

이 문서는 목표만 정의한다. 다음 항목은 별도 설계 단계에서 결정한다.

- registry 파일 형식
- file lock과 atomic write 방식
- heartbeat 주기와 timeout 값
- server health check의 구체적 프로토콜
- 기존 `tools/serena_project.py`, `tools/serena_watchdog.py` 삭제 및 대체 파일 구조
- `codex`/`claude` wrapper를 PATH에 배치하는 방식
- dotsync Codex config sanitizer의 세부 정책
- 테스트 전략과 구현 순서
