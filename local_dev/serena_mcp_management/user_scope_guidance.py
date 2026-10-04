"""Install the launcher-owned Serena and Graphify user guidance."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
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

TOOL_ROUTING_GUIDANCE = """### Serena · Graphify 사용 조건과 도구 선택

두 도구는 독립적인 워크트리별 opt-in 도구다. 전역 설치나 스킬 목록 노출만으로 사용에 동의한 것으로 보지 않는다.

- 현재 워크트리 루트의 `.serena/project.yml`은 Serena, `graphify-out/graph.json`은 Graphify의 opt-in 표식이다. primary 그래프를 가리키는 조회용 심볼릭 링크도 인정한다.
- 표식이 없는 도구는 검색·로드·호출·초기화하지 않는다. Graphify 표식이 없으면 그래프 생성·갱신과 통합·훅 설치도 하지 않는다. 한 도구의 opt-in으로 다른 도구까지 사용하지 않는다.
- 사용자가 현재 요청에서 해당 도구의 사용·초기화를 명시한 경우에만 표식 없이 그 요청 범위를 검토한다. Graphify 초기화·갱신은 아래의 primary 규칙을 따른다.

사용 조건을 만족하는 도구 중 질문에 직접 필요한 도구를 선택한다.

- 낯선 기능의 위치나 여러 모듈의 구조·관계를 파악할 때는 기존 Graphify 그래프의 `query/explain/path`를 먼저 사용한다.
- 특정한 소스 파일의 구조, 심볼의 정의·참조·구현을 확인하거나 심볼 단위로 편집할 때는 Serena를 먼저 사용한다.
- 정확한 문자열·에러 메시지·설정값·문서 원문 확인과 짧은 부분 수정에는 기본 검색·편집 도구를 사용한다.
- 심볼 작업에 앞서 Graphify로 위치를 좁히는 것은 대상이 불명확할 때만 한다. 대상 심볼을 이미 알면 Serena로 바로 확인한다.
- 문서·설정 파일도 그래프에 수록돼 있으면 관계 탐색에 활용한다. 파일 종류만으로 제외하지 않으며, 그래프에 수록되지 않은 대상은 실제 파일로 확인한다.
"""

GRAPHIFY_GUIDANCE = """### Graphify

- primary checkout은 처음 clone한 원본 작업 폴더이며 브랜치 이름이 아니다. main을 기준으로 삼으려면 이 폴더를 main으로 유지한다. dotsync launcher는 opt-in된 Claude·Codex 세션의 `GRAPHIFY_OUT`을 primary의 `graphify-out/` 절대 경로로 설정한다.
- linked worktree에서는 primary의 기준 그래프를 조회만 한다. 미병합 변경이 없을 수 있으므로 현재 코드·심볼·참조는 해당 워크트리의 실제 파일과 사용 가능한 Serena로 확인한다. `update`, rebuild, `--cluster-only`, `add`, `--watch`, 통합·훅 설치는 실행하지 않는다.
- `query/explain/path`의 내부 조회 캐시 기록은 허용하지만, 그래프·학습 자료 갱신은 조회에 포함하지 않는다. 스킬이 안내하더라도 `save-result`, `reflect`를 조회 전후에 자동 실행하지 않는다. 에이전트가 직접 실행하는 저장·회고 갱신은 사용자가 명시적으로 요청한 경우에만 primary에서 수행한다.
- primary의 자동 code graph 갱신은 공식 Git 훅(post-commit/post-checkout)이 담당한다. 승인된 코드 동기화로 primary에서 `git pull`·`git merge`를 수행했다면 이후 `graphify update .`로 갱신한다. 읽기 전용 조사 중에는 갱신하지 않는다.
- 문서·논문·이미지 등 semantic 입력의 갱신은 사용자가 명시적으로 요청한 경우에만 primary에서 수행한다. linked worktree에 그래프가 없거나 갱신을 요청받으면 primary에서 초기화·갱신하도록 안내한다.
- Graphify 조회에 실패하거나 결과가 불충분하면 실제 파일과 사용 가능한 Serena로 확인한다. 검색 결과가 없다는 이유만으로 코드에 없다고 단정하지 않으며, 조회 문제를 해결하려고 자동 초기화·재구축하지 않는다.
- Graphify를 MCP 서버로 등록하지 않는다. CLI와 agent skill로 사용하며, 스킬의 일반 안내에도 위 사용 조건과 checkout 규칙을 적용한다.
- checkout 구분이 필요하면 `git rev-parse --git-dir`와 `git rev-parse --git-common-dir`의 실제 경로를 비교한다. 다르면 linked worktree다.
"""

SESSION_START_COMMAND = HOOK_MARKER + r'''; root="$PWD"; while [ "$root" != "/" ] && [ ! -e "$root/.git" ]; do root=$(dirname "$root"); done; [ "$root" = "/" ] && root="$PWD"; checkout=none; if git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then git_dir=$(cd "$root" && cd "$(git rev-parse --git-dir 2>/dev/null)" 2>/dev/null && pwd -P); common_dir=$(cd "$root" && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P); if [ -n "$common_dir" ] && [ "$git_dir" != "$common_dir" ]; then checkout=linked; else checkout=primary; fi; fi; serena=disabled; [ -f "$root/.serena/project.yml" ] && serena=enabled; graphify=disabled; [ -f "$root/graphify-out/graph.json" ] && graphify=enabled; graph_context=""; if [ "$graphify" = enabled ] && [ "$checkout" = linked ]; then graph_context="Shared primary graph: query-only; do not run graphify update here. Unmerged branch changes may be absent."; elif [ "$graphify" = enabled ] && [ "$checkout" = primary ]; then graph_context="This checkout owns the canonical graph; official Git hooks refresh code."; fi; printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Project tool opt-in: Serena=%s, graphify=%s, checkout=%s. Follow the user-scope Serena/Graphify guidance. %s"}}' "$serena" "$graphify" "$checkout" "$graph_context"'''


PROJECT_GRAPHIFY_START = "<!-- dotsync-graphify-checkout:start -->"
PROJECT_GRAPHIFY_END = "<!-- dotsync-graphify-checkout:end -->"
PROJECT_GRAPHIFY_POLICY = (
    "- Query the shared primary checkout graph; linked worktrees are query-only. "
    "Only the primary checkout updates it (official Git hooks, or `graphify update .` "
    "after an authorized pull/merge); semantic updates require an explicit request. "
    "Check unmerged branch changes against local source and Serena."
)


# Match official paragraphs after folding line wraps; preserve custom project text.
PROJECT_QUERY_RULE = (
    '- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use '
    '`graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. '
    'These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.'
)
PROJECT_DIRTY_RULE = (
    '- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip '
    'graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.'
)
PROJECT_TOOL_ROUTING = """- For locating an unfamiliar feature or exploring relationships across modules, use `graphify query/explain/path` when graphify-out/graph.json exists. Indexed documents and configuration files can also supply relationship context; do not exclude them by file type.
- For known symbols, use Serena for definitions/references and symbol-level edits when opted in and available. Use built-in tools for exact strings, configuration values, document text, and small text edits. Use Graphify before Serena only when the location still needs narrowing.
- If graph queries fail or are insufficient, check actual files and available Serena tools. No search results do not prove that code is absent. Do not automatically initialize or rebuild a graph to recover from a query problem."""


def install_project_graphify_guidance(project_root: Path) -> None:
    """Align official routing and update instructions during explicit setup."""
    _remove_project_graphify_guards(project_root)
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = project_root / name
        if not path.is_file() or path.is_symlink():
            continue
        source = path.read_text(encoding="utf-8")
        match = re.search(r"^## graphify[ \t]*\r?$.*?(?=^## |\Z)", source, re.M | re.S)
        if match is None:
            continue
        section = match.group()
        replacements = {PROJECT_QUERY_RULE: PROJECT_TOOL_ROUTING, PROJECT_DIRTY_RULE: ""}
        section = re.sub(
            r"(?m)^- [^\n]*(?:\n[ \t]+[^\n]+)*",
            lambda bullet: replacements.get(" ".join(bullet.group().split()), bullet.group()),
            section,
        )
        if PROJECT_GRAPHIFY_START in section or PROJECT_GRAPHIFY_END in section:
            if section.count(PROJECT_GRAPHIFY_START) != 1 or section.count(PROJECT_GRAPHIFY_END) != 1:
                raise GuidanceUpdateError(f"malformed Graphify checkout policy: {path}")
            start = section.index(PROJECT_GRAPHIFY_START)
            end = section.index(PROJECT_GRAPHIFY_END)
            if end < start:
                raise GuidanceUpdateError(f"reversed Graphify checkout policy: {path}")
            section = section[:start] + section[end + len(PROJECT_GRAPHIFY_END):]
        section = section.replace(
            "- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).",
            PROJECT_GRAPHIFY_POLICY,
        ).replace("- Graph updates follow the checkout policy below.", PROJECT_GRAPHIFY_POLICY)
        section = section.rstrip() + "\n\n"
        updated = source[:match.start()] + section + source[match.end():]
        if updated != source:
            _atomic_write(path, updated)


class GuidanceUpdateError(RuntimeError):
    """Raised when a managed guidance boundary cannot be updated safely."""


def _is_graphify_guard(hook: Any) -> bool:
    """Recognise direct official guard invocations, not arbitrary shell mentions."""
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return False
    command = hook.get("command")
    if not isinstance(command, str):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return (
        len(words) in (3, 4)
        and Path(words[0]).name == "graphify"
        and words[1] == "hook-guard"
        and words[2] in ("search", "read")
        and words[3:] in ([], ["--strict"])
    )


def _remove_project_graphify_guards(project_root: Path) -> None:
    """Retire Claude's conflicting nudges while preserving every unrelated hook."""
    path = project_root / ".claude" / "settings.json"
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        raise GuidanceUpdateError(f"Claude settings must be a regular file: {path}")
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise GuidanceUpdateError(f"invalid Claude settings JSON: {path}") from exc
    if not isinstance(settings, dict):
        raise GuidanceUpdateError(f"Claude settings root must be an object: {path}")
    events = settings.get("hooks", {})
    if not isinstance(events, dict):
        raise GuidanceUpdateError(f"Claude settings hooks must be an object: {path}")
    entries = events.get("PreToolUse", [])
    if not isinstance(entries, list):
        raise GuidanceUpdateError(f"Claude settings PreToolUse must be a list: {path}")
    remaining = []
    for entry in entries:
        hooks = entry.get("hooks") if isinstance(entry, dict) else None
        if not isinstance(hooks, list):
            remaining.append(entry)
            continue
        kept = [hook for hook in hooks if not _is_graphify_guard(hook)]
        if len(kept) == len(hooks):
            remaining.append(entry)
        elif kept:
            remaining.append({**entry, "hooks": kept})
    if remaining == entries:
        return
    if remaining:
        events["PreToolUse"] = remaining
    else:
        events.pop("PreToolUse", None)
    _atomic_write(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")


def _serena_guidance(client: str) -> str:
    loading = (
        "필요한 도구가 deferred 상태면 ToolSearch로 해당 도구를 로드한다."
        if client == "claude"
        else "필요한 도구가 바로 노출되지 않으면 제공된 도구 탐색 기능으로 확인한다."
    )
    return f"""### Serena MCP

같은 워크트리에서 실행한 Codex와 Claude는 동일한 Serena 서버를 공유한다. dotsync launcher는 워크트리마다 별도 서버를 기동하고 현재 프로젝트에 고정한다. `activate_project`, `get_current_config`, `search_for_pattern`, `replace_content`는 공통 context에서 제외되어 있으므로 호출하지 않는다.

Serena가 필요한 작업으로 판단한 뒤 {loading} 세션에서 처음 사용할 때 `initial_instructions`가 제공되면 한 번 읽고 따른다.

- 파일 구조 파악: `get_symbols_overview`. 특정 심볼 구현 확인: `find_symbol`.
- 호출처·참조 확인: `find_referencing_symbols`. 선언·구현 추적: `find_declaration`/`find_implementations`. 시그니처·공개 계약 변경이나 심볼 이름 변경·삭제·이동 전에는 필요한 참조를 확인한다.
- 함수·클래스 전체 변경: `replace_symbol_body`. 심볼 앞뒤 추가: `insert_before_symbol`/`insert_after_symbol`. 수정 후 필요하면 `get_diagnostics_for_file`로 진단한다.
- 도구 탐색·연결에 실패하거나 권한상 사용할 수 없으면 짧게 알리고 기본 도구로 진행한다. 서브에이전트에도 같은 조건을 적용하며, 메인 에이전트가 이미 확인해 제공한 결과는 우선 활용한다.

작업 완료 보고에는 Serena 사용 여부 또는 생략 이유를 한 줄로 남긴다.
"""


def guidance_block(client: str) -> str:
    """Return the managed Markdown block for one agent client."""
    if client not in {"codex", "claude"}:
        raise ValueError(f"unsupported client: {client}")
    return (
        f"{GUIDANCE_START}\n"
        f"{TOOL_ROUTING_GUIDANCE.rstrip()}\n\n"
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


def _remove_legacy_grep_guidance(settings: dict[str, Any]) -> None:
    """Retire only our repeated Serena reminder, preserving sibling hooks."""
    events = settings.get("hooks", {})
    entries = events.get("PreToolUse", [])
    remaining = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("matcher") != "Grep":
            remaining.append(entry)
            continue
        hooks = entry.get("hooks", [])
        kept = [
            hook for hook in hooks
            if not (
                isinstance(hook, dict)
                and hook.get("type") == "command"
                and (
                    hook.get("command") == LEGACY_PRE_TOOL_USE_COMMAND
                    or str(hook.get("command", "")).startswith(HOOK_MARKER + ";")
                )
            )
        ]
        if len(kept) == len(hooks):
            remaining.append(entry)
        elif kept:
            remaining.append({**entry, "hooks": kept})
    if remaining == entries:
        return
    if remaining:
        events["PreToolUse"] = remaining
    else:
        events.pop("PreToolUse", None)


def update_claude_settings(text: str) -> str:
    """Update only the launcher-owned commands in Claude settings JSON."""
    try:
        settings = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GuidanceUpdateError(f"invalid Claude settings JSON: {exc}") from exc
    if not isinstance(settings, dict):
        raise GuidanceUpdateError("Claude settings root must be an object")
    _remove_legacy_grep_guidance(settings)
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
