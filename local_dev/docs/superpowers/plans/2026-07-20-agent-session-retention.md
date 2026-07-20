# Agent Session Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Codex와 Claude launcher가 현재 프로젝트에 한정하지 않고 각 제품이 볼 수 있는 모든 세션에 5일 보존 정책을 안전하고 빠르게 적용하게 한다.

**Architecture:** `session_inventory.py`는 저장소 발견, metadata-first scan, Codex logical group 구성, active rollout snapshot을 담당한다. 새 `session_cleanup.py`는 저장된 인벤토리를 재검증한 뒤 공식 Codex CLI 삭제를 실행하고 Claude child argv에 native retention setting을 추가한다. `serena_agent_launcher.py`는 인벤토리를 한 번만 만들고 preflight와 cleanup에서 공유하며, `serena_zsh_shim.py`는 명세에 포함된 interactive resume/fork/continue 호출만 launcher로 보낸다.

**Tech Stack:** Python 3.12+ stdlib, pytest, zsh, macOS `lsof`, Codex CLI `delete --force`, Claude Code `--settings`.

## Global Constraints

- 대상 보존 기간은 정확히 `5 * 24시간`이며 cutoff와 같은 mtime은 보존한다.
- `local_dev/`만 변경하고 공개 dotsync runtime, root README, root Makefile, Homebrew formula는 변경하지 않는다.
- Codex JSONL/SQLite와 Claude JSONL/subagent directory를 launcher가 직접 삭제하지 않는다.
- Codex memory, Claude auto-memory, Codex `archived_sessions`, Orca `orchestration.db`, 인증·계정·캐시·history를 건드리지 않는다.
- Codex 삭제는 resolved real binary의 `delete --force <UUID>`만 사용한다.
- Claude 삭제는 `--settings '{"cleanupPeriodDays":5}'`로 활성화되는 native startup retention에 맡긴다.
- destructive test는 임시 HOME과 fake CLI만 사용하며 실제 사용자 세션을 읽거나 삭제하지 않는다.
- runtime dependency는 stdlib-only로 유지한다.

---

### Task 1: 공통 인벤토리 계약과 Claude 전역 프로젝트 스캔

**Files:**
- Modify: `local_dev/serena_mcp_management/session_inventory.py`
- Modify: `local_dev/tests/test_session_inventory.py`

**Interfaces:**
- Produces: `CountStats`, `FileIdentity`, `FileFingerprint`, `CodexSessionFile`, `OwnerDeletePlan`, `CodexCleanupTarget`, `AgentInventory`.
- Produces: `scan_inventory(*, client, home, codex_home, claude_config_dir=None, orca_codex_home=None, now=None, open_file_identities=None) -> AgentInventory`.
- Produces: `snapshot_open_rollouts(session_dirs, *, runner=subprocess.run) -> frozenset[FileIdentity]` for Task 2/3.
- Removes: memory-related fields and generic raw `cleanup_inventory()`.

- [ ] **Step 1: 기존 동작을 대체하는 Claude 실패 테스트 작성**

```python
NOW = 2_000_000_000.0
ROOT_A = "00000000-0000-4000-8000-000000000001"
ROOT_B = "00000000-0000-4000-8000-000000000002"
CHILD = "00000000-0000-4000-8000-000000000003"

def _session_meta(session_id: str, parent_id: str | None = None) -> dict:
    payload = {"id": session_id, "cwd": "/repo"}
    if parent_id is not None:
        payload["source"] = {
            "subagent": {"thread_spawn": {"parent_thread_id": parent_id}}
        }
    return {"type": "session_meta", "payload": payload}

def test_scan_claude_counts_all_projects_without_memory_or_subagents(tmp_path):
    config = tmp_path / ".claude"
    old = config / "projects" / "-repo-a" / "old.jsonl"
    new = config / "projects" / "-repo-b" / "new.jsonl"
    subagent = config / "projects" / "-repo-a" / "old" / "subagents" / "agent.jsonl"
    memory = config / "projects" / "-repo-a" / "memory" / "MEMORY.md"
    _write_jsonl(old, [_session_meta(ROOT_A)], age_days=6, now=NOW)
    _write_jsonl(new, [_session_meta(ROOT_B)], age_days=1, now=NOW)
    _write_jsonl(subagent, [_session_meta(CHILD)], age_days=6, now=NOW)
    memory.parent.mkdir(parents=True)
    memory.write_text("keep")

    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=NOW,
    )

    assert inventory.sessions == CountStats(total=2, to_delete=1, to_keep=1)
    assert inventory.criteria == "sessions: all projects + native retention 5d"
    assert not hasattr(inventory, "memory")
    assert memory.read_text() == "keep"
```

같은 RED 단계에서 cutoff와 같은 mtime은 keep, 1ns라도 오래되면 delete로
분류하는 테스트를 추가한다. absolute `claude_config_dir`는 그 경로의 모든
project를 검색하고 relative path는 `ValueError`를 내는 테스트도 추가한다.

- [ ] **Step 2: Claude 대상 테스트가 이전 project-local/memory 계약 때문에 실패하는지 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_session_inventory.py -q`

Expected: FAIL because `AgentInventory` still exposes memory fields, scans one encoded cwd only, and uses 3 days.

- [ ] **Step 3: 공통 모델과 Claude scanner를 최소 구현**

```python
RETENTION_DAYS = 5
RETENTION_SECONDS = RETENTION_DAYS * 86400

@dataclass(frozen=True)
class CountStats:
    total: int
    to_delete: int = 0
    to_keep: int = 0

@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int

@dataclass(frozen=True)
class FileFingerprint:
    identity: FileIdentity
    size: int
    mtime_ns: int

@dataclass(frozen=True)
class CodexSessionFile:
    session_id: str
    parent_id: str | None
    path: Path
    codex_home: Path
    fingerprint: FileFingerprint

@dataclass(frozen=True)
class OwnerDeletePlan:
    codex_home: Path
    local_root_ids: tuple[str, ...]
    is_orca: bool

@dataclass(frozen=True)
class CodexCleanupTarget:
    root_id: str
    files: tuple[CodexSessionFile, ...]
    owners: tuple[OwnerDeletePlan, ...]

@dataclass(frozen=True)
class AgentInventory:
    client: str
    sessions: CountStats
    criteria: str
    codex_targets: tuple[CodexCleanupTarget, ...] = ()
    scanned_paths: tuple[Path, ...] = ()
    session_dirs: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()
```

`scan_inventory(client="claude", ...)`는 `claude_config_dir`가 없으면
`home / ".claude"`를 사용하고 `projects/*/*.jsonl`만 `stat`한다. 판정은
`st_mtime < now - RETENTION_SECONDS`이며 파일을 열지 않는다. 상대
`CLAUDE_CONFIG_DIR`은 `ValueError`로 거부한다.

- [ ] **Step 4: Claude scanner green 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_session_inventory.py -q`

Expected: Claude all-project, cutoff-boundary, custom-config-root tests PASS; 아직 작성하지 않은 Codex 기능은 이 task에서 검증하지 않는다.

- [ ] **Step 5: Task 1 커밋**

```bash
git add local_dev/serena_mcp_management/session_inventory.py local_dev/tests/test_session_inventory.py
git commit -m "refactor(local_dev): define five-day session inventory contract"
```

---

### Task 2: Codex known-home scan, logical grouping, active rollout 보호

**Files:**
- Modify: `local_dev/serena_mcp_management/session_inventory.py`
- Modify: `local_dev/tests/test_session_inventory.py`

**Interfaces:**
- Consumes: Task 1 data classes and `RETENTION_SECONDS`.
- Produces: metadata-first Codex branch of `scan_inventory()`.
- Produces: `snapshot_open_rollouts()` and stable fingerprints for Task 3 revalidation.

- [ ] **Step 1: global roots, grouping, hard-link dedup 실패 테스트 작성**

```python
def test_scan_codex_groups_all_homes_by_root_and_descendant_activity(tmp_path):
    default_home = tmp_path / ".codex"
    orca_home = tmp_path / "Library/Application Support/orca/codex-runtime-home/home"
    root = default_home / "sessions/2026/07/01/root.jsonl"
    bridged = orca_home / "sessions/2026/07/01/root.jsonl"
    child = orca_home / "sessions/2026/07/02/child.jsonl"
    _write_jsonl(root, [_session_meta(ROOT_A)], age_days=8, now=NOW)
    bridged.parent.mkdir(parents=True)
    os.link(root, bridged)
    _write_jsonl(child, [_session_meta(CHILD, parent_id=ROOT_A)], age_days=1, now=NOW)

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=orca_home,
        orca_codex_home=orca_home,
        now=NOW,
        open_file_identities=frozenset(),
    )

    assert inventory.sessions == CountStats(total=1, to_delete=0, to_keep=1)
    assert len(inventory.codex_targets) == 0
    assert inventory.criteria == "sessions: all known homes + inactive longer than 5d"
```

다음 독립 테스트도 같은 RED 단계에 추가한다.

- 첫 줄 뒤에 매우 큰 invalid body가 있어도 첫 줄 metadata만으로 성공한다.
- root와 모든 descendant가 6일 이상이면 logical group 1개가 delete target이다.
- open identity가 group member와 같으면 전체 group이 keep이다.
- malformed UUID, missing parent, parent cycle, conflicting parent는 warning 후 keep이다.
- `archived_sessions`는 검색하지 않는다.
- default/active/Orca home canonical path 중복을 제거한다.
- 같은 UUID의 symlink/hard-link copy를 physical session 두 개로 세지 않는다.
- owner home에 global root가 없으면 그 home의 local top-level descendant가
  `OwnerDeletePlan.local_root_ids`에 들어간다.

- [ ] **Step 2: Codex tests가 현재 cwd filter와 per-file count 때문에 실패하는지 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_session_inventory.py -q`

Expected: FAIL because current scanner reads every JSONL line, filters one cwd/home, counts files, and knows no parent relationship.

- [ ] **Step 3: metadata-first graph 구성 최소 구현**

Codex branch는 다음 순서로 구현한다.

```python
homes = _codex_homes(home, codex_home, orca_codex_home)
session_dirs = tuple(path / "sessions" for path in homes if (path / "sessions").is_dir())
open_ids = open_file_identities
if open_ids is None:
    open_ids = snapshot_open_rollouts(session_dirs)
files, warnings = _read_codex_session_files(session_dirs)
records = _merge_codex_records_by_uuid_and_identity(files, warnings)
groups = _group_codex_records(records, warnings)
targets = tuple(
    _cleanup_target(group, orca_codex_home)
    for group in groups
    if max(file.fingerprint.mtime_ns for file in group) < cutoff_ns
    and not any(file.fingerprint.identity in open_ids for file in group)
)
```

`_read_codex_session_files()`는 각 `sessions/**/*.jsonl`에서 `readline()` 한 번만
호출하고 첫 row가 `session_meta`인지 검증한다. UUID는 `uuid.UUID()`로 검증한다.
`parent_thread_id`는 payload의 nested dict/list를 재귀 순회해 첫 문자열 값을
찾는다. graph root 탐색은 visited set을 사용해 cycle을 감지한다. owner 순서는
default home, 기타 active home, Orca managed home 순이다.

- [ ] **Step 4: `lsof` snapshot 실패-폐쇄 테스트와 구현**

```python
def test_snapshot_open_rollouts_fails_closed_on_lsof_error(tmp_path):
    def fail(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 2, "", "permission denied")

    with pytest.raises(ActiveSessionScanError, match="permission denied"):
        snapshot_open_rollouts((tmp_path,), runner=fail)
```

실행 명령은 `/usr/sbin/lsof` fallback을 포함한 resolved `lsof`에
`-n`, `-F`, `n`, `+D <session_dir>`를 전달한다. stdout의 `n<path>` row만
`stat`해 `FileIdentity`로 바꾼다. return code 0과 출력·stderr가 모두 없는 1은
성공으로 보고, 그 밖의 실패는 `ActiveSessionScanError`로 올린다. Codex
`scan_inventory()`는 이 오류를 warning으로 삼고 모든 group을 keep 처리한다.

- [ ] **Step 5: Codex inventory green 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_session_inventory.py -q`

Expected: all inventory tests PASS and no test touches a real user path.

- [ ] **Step 6: Task 2 커밋**

```bash
git add local_dev/serena_mcp_management/session_inventory.py local_dev/tests/test_session_inventory.py
git commit -m "feat(local_dev): inventory global Codex session groups"
```

---

### Task 3: 공식 Codex 삭제와 Claude native-retention argv

**Files:**
- Create: `local_dev/serena_mcp_management/session_cleanup.py`
- Create: `local_dev/tests/test_session_cleanup.py`
- Modify: `local_dev/serena_mcp_management/session_inventory.py`

**Interfaces:**
- Consumes: `AgentInventory`, `CodexCleanupTarget`, fingerprints, session dirs.
- Produces: `CleanupResult(deleted: int, native_eligible: int, warnings: tuple[str, ...])`.
- Produces: `cleanup_codex_inventory(inventory, *, codex_binary, runner=subprocess.run, open_file_snapshot=snapshot_open_rollouts) -> CleanupResult`.
- Produces: `claude_retention_args(args: list[str]) -> list[str]`.

- [ ] **Step 1: fake CLI를 사용하는 공식 삭제 RED 테스트 작성**

```python
def test_cleanup_calls_official_delete_source_before_orca(tmp_path):
    default_home = tmp_path / ".codex"
    orca_home = tmp_path / "Library/Application Support/orca/codex-runtime-home/home"
    source = default_home / "sessions/2026/07/01/root.jsonl"
    bridged = orca_home / "sessions/2026/07/01/root.jsonl"
    _write_jsonl(source, [_session_meta(ROOT_A)], age_days=6, now=NOW)
    bridged.parent.mkdir(parents=True)
    os.link(source, bridged)
    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=orca_home,
        orca_codex_home=orca_home,
        now=NOW,
        open_file_identities=frozenset(),
    )
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs["env"]["CODEX_HOME"]))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=run,
        open_file_snapshot=lambda _: frozenset(),
    )

    assert calls[0][0] == ["/fake/codex", "delete", "--help"]
    assert calls[1] == (["/fake/codex", "delete", "--force", ROOT_A], str(default_home))
    assert calls[2] == (["/fake/codex", "delete", "--force", ROOT_A], str(orca_home))
    assert result.deleted == 1
```

함께 추가할 독립 테스트:

- capability probe 실패 시 delete call 0개, raw unlink 0개, warning 반환.
- source delete 실패 시 Orca delete를 호출하지 않음.
- managed delete 실패 시 logical group deleted count를 올리지 않음.
- fingerprint/path set이 바뀌거나 rollout이 새로 open되면 전체 cleanup skip.
- delete timeout/exception은 warning으로 바꾸고 다음 agent launch를 막지 않음.
- local root fragment가 여러 개면 각각 공식 delete 호출.
- `claude_retention_args([])`가
  `["--settings", '{"cleanupPeriodDays":5}']`를 반환.
- 이미 `--settings`/`--settings=...`가 있으면 argv를 그대로 반환.

- [ ] **Step 2: 새 모듈 부재로 RED 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_session_cleanup.py -q`

Expected: collection FAIL because `session_cleanup` does not exist. Create only an empty importable module, rerun, then verify behavior tests FAIL because functions are missing.

- [ ] **Step 3: 재검증과 공식 명령 실행 최소 구현**

```python
CLAUDE_RETENTION_JSON = '{"cleanupPeriodDays":5}'
DELETE_TIMEOUT_SECONDS = 30

@dataclass(frozen=True)
class CleanupResult:
    deleted: int = 0
    native_eligible: int = 0
    warnings: tuple[str, ...] = ()

def claude_retention_args(args: list[str]) -> list[str]:
    if any(arg == "--settings" or arg.startswith("--settings=") for arg in args):
        return list(args)
    return ["--settings", CLAUDE_RETENTION_JSON, *args]
```

`cleanup_codex_inventory()`는 capability probe를 한 번 실행한 뒤 현재
`sessions/**/*.jsonl` path set, 각 candidate fingerprint, 한 번의 current-open
snapshot을 최초 인벤토리와 비교한다. 불일치 시 삭제하지 않는다. group별로
owner/local-root 순서대로 `runner([...], env={**os.environ,
"CODEX_HOME": str(owner.codex_home)}, capture_output=True, text=True,
timeout=30)`을 호출한다. Orca owner 앞 source owner가 실패하면 해당 Orca
owner는 skip한다. 어떠한 실패 경로도 `Path.unlink`, `shutil.rmtree`, SQLite를
호출하지 않는다.

- [ ] **Step 4: cleanup green 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_session_cleanup.py local_dev/tests/test_session_inventory.py -q`

Expected: PASS.

- [ ] **Step 5: Task 3 커밋**

```bash
git add local_dev/serena_mcp_management/session_cleanup.py local_dev/serena_mcp_management/session_inventory.py local_dev/tests/test_session_cleanup.py
git commit -m "feat(local_dev): clean sessions through native agent APIs"
```

---

### Task 4: launcher snapshot 재사용, UI 계약, interactive shim 범위

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py`
- Modify: `local_dev/serena_mcp_management/serena_zsh_shim.py`
- Modify: `local_dev/tests/test_launcher_phases.py`
- Modify: `local_dev/tests/test_serena_launcher.py`
- Modify: `local_dev/tests/test_serena_zsh_shim.py`

**Interfaces:**
- Consumes: Task 1 `AgentInventory`, Task 3 `CleanupResult`,
  `cleanup_codex_inventory()`, `claude_retention_args()`.
- Produces: one-scan `InventorySnapshot(inventory, error)` shared by preflight and launch prep.
- Produces: child command that arms Claude native retention in scoped and bare launch paths.

- [ ] **Step 1: launcher UI와 one-scan RED 테스트 작성**

```python
def test_v2_main_passes_preflight_snapshot_to_cleanup_before_bare_launch(
    monkeypatch, tmp_path
):
    inventory = AgentInventory(
        client="codex",
        sessions=CountStats(total=58, to_delete=41, to_keep=17),
        criteria="sessions: all known homes + inactive longer than 5d",
    )
    snapshot = launcher.InventorySnapshot(inventory=inventory)
    seen = []

    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setattr(launcher, "_render_preflight_overview_v2", lambda: snapshot)
    monkeypatch.setattr(launcher, "_run_serena_cli_install_v2", lambda: None)
    monkeypatch.setattr(launcher, "_run_serena_init_v2", lambda: "managed")
    monkeypatch.setattr(launcher, "_run_preflight_v2", lambda **kwargs: 0)
    monkeypatch.setattr(launcher, "_run_final_confirm_v2", lambda: True)
    monkeypatch.setattr(launcher, "find_real_binary", lambda client: "/fake/codex")
    monkeypatch.setattr(
        launcher,
        "_run_launch_prep_v2",
        lambda *, snapshot, real_binary: seen.append((snapshot, real_binary))
        or launcher.LaunchPrepSummary(),
    )
    monkeypatch.setattr(launcher, "serena_server_command", lambda: None)
    monkeypatch.setattr(launcher, "_launch_bare_child", lambda *args, **kwargs: 0)

    assert launcher._main_v2([]) == 0
    assert seen == [(snapshot, "/fake/codex")]
```

다음 UI/child tests도 추가한다.

- Codex preflight: `58 total . 41 to delete . 17 to keep`와 all-known-homes 5d criteria.
- Claude preflight: `108 total . 74 native cleanup . 34 to keep`와 all-projects 5d criteria.
- memory row와 `memory files reset` 문구가 preflight, prep, summary 어디에도 없음.
- scan failure는 sessions warning만 표시하고 cleanup을 호출하지 않음.
- Codex prep은 official cleanup warning을 summary warning에 전달함.
- Claude prep은 파일을 삭제하지 않고 `native retention 5d . N eligible`을 표시함.
- scoped Claude child command와 bare Claude child command 모두 retention args를 포함함.
- cleanup은 Serena unavailable/skipped bare-launch 경로에서도 실행됨.

- [ ] **Step 2: launcher tests RED 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py local_dev/tests/test_serena_launcher.py -q`

Expected: FAIL because current launcher scans twice, raw-deletes, resets memory, and arms no Claude retention.

- [ ] **Step 3: launcher orchestration 최소 변경**

```python
@dataclass(frozen=True)
class InventorySnapshot:
    inventory: AgentInventory | None
    error: str | None = None

@dataclass(frozen=True)
class LaunchPrepSummary:
    cleanup_deleted: int = 0
    native_eligible: int = 0
    warnings: tuple[str, ...] = ()
```

`_render_preflight_overview_v2()`는 `InventorySnapshot`을 반환한다. `_main_v2()`는
그 snapshot과 미리 resolved한 real binary를 `_run_launch_prep_v2()`에 넘긴다.
prep은 Codex일 때만 `cleanup_codex_inventory()`를 호출하고 Claude일 때는
`native_eligible=inventory.sessions.to_delete`만 기록한다. prep을 Serena
start/degrade 분기 앞에 배치한다. `build_child_command()`와
`_launch_bare_child()`는 Claude child args에 `claude_retention_args()`를 적용한다.

- [ ] **Step 4: shim interactive 범위 RED 테스트 작성**

```python
@pytest.mark.parametrize("client,args", [
    ("codex", ""),
    ("codex", "resume"),
    ("codex", "fork"),
    ("claude", ""),
    ("claude", "-c"),
    ("claude", "--continue"),
    ("claude", "-r session-id"),
    ("claude", "--resume session-id"),
])
def test_zsh_matcher_accepts_session_managing_interactive_commands(
    tmp_path, client, args
):
    shim_path, *_ = _write_zsh_fixture(tmp_path)
    result = subprocess.run(
        [
            "zsh",
            "-fc",
            f"source {shlex.quote(str(shim_path))}; "
            f"_dotsync_agent_should_manage_launch 1 {client} {args}",
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
```

non-interactive `codex exec`, `claude -p`, `--help`, `--version`, 그리고
어디에든 user `--settings`가 있는 Claude command는 real binary로 직접 가는
독립 테스트를 작성한다.

- [ ] **Step 5: shim matcher 최소 구현**

`_dotsync_agent_should_manage_launch`는 `(interactive, client, argv...)`를 받고
interactive가 아니면 실패한다. 빈 argv는 성공한다. Claude argv에
`--settings` 또는 `--settings=*`가 있으면 실패한다. 나머지는 첫 argv가
Codex `resume|fork`, Claude `-c|--continue|-r|--resume`일 때만 성공한다.
`claude()`와 `codex()` 호출부는 client와 `"$@"`를 모두 전달한다.

- [ ] **Step 6: launcher/shim green과 관련 regression 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py local_dev/tests/test_serena_launcher.py local_dev/tests/test_serena_zsh_shim.py -q`

Expected: PASS.

- [ ] **Step 7: Task 4 커밋**

```bash
git add local_dev/serena_mcp_management/serena_agent_launcher.py local_dev/serena_mcp_management/serena_zsh_shim.py local_dev/tests/test_launcher_phases.py local_dev/tests/test_serena_launcher.py local_dev/tests/test_serena_zsh_shim.py
git commit -m "feat(local_dev): apply five-day retention on agent launch"
```

---

### Task 5: 문서, 성능 회귀, 전체 검증, runtime 설치

**Files:**
- Modify: `local_dev/README.md`
- Modify: `local_dev/tests/test_session_inventory.py`
- Modify: `graphify-out/*` via `graphify update .`

**Interfaces:**
- Consumes: completed launcher/session behavior.
- Produces: local_dev operator documentation and installed stable runtime copy.

- [ ] **Step 1: metadata-first 성능 성질 테스트 추가**

```python
def test_codex_scan_reads_only_first_jsonl_record(tmp_path):
    rollout = tmp_path / ".codex/sessions/2026/07/01/root.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(json.dumps(_session_meta(ROOT_A)) + "\n" + "{" * 5_000_000)

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=NOW,
        open_file_identities=frozenset(),
    )

    assert inventory.sessions.total == 1
```

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_session_inventory.py::test_codex_scan_reads_only_first_jsonl_record -q`

Expected: PASS without parsing or raising on the invalid multi-megabyte body.

- [ ] **Step 2: local_dev README 갱신**

`local_dev/README.md`에서 interactive 범위를 no-argument 전용에서
resume/fork/continue 포함으로 바꾼다. session 표는 Codex all-known-homes logical
groups/official delete, Claude all-projects/native 5d retention으로 교체한다. memory
row/reset 설명과 final `M memory files reset` 문구를 제거하고, archive와 memory가
보존됨을 명시한다. runtime 설치 절차는 기존 `make install-shim` 한 단계로
유지한다.

- [ ] **Step 3: formatter-free syntax와 targeted tests 검증**

Run: `.venv/bin/python3 -m py_compile local_dev/serena_mcp_management/session_inventory.py local_dev/serena_mcp_management/session_cleanup.py local_dev/serena_mcp_management/serena_agent_launcher.py local_dev/serena_mcp_management/serena_zsh_shim.py`

Expected: exit 0.

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_session_inventory.py local_dev/tests/test_session_cleanup.py local_dev/tests/test_launcher_phases.py local_dev/tests/test_serena_launcher.py local_dev/tests/test_serena_zsh_shim.py -q`

Expected: PASS.

- [ ] **Step 4: local_dev와 공개 dotsync 전체 회귀 검증**

Run: `.venv/bin/python3 -m pytest local_dev/tests -q`

Expected: PASS.

Run: `.venv/bin/python3 -m pytest tests -q`

Expected: PASS; public dotsync behavior unchanged.

- [ ] **Step 5: 실제 사용자 데이터에 대한 read-only dry inventory 측정**

실제 launcher cleanup을 실행하지 않는다. `scan_inventory()`만 호출하는 read-only
명령으로 Codex logical group count, Claude top-level count, scan duration을
측정한다. 경로나 session content는 출력하지 않고 count와 elapsed seconds만
기록한다. Codex metadata scan이 전체 JSONL body parsing 때의 수 초 지연으로
회귀하면 완료 처리하지 않는다.

- [ ] **Step 6: graphify 갱신과 diff 검토**

Run: `graphify update .`

Expected: exit 0 and graph files reflect new session modules/contracts.

Run: `git diff --check && git status --short && git diff --stat`

Expected: whitespace errors 없음; 사용자 소유 `AGENTS.md` 외에는 이 기능 관련 파일만 변경됨.

- [ ] **Step 7: Task 5 커밋**

```bash
git add local_dev/README.md local_dev/tests/test_session_inventory.py graphify-out
git commit -m "docs(local_dev): document native session retention"
```

- [ ] **Step 8: stable runtime shim 설치**

Run: `make -C local_dev install-shim`

Expected: runtime mirror가 갱신되고 managed `~/.zshrc` block이 새 interactive matcher를 포함한다. 이 명령 자체는 Codex/Claude를 실행하거나 세션을 삭제하지 않는다.

- [ ] **Step 9: 설치 결과 read-only 검증**

Run: `rg -n "cleanupPeriodDays|resume|fork|continue" "$HOME/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management" "$HOME/.zshrc"`

Expected: runtime module과 managed shim에 새 정책이 존재한다. 사용자 세션 디렉터리의 삭제 여부는 이 단계에서 검사하거나 변경하지 않는다.
