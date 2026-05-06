# Serena MCP Server Management Implementation Plan

작성일: 2026-05-06

## Goal

각 `(project root, client type)` scope가 정확히 하나의 healthy Serena MCP
server를 공유하고, active session lease가 0개가 되면 해당 server를 종료한다.
Codex와 Claude는 같은 프로젝트에서도 서로 다른 scope로 분리한다.

## Runtime Architecture

- `~/.zshrc`에는 `codex()` / `claude()` agent shim을 둔다.
- shim은 기존 agent preflight/cleanup UX를 보존한 뒤
  설치된 `libexec/tools/serena_agent_launcher.py`를 실행한다.
- preflight/cleanup adapter는 Codex/Claude session jsonl 정리, memory reset,
  `sessions delete/keep`, `memory reset` 표시를 담당한다.
- Serena launcher는 프로젝트 루트 탐지, scope별 server ensure, lease 등록,
  heartbeat, child process MCP URL 주입, 정상 종료 cleanup을 담당한다.
- detached watchdog는 force-quit 등으로 launcher cleanup이 실행되지 않은 경우
  stale lease를 제거하고 zero-lease server를 종료한다.
- runtime state는 project-local `.serena/dotsync-mcp/<client>/`에 둔다.
- `dotsync`는 Codex/Claude 설정 sync 시 동적 local Serena MCP URL을 제외한다.

## Module Boundaries

### Agent preflight/cleanup adapter

소유 책임:

- Claude 오래된 session jsonl 정리
- Claude project memory 디렉터리 삭제
- Codex session jsonl 일부 정리
- Codex memories 디렉터리 삭제
- preflight 화면에서 `sessions delete/keep`, `memory reset` 표시
- 사용자 확인 후 cleanup 실행

금지 책임:

- Serena MCP registry 읽기/쓰기
- Serena MCP server 시작/종료
- lease, heartbeat, watchdog 관리
- Codex/Claude에 MCP URL 주입

### Serena MCP lifecycle launcher

소유 책임:

- `(project root, client type)` scope 계산
- healthy Serena MCP server ensure
- session lease 등록/해제
- heartbeat 갱신
- zero-lease shutdown
- stale lease 정리 트리거
- Codex/Claude child command에 live MCP URL 주입

금지 책임:

- Codex/Claude session jsonl 삭제
- Codex/Claude memory 디렉터리 삭제
- preflight cleanup UI 표시

### Agent launch adapter

소유 책임:

- Codex에는 per-run `-c mcp_servers.serena.url="..."` 주입
- Claude에는 temporary `--mcp-config=<path>` 주입
- 동적 Serena URL을 전역 설정 파일이나 sync folder에 쓰지 않음

## zsh Agent Shim

공식 적용 방식은 PATH wrapper 파일 설치가 아니라 zsh 함수다. 이 함수는
기존 preflight/cleanup UX를 유지하고, Serena 관리만 Python launcher에 위임한다.

```bash
python3 "$(brew --prefix dotsync)/libexec/tools/serena_zsh_shim.py"
```

생성된 snippet은 인자가 없는 대화형 `codex` / `claude` 실행만 관리한다.
`codex --help`, `claude --version`, `claude mcp list` 같은 인자 기반 CLI 호출은
cleanup이나 Serena launcher를 거치지 않고 실제 binary로 직접 전달한다.

## Critical Behaviors

- The launcher must never pass an MCP URL to Codex/Claude until both MCP and
  dashboard active-project health checks pass.
- Dashboard health requires `active_project.path == project_root`.
- Codex receives the URL with per-run `-c mcp_servers.serena.url="..."`.
- Claude receives the URL with a temporary `--mcp-config=<path>` file.
- The launcher must not write dynamic Serena MCP URLs into `~/.codex/config.toml`
  or `~/.claude.json`.
- Existing stale global Serena MCP entries must be removed from local configs.
- Existing agent preflight/cleanup output must remain owned by the zsh shim, not
  by `tools/serena_agent_launcher.py`.

## Verification

- Unit tests cover sanitizer behavior, registry locking, health checks, server
  ensure, watchdog cleanup, launcher command construction, and zsh shim text.
- Unit tests prove `tools/serena_agent_launcher.py` does not import or execute
  Codex/Claude session cleanup.
- zsh shim tests prove `sessions delete/keep`, `memory reset`, Claude cleanup,
  and Codex cleanup remain present.
- Runtime checks:
  - same project + two Codex sessions -> one Codex Serena server, two leases
  - same project + two Claude sessions -> one Claude Serena server, two leases
  - same project + Codex and Claude -> two servers split by context
  - normal exit -> own lease removed, zero-lease server stopped
  - force quit -> heartbeat timeout then watchdog cleanup
