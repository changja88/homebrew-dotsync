"""Install the launcher-owned Serena and Graphify user guidance."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any


GUIDANCE_START = "<!-- dotsync-agent-guidance:start -->"
GUIDANCE_END = "<!-- dotsync-agent-guidance:end -->"
LEGACY_START = "### Serena MCP"
LEGACY_END = "## 코딩 설계 원칙"
HOOK_MARKER = "DOTSYNC_AGENT_GUIDANCE_V1=1"

LEGACY_PRE_TOOL_USE_COMMAND = r'''r="$PWD"; while [ "$r" != "/" ] && [ ! -e "$r/.git" ] && [ ! -f "$r/.serena/project.yml" ]; do r=$(dirname "$r"); done; [ -f "$r/.serena/project.yml" ] && printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"This repository explicitly opts into Serena via .serena/project.yml. For exact symbol definitions and who-calls-what, prefer Serena find_symbol / find_referencing_symbols over text grep. If Serena tools are deferred, load them with ToolSearch first. No active-project check is needed because the launcher pins Serena to this repo. If Serena tools are unavailable, continue with built-in tools."}}' || true'''
LEGACY_SESSION_START_COMMAND = r'''root="$PWD"; while [ "$root" != "/" ] && [ ! -e "$root/.git" ]; do root=$(dirname "$root"); done; [ "$root" = "/" ] && root="$PWD"; serena=disabled; [ -f "$root/.serena/project.yml" ] && serena=enabled; graphify=disabled; [ -f "$root/graphify-out/graph.json" ] && graphify=enabled; printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Project tool opt-in: Serena=%s (.serena/project.yml), graphify=%s (graphify-out/graph.json). Only use a tool when enabled or when the user explicitly requests it. When disabled, do not load, call, or initialize it; do not create or rebuild graphify-out or install graphify integration or hooks. Use built-in tools instead. If Serena is enabled, the dotsync launcher pins it to this repo; load symbolic tools before symbol-level code work and never call activate_project. If graphify is enabled, query the existing graph but never rebuild it without an explicit request."}}' "$serena" "$graphify"'''

SERENA_USE_CASES = """Serena opt-in 조건을 만족한 상태에서 다음 중 하나라도 해당하면 Serena를 기본 선택지로 삼는다.

- 함수, 메서드, 클래스 시그니처 변경
- 공개 API 또는 여러 파일에서 호출되는 계약 변경
- 여러 파일에 영향이 갈 수 있는 동작 변경이나 리팩터링
- 심볼 이름 변경, 삭제, 이동
- 300줄 이상 소스 파일의 구조 파악
- 기본 텍스트 검색 결과가 여러 파일에 걸치는 참조 추적
- 함수나 클래스 전체의 의미를 바꾸는 리팩터링
- 호출 관계, 상속, 오버라이드, 참조 누락이 버그 원인일 가능성이 있는 작업

Serena 도구 선택 기준:

- 큰 파일 구조 파악에는 `get_symbols_overview`를 우선 고려한다.
- 특정 심볼 구현 확인에는 `find_symbol`을 우선 고려한다.
- 호출처와 참조 확인에는 `find_referencing_symbols`를, 선언·구현 추적에는 `find_declaration`/`find_implementations`를 우선 고려한다.
- 함수나 클래스 전체를 바꾸는 작업에는 `replace_symbol_body`, 심볼 앞뒤 추가에는 `insert_before_symbol`/`insert_after_symbol`을 우선 고려한다.
- 수정 후 필요하면 `get_diagnostics_for_file`로 진단을 확인한다.
- 텍스트 패턴 검색과 짧은 부분 수정에는 에이전트의 기본 검색·편집 도구를 사용한다.
- 단순 텍스트 검색, 작은 단일 파일 수정, 빠른 확인 작업, 문서 작성, 리서치, 단순 질의응답에는 Serena 사용을 강제하지 않는다.
- Serena가 연결되지 않았거나 도구를 로드할 수 없으면 그 사실을 짧게 알리고 기본 도구로 계속 진행한다.
"""

GRAPHIFY_GUIDANCE = """### graphify · Serena · 기본 도구 라우팅

graphify와 Serena는 서로 독립적인 워크트리별 opt-in 도구다. 도구가 전역 설치되어 있거나 스킬 목록에 보이는 것만으로는 사용에 동의한 것으로 보지 않는다.

- Serena opt-in 표식은 현재 워크트리 루트의 `.serena/project.yml`이다. 표식이 없으면 Serena를 로드·호출·초기화하지 않는다.
- graphify opt-in 표식은 현재 워크트리 루트의 `graphify-out/graph.json`이다. 기존 그래프가 없으면 graphify 스킬이나 CLI를 호출하지 않고, `graphify-out/` 생성·갱신, 통합 설치, 훅 설치도 하지 않는다.
- 사용자가 현재 요청에서 해당 도구의 사용이나 초기화를 명시한 경우에는 표식 없이도 검토할 수 있다. 단, Graphify 초기화와 갱신은 primary checkout에서만 허용한다.
- 한 도구만 opt-in되어 있으면 다른 도구까지 자동으로 사용하지 않는다.

Graphify checkout 규칙:

- primary checkout이 canonical `graphify-out/`을 소유한다. 자동 code graph 업데이트는 primary checkout의 공식 Git 훅(post-commit/post-checkout)만 담당한다.
- linked worktree에서는 기존 그래프를 조회만 한다. `graphify query/explain/path`는 사용할 수 있지만 `graphify update`, 전체 rebuild, `--cluster-only`, `add`, `--watch`, integration/hook 설치는 실행하지 않는다.
- linked worktree에 그래프가 없거나 사용자가 갱신을 요청하면 직접 만들거나 갱신하지 말고 primary checkout에서 런처 초기화 또는 명시적 갱신을 하도록 안내한다.
- 문서·논문·이미지처럼 공식 code-only Git 훅이 갱신하지 않는 입력은 사용자가 명시적으로 요청한 경우에만 primary checkout에서 갱신한다.
- Graphify를 MCP 서버로 등록하지 않는다. CLI와 agent skill로 사용한다.
- primary/linked 판별이 필요하면 `git rev-parse --git-dir`와 `git rev-parse --git-common-dir`의 실제 경로를 비교한다. 다르면 linked worktree다.

위 opt-in과 checkout 조건을 만족할 때만 아래 용도 기준을 적용한다.

- 기존 그래프가 있는 구조·관계·범위 질문에는 `graphify query/explain/path`를 먼저 쓴다. 명시적 요청 없이 그래프를 새로 만들거나 재구축하지 않는다.
- 특정 심볼의 정의·참조·시그니처 확인과 심볼 단위 편집에는 Serena를 먼저 쓴다. graphify로 위치를 좁힌 뒤 Serena로 정밀 작업을 잇는 조합이 표준이다.
- 리터럴 문자열·에러 메시지·설정 키 검색에는 기본 검색 도구를 쓴다.
- opt-in 표식이 없거나 그래프 범위 밖인 파일(설정 파일, 문서, 저장소 외부 경로)은 기본 도구로 직접 읽는다.
"""

PRE_TOOL_USE_COMMAND = HOOK_MARKER + r'''; r="$PWD"; while [ "$r" != "/" ] && [ ! -e "$r/.git" ] && [ ! -f "$r/.serena/project.yml" ]; do r=$(dirname "$r"); done; if [ -f "$r/.serena/project.yml" ]; then printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"This worktree explicitly opts into Serena via .serena/project.yml. Codex and Claude share one launcher-managed Serena server for this worktree. For exact symbol definitions and who-calls-what, prefer Serena find_symbol / find_referencing_symbols over text grep. If Serena tools are deferred, load them with ToolSearch first. Never call activate_project or get_current_config; the shared single-project context excludes them. If Serena tools are unavailable, continue with built-in tools."}}'; fi'''

SESSION_START_COMMAND = HOOK_MARKER + r'''; root="$PWD"; while [ "$root" != "/" ] && [ ! -e "$root/.git" ]; do root=$(dirname "$root"); done; [ "$root" = "/" ] && root="$PWD"; checkout=none; if git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then git_dir=$(cd "$root" && cd "$(git rev-parse --git-dir 2>/dev/null)" 2>/dev/null && pwd -P); common_dir=$(cd "$root" && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P); if [ -n "$common_dir" ] && [ "$git_dir" != "$common_dir" ]; then checkout=linked; else checkout=primary; fi; fi; serena=disabled; [ -f "$root/.serena/project.yml" ] && serena=enabled; graphify=disabled; [ -f "$root/graphify-out/graph.json" ] && graphify=enabled; serena_policy="Serena policy: disabled; use built-in tools."; [ "$serena" = enabled ] && serena_policy="Serena policy: one launcher-managed server per worktree, shared by Codex and Claude; never call activate_project or get_current_config."; graphify_policy="Graphify policy: disabled; do not load, initialize, update, or install it unless the user explicitly requests initialization from a primary checkout."; if [ "$graphify" = enabled ] && [ "$checkout" = linked ]; then graphify_policy="Graphify policy: query-only in this linked worktree; do not run graphify update, rebuild, cluster-only, add, watch, integration install, or hook install. Initialize and update the canonical graph from the primary checkout."; elif [ "$graphify" = enabled ]; then graphify_policy="Graphify policy: this primary checkout owns the canonical graph; automatic code updates run through the official Git hooks. Manual semantic updates require an explicit user request."; fi; printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Project tool opt-in: Serena=%s (.serena/project.yml), graphify=%s (graphify-out/graph.json), checkout=%s. %s %s"}}' "$serena" "$graphify" "$checkout" "$serena_policy" "$graphify_policy"'''


class GuidanceUpdateError(RuntimeError):
    """Raised when a managed guidance boundary cannot be updated safely."""


def _serena_guidance(client: str) -> str:
    loading = (
        "Serena opt-in 코드 작업을 시작할 때 도구가 deferred 상태면 ToolSearch로 먼저 로드하고, 세션에서 처음 사용할 때 `initial_instructions`를 한 번 읽고 따른다."
        if client == "claude"
        else "Serena 도구가 제공되면 아래 기준으로 사용한다. 세션에서 `initial_instructions`가 노출되고 아직 읽지 않았다면 한 번 읽고 따른다."
    )
    return f"""### Serena MCP

Serena는 워크트리별 opt-in 도구다. 현재 워크트리 루트에 `.serena/project.yml`이 있을 때만 Serena 사용을 선택한 것으로 본다. 표식이 없으면 Serena 도구를 검색·로드·호출하거나 프로젝트를 초기화하지 않고 기본 도구로 진행한다. 사용자가 현재 요청에서 Serena 사용이나 초기화를 명시한 경우만 예외로 한다.

opt-in된 워크트리에서는 dotsync launcher가 `--project <워크트리 루트>`와 공통 `oaicompat-agent` single-project context로 Serena를 자동 기동한다. 같은 워크트리에서 실행한 Codex와 Claude는 동일한 Serena 서버를 공유하고, 서로 다른 linked worktree는 각자 별도 서버를 사용한다. 마지막 연결 세션이 종료되면 해당 워크트리 서버도 종료된다.

프로젝트 전환이 불가능하므로 active-project 확인이나 전환을 시도하지 않는다. `activate_project`, `get_current_config`, `search_for_pattern`, `replace_content`는 공통 context에서 제외되어 있으므로 호출하지 않는다. {loading}

{SERENA_USE_CASES}
서브에이전트와 병렬 작업에서도 `activate_project`를 호출하지 않는다. 도구 목록에 Serena가 있으면 같은 기준으로 사용하고, 없으면 기본 도구로 진행한다. 메인 에이전트가 이미 확인한 심볼·참조·구조 결과를 제공한 경우 그 결과를 우선 사용한다.

작업 완료 보고에는 Serena 사용 여부 또는 생략 이유를 한 줄로 남긴다.

- 예: `Serena: used find_referencing_symbols for start_browser.`
- 예: `Serena: skipped — 단일 파일 소규모 수정이라 기본 도구로 충분.`
"""


def guidance_block(client: str) -> str:
    """Return the managed Markdown block for one agent client."""
    if client not in {"codex", "claude"}:
        raise ValueError(f"unsupported client: {client}")
    return (
        f"{GUIDANCE_START}\n"
        f"{_serena_guidance(client).rstrip()}\n\n"
        f"{GRAPHIFY_GUIDANCE.rstrip()}\n"
        f"{GUIDANCE_END}\n\n"
    )


def replace_guidance(text: str, client: str) -> str:
    """Replace an existing managed or legacy guidance section."""
    block = guidance_block(client)
    if GUIDANCE_START in text or GUIDANCE_END in text:
        if text.count(GUIDANCE_START) != 1 or text.count(GUIDANCE_END) != 1:
            raise GuidanceUpdateError("managed guidance markers are incomplete or duplicated")
        start = text.index(GUIDANCE_START)
        end = text.index(GUIDANCE_END, start) + len(GUIDANCE_END)
        while end < len(text) and text[end] == "\n":
            end += 1
        return text[:start] + block + text[end:]

    if text.count(LEGACY_START) != 1 or text.count(LEGACY_END) != 1:
        raise GuidanceUpdateError("legacy guidance headings are missing or duplicated")
    start = text.index(LEGACY_START)
    end = text.index(LEGACY_END, start)
    return text[:start] + block + text[end:]


def _replace_hook_command(
    settings: dict[str, Any],
    *,
    event: str,
    matcher: str | None,
    legacy_command: str,
    replacement: str,
) -> None:
    matches: list[dict[str, Any]] = []
    entries = settings.get("hooks", {}).get(event, [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if matcher is not None and entry.get("matcher") != matcher:
            continue
        for hook in entry.get("hooks", []):
            command = hook.get("command", "") if isinstance(hook, dict) else ""
            if (
                isinstance(hook, dict)
                and hook.get("type") == "command"
                and (command == legacy_command or HOOK_MARKER in str(command))
            ):
                matches.append(hook)
    if len(matches) != 1:
        raise GuidanceUpdateError(
            f"expected one launcher-owned {event} guidance hook; found {len(matches)}"
        )
    matches[0]["command"] = replacement


def update_claude_settings(text: str) -> str:
    """Update only the launcher-owned commands in Claude settings JSON."""
    try:
        settings = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GuidanceUpdateError(f"invalid Claude settings JSON: {exc}") from exc
    if not isinstance(settings, dict):
        raise GuidanceUpdateError("Claude settings root must be an object")
    _replace_hook_command(
        settings,
        event="PreToolUse",
        matcher="Grep",
        legacy_command=LEGACY_PRE_TOOL_USE_COMMAND,
        replacement=PRE_TOOL_USE_COMMAND,
    )
    _replace_hook_command(
        settings,
        event="SessionStart",
        matcher=None,
        legacy_command=LEGACY_SESSION_START_COMMAND,
        replacement=SESSION_START_COMMAND,
    )
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise GuidanceUpdateError(f"guidance target must be a regular file: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _target_paths(config_root: Path, live_home: Path) -> tuple[tuple[Path, str], ...]:
    return (
        (config_root / "codex" / "AGENTS.md", "codex"),
        (config_root / "claude" / "CLAUDE.md", "claude"),
        (config_root / "claude" / "settings.json", "settings"),
        (live_home / ".codex" / "AGENTS.md", "codex"),
        (live_home / ".claude" / "CLAUDE.md", "claude"),
        (live_home / ".claude" / "settings.json", "settings"),
    )


def install_guidance(config_root: Path, live_home: Path) -> bool:
    """Update sync-folder and live guidance; return False when sync files are absent."""
    sync_targets = (
        config_root / "codex" / "AGENTS.md",
        config_root / "claude" / "CLAUDE.md",
        config_root / "claude" / "settings.json",
    )
    existing = [os.path.lexists(path) for path in sync_targets]
    if not any(existing):
        return False
    if not all(existing):
        missing = ", ".join(str(path) for path, present in zip(sync_targets, existing) if not present)
        raise GuidanceUpdateError(f"partial dotsync user-scope config; missing: {missing}")

    rendered: list[tuple[Path, str, str]] = []
    for path, kind in _target_paths(config_root, live_home):
        if path.is_symlink() or not path.is_file():
            raise GuidanceUpdateError(f"guidance target must be a regular file: {path}")
        source = path.read_text(encoding="utf-8")
        if kind == "settings":
            updated = update_claude_settings(source)
        else:
            updated = replace_guidance(source, kind)
        rendered.append((path, source, updated))

    committed: list[tuple[Path, str]] = []
    try:
        for path, source, updated in rendered:
            _atomic_write(path, updated)
            committed.append((path, source))
    except BaseException as primary:
        rollback_errors: list[str] = []
        for committed_path, original in reversed(committed):
            try:
                _atomic_write(committed_path, original)
            except BaseException as rollback_error:
                rollback_errors.append(f"{committed_path}: {rollback_error}")
        if rollback_errors:
            detail = "; ".join(rollback_errors)
            message = f"guidance update failed at {path}; rollback incomplete: {detail}"
        else:
            message = f"guidance update failed at {path}; prior targets rolled back"
        if isinstance(primary, Exception):
            raise GuidanceUpdateError(message) from primary
        if hasattr(primary, "add_note"):
            primary.add_note(message)
        raise
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-root", type=Path, required=True)
    parser.add_argument("--live-home", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        installed = install_guidance(args.config_root.resolve(), args.live_home.resolve())
    except GuidanceUpdateError as exc:
        parser.error(str(exc))
    if installed:
        print("installed Serena/Graphify user guidance into dotsync and live scopes")
    else:
        print("skipped Serena/Graphify user guidance: dotsync targets not found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
