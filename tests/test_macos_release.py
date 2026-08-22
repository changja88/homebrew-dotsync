from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import signal
from pathlib import Path

import pytest

from scripts import macos_release_support, render_cask as render_cask_module
from scripts.render_cask import render_cask


VALID_VERSION = "0.3.0"
VALID_SHA256 = "a" * 64
VALID_URL = (
    "https://github.com/changja88/homebrew-dotsync/releases/download/"
    "v0.3.0/DotSync-0.3.0-macOS.zip"
)
REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release_macos_app.sh"
RENDERER_SCRIPT = REPO_ROOT / "scripts" / "render_cask.py"
RELEASE_SUPPORT_SCRIPT = REPO_ROOT / "scripts" / "macos_release_support.py"
CASK_TEMPLATE = REPO_ROOT / "packaging" / "dotsync-app.rb.in"
REPOSITORY_SLUG = "changja88/homebrew-dotsync"
TEST_IDENTITY = "Developer ID Application: Release Test (TEAMID1234)"
TEST_NOTARY_PROFILE = "dotsync-release-test"
FIXTURE_EXECUTABLE_BYTES = b"fixture-universal-binary\n"
MALICIOUS_EXECUTABLE_BYTES = b"substituted-executable\n"
FINAL_ARCHIVE_BYTES = b"archive:" + FIXTURE_EXECUTABLE_BYTES
FINAL_ARCHIVE_SHA256 = hashlib.sha256(FINAL_ARCHIVE_BYTES).hexdigest()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    (repository / "Casks").mkdir(parents=True)
    (repository / "packaging").mkdir()
    (repository / "packaging" / "dotsync-app.rb.in").write_bytes(
        (
            Path(__file__).resolve().parent.parent
            / "packaging"
            / "dotsync-app.rb.in"
        ).read_bytes()
    )
    return repository


def _render(repository: Path, **overrides: object) -> Path:
    output = repository / "Casks" / "dotsync-app.rb"
    arguments: dict[str, object] = {
        "version": VALID_VERSION,
        "sha256": VALID_SHA256,
        "url": VALID_URL,
        "output": output,
        "repository_root": repository,
    }
    arguments.update(overrides)
    render_cask(**arguments)
    return output


def test_renderer_writes_exact_formula_dependent_cask_from_release_inputs(tmp_path):
    repository = _repository(tmp_path)

    output = _render(repository)

    assert output.read_text(encoding="utf-8") == (
        'cask "dotsync-app" do\n'
        '  version "0.3.0"\n'
        f'  sha256 "{VALID_SHA256}"\n'
        "\n"
        f'  url "{VALID_URL}"\n'
        '  name "DotSync"\n'
        '  desc "Menu bar companion for DotSync config sync and Codex subscription usage"\n'
        '  homepage "https://github.com/changja88/homebrew-dotsync"\n'
        "\n"
        '  depends_on macos: ">= :ventura"\n'
        '  depends_on formula: "changja88/dotsync/dotsync"\n'
        "\n"
        '  app "DotSync.app"\n'
        "end\n"
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o644
    assert "__DOTSYNC_" not in output.read_text(encoding="utf-8")
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize(
    "version",
    [
        "0.0.0",
        "0.3",
        "0.3.0.0",
        "00.3.0",
        "0.03.0",
        "0.3.00",
        "+0.3.0",
        "v0.3.0",
        "1.2.-3",
        "1.2.3\n",
    ],
)
def test_renderer_rejects_noncanonical_or_zero_version(version, tmp_path):
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="version"):
        _render(repository, version=version)

    assert not (repository / "Casks" / "dotsync-app.rb").exists()


@pytest.mark.parametrize(
    "sha256",
    [
        "0" * 64,
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "not-a-sha",
        "a" * 63 + "\n",
    ],
)
def test_renderer_rejects_noncanonical_or_zero_sha256(sha256, tmp_path):
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="SHA-256"):
        _render(repository, sha256=sha256)

    assert not (repository / "Casks" / "dotsync-app.rb").exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/DotSync-0.3.0-macOS.zip",
        (
            "http://github.com/changja88/homebrew-dotsync/releases/download/"
            "v0.3.0/DotSync-0.3.0-macOS.zip"
        ),
        (
            "https://github.com/changja88/homebrew-dotsync/releases/download/"
            "v0.3.1/DotSync-0.3.0-macOS.zip"
        ),
        (
            "https://github.com/changja88/homebrew-dotsync/releases/download/"
            "v0.3.0/DotSync-0.3.1-macOS.zip"
        ),
        f"{VALID_URL}?download=1",
        f"{VALID_URL}#asset",
        f"{VALID_URL}\n",
    ],
)
def test_renderer_rejects_every_nonexact_release_asset_url(url, tmp_path):
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="URL"):
        _render(repository, url=url)

    assert not (repository / "Casks" / "dotsync-app.rb").exists()


@pytest.mark.parametrize(
    "relative_output",
    [
        "dotsync-app.rb",
        "Casks/other.rb",
        "nested/Casks/dotsync-app.rb",
        "Casks/subdirectory/dotsync-app.rb",
        "../Casks/dotsync-app.rb",
    ],
)
def test_renderer_rejects_output_other_than_exact_cask_path(
    relative_output, tmp_path
):
    repository = _repository(tmp_path)
    output = repository / relative_output

    with pytest.raises(ValueError, match="output"):
        _render(repository, output=output)

    assert not (repository / "Casks" / "dotsync-app.rb").exists()


def test_renderer_rejects_symlinked_casks_directory_without_touching_target(tmp_path):
    repository = _repository(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (repository / "Casks").rmdir()
    (repository / "Casks").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="Casks"):
        _render(repository)

    assert list(external.iterdir()) == []


def test_renderer_refuses_existing_cask_and_preserves_its_bytes(tmp_path):
    repository = _repository(tmp_path)
    output = repository / "Casks" / "dotsync-app.rb"
    output.write_bytes(b"existing-cask\n")

    with pytest.raises(FileExistsError):
        _render(repository)

    assert output.read_bytes() == b"existing-cask\n"
    assert list(output.parent.iterdir()) == [output]


def test_renderer_replaces_existing_cask_only_when_explicitly_requested(tmp_path):
    repository = _repository(tmp_path)
    output = repository / "Casks" / "dotsync-app.rb"
    output.write_bytes(b"existing-cask\n")

    rendered = _render(repository, replace_existing=True)

    assert rendered == output
    assert output.read_text(encoding="utf-8").startswith(
        'cask "dotsync-app" do\n  version "0.3.0"\n'
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o644
    assert list(output.parent.iterdir()) == [output]


def test_renderer_atomic_publish_failure_leaves_no_output_or_temporary_file(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)

    def fail_publish(*_args, **_kwargs):
        raise OSError("injected atomic publication failure")

    monkeypatch.setattr(os, "link", fail_publish)

    with pytest.raises(OSError, match="atomic publication"):
        _render(repository)

    assert list((repository / "Casks").iterdir()) == []


def test_renderer_rolls_back_interruption_immediately_after_new_link(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    real_link = os.link

    def interrupt_after_link(*args, **kwargs):
        real_link(*args, **kwargs)
        raise InterruptedError("injected interruption after publication link")

    monkeypatch.setattr(os, "link", interrupt_after_link)

    with pytest.raises(InterruptedError, match="after publication link"):
        _render(repository)

    assert list((repository / "Casks").iterdir()) == []


def test_renderer_restores_prior_cask_when_interrupted_immediately_after_swap(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    output = repository / "Casks" / "dotsync-app.rb"
    prior_bytes = b"prior-before-interrupted-swap\n"
    output.write_bytes(prior_bytes)
    output.chmod(0o640)
    real_swap = render_cask_module._swap_entries
    swap_calls = 0

    def interrupt_after_first_swap(*args, **kwargs):
        nonlocal swap_calls
        swap_calls += 1
        real_swap(*args, **kwargs)
        if swap_calls == 1:
            raise InterruptedError("injected interruption after publication swap")

    monkeypatch.setattr(render_cask_module, "_swap_entries", interrupt_after_first_swap)

    with pytest.raises(InterruptedError, match="after publication swap"):
        _render(repository, replace_existing=True)

    assert output.read_bytes() == prior_bytes
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
    assert list(output.parent.iterdir()) == [output]


def test_renderer_rejects_template_rebound_after_descriptor_open(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    template = repository / "packaging" / "dotsync-app.rb.in"
    attacker_template = repository / "packaging" / "attacker.rb.in"
    attacker_template.write_text(
        "# attacker-controlled template\n"
        'version "__DOTSYNC_VERSION__"\n'
        'sha256 "__DOTSYNC_SHA256__"\n'
        'url "__DOTSYNC_URL__"\n',
        encoding="utf-8",
    )
    real_open = os.open
    rebound = False

    def rebind_after_open(path, flags, *args, **kwargs):
        nonlocal rebound
        descriptor = real_open(path, flags, *args, **kwargs)
        if (
            not rebound
            and path == "dotsync-app.rb.in"
            and kwargs.get("dir_fd") is not None
        ):
            rebound = True
            os.replace(attacker_template, template)
        return descriptor

    monkeypatch.setattr(os, "open", rebind_after_open)

    with pytest.raises(ValueError, match="binding changed"):
        _render(repository)

    assert rebound
    assert not (repository / "Casks" / "dotsync-app.rb").exists()


def test_renderer_rolls_back_new_cask_when_directory_fsync_fails(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    real_fsync = os.fsync
    fsync_calls = 0

    def fail_second_fsync(descriptor):
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("injected post-publication fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_second_fsync)

    with pytest.raises(OSError, match="post-publication fsync"):
        _render(repository)

    assert list((repository / "Casks").iterdir()) == []


def test_renderer_rolls_back_new_cask_when_final_validation_fails(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    real_stat = os.stat

    def invalidate_published_cask(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if path == "dotsync-app.rb" and kwargs.get("dir_fd") is not None:
            return os.stat_result(
                (
                    stat.S_IFDIR | 0o755,
                    *result[1:],
                )
            )
        return result

    monkeypatch.setattr(os, "stat", invalidate_published_cask)

    with pytest.raises(ValueError, match="regular file"):
        _render(repository)

    assert list((repository / "Casks").iterdir()) == []


@pytest.mark.parametrize(
    "failure_boundary",
    ["fsync", "validation"],
)
def test_renderer_restores_replaced_cask_on_every_postpublication_failure(
    tmp_path, monkeypatch, failure_boundary
):
    repository = _repository(tmp_path)
    output = repository / "Casks" / "dotsync-app.rb"
    prior_bytes = b"prior-cask-bytes\n"
    output.write_bytes(prior_bytes)
    output.chmod(0o640)

    if failure_boundary == "fsync":
        real_fsync = os.fsync
        fsync_calls = 0

        def fail_selected_fsync(descriptor):
            nonlocal fsync_calls
            fsync_calls += 1
            if fsync_calls == 2:
                raise OSError("injected replacement fsync failure")
            return real_fsync(descriptor)

        monkeypatch.setattr(os, "fsync", fail_selected_fsync)
        expected_error = OSError
    else:
        real_stat = os.stat

        def invalidate_published_cask(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if path == "dotsync-app.rb" and kwargs.get("dir_fd") is not None:
                return os.stat_result(
                    (
                        stat.S_IFDIR | 0o755,
                        *result[1:],
                    )
                )
            return result

        monkeypatch.setattr(os, "stat", invalidate_published_cask)
        expected_error = ValueError

    with pytest.raises(expected_error):
        _render(repository, replace_existing=True)

    assert output.read_bytes() == prior_bytes
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.no_subprocess_block
def test_renderer_rolls_back_if_binding_output_cannot_be_published(tmp_path):
    repository = _repository(tmp_path)
    output = repository / "Casks" / "dotsync-app.rb"
    command = [
        sys.executable,
        str(RENDERER_SCRIPT),
        "--version",
        VALID_VERSION,
        "--sha256",
        VALID_SHA256,
        "--url",
        VALID_URL,
        "--output",
        str(output),
        "--repository-root",
        str(repository),
    ]

    stdout_path = tmp_path / "read-only-stdout"
    stdout_path.write_bytes(b"")
    with stdout_path.open("rb") as read_only_stdout:
        result = subprocess.run(
            command,
            stdout=read_only_stdout,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    assert result.returncode != 0
    assert not output.exists()
    assert list(output.parent.iterdir()) == []


@pytest.mark.parametrize("replace_existing", [False, True])
def test_renderer_retains_exact_rollback_inode_until_binding_callback_completes(
    tmp_path, replace_existing
):
    repository = _repository(tmp_path)
    output = repository / "Casks" / "dotsync-app.rb"
    prior_bytes = b"prior-cask-retained-through-binding\n"
    prior_inode = None
    if replace_existing:
        output.write_bytes(prior_bytes)
        output.chmod(0o640)
        prior_inode = output.stat().st_ino

    def fail_binding_flush(_binding):
        entries = list(output.parent.iterdir())
        retained = [entry for entry in entries if entry.name.startswith(".dotsync-app.")]
        assert output.is_file()
        assert len(retained) == 1
        if replace_existing:
            assert retained[0].read_bytes() == prior_bytes
            assert stat.S_IMODE(retained[0].stat().st_mode) == 0o640
            assert retained[0].stat().st_ino != output.stat().st_ino
        else:
            assert retained[0].stat().st_ino == output.stat().st_ino
            assert output.stat().st_nlink == 2
        raise OSError("injected binding callback flush failure")

    with pytest.raises(OSError, match="binding callback flush"):
        render_cask_module._render_cask_with_binding(
            version=VALID_VERSION,
            sha256=VALID_SHA256,
            url=VALID_URL,
            output=output,
            repository_root=repository,
            replace_existing=replace_existing,
            binding_callback=fail_binding_flush,
        )

    if replace_existing:
        assert output.read_bytes() == prior_bytes
        assert stat.S_IMODE(output.stat().st_mode) == 0o640
        assert output.stat().st_ino == prior_inode
        assert list(output.parent.iterdir()) == [output]
    else:
        assert list(output.parent.iterdir()) == []


@pytest.mark.parametrize("replace_existing", [False, True])
def test_renderer_signal_immediately_before_retained_unlink_rolls_back_exactly(
    tmp_path, monkeypatch, replace_existing
):
    repository = _repository(tmp_path)
    output = repository / "Casks" / "dotsync-app.rb"
    prior_bytes = b"prior-cask-before-renderer-commit\n"
    if replace_existing:
        output.write_bytes(prior_bytes)
        output.chmod(0o640)
    real_pending_check = render_cask_module._raise_if_managed_signal_pending
    injected = False

    def signal_immediately_before_commit():
        nonlocal injected
        if not injected:
            injected = True
            os.kill(os.getpid(), signal.SIGTERM)
        real_pending_check()

    monkeypatch.setattr(
        render_cask_module,
        "_raise_if_managed_signal_pending",
        signal_immediately_before_commit,
    )
    prior_handler = signal.signal(signal.SIGTERM, render_cask_module._raise_signal)
    try:
        with pytest.raises(InterruptedError):
            _render(repository, replace_existing=replace_existing)
    finally:
        signal.signal(signal.SIGTERM, prior_handler)

    if replace_existing:
        assert output.read_bytes() == prior_bytes
        assert stat.S_IMODE(output.stat().st_mode) == 0o640
        assert list(output.parent.iterdir()) == [output]
    else:
        assert list(output.parent.iterdir()) == []


@pytest.mark.parametrize("replace_existing", [False, True])
def test_renderer_signal_immediately_after_retained_unlink_is_postcommit(
    tmp_path, monkeypatch, replace_existing
):
    repository = _repository(tmp_path)
    output = repository / "Casks" / "dotsync-app.rb"
    if replace_existing:
        output.write_bytes(b"prior-cask-replaced-at-commit\n")
    real_unlink = os.unlink
    injected = False

    def signal_immediately_after_commit(path, *args, **kwargs):
        nonlocal injected
        result = real_unlink(path, *args, **kwargs)
        if not injected and str(path).startswith(".dotsync-app."):
            injected = True
            os.kill(os.getpid(), signal.SIGTERM)
        return result

    monkeypatch.setattr(os, "unlink", signal_immediately_after_commit)
    prior_handler = signal.signal(signal.SIGTERM, render_cask_module._raise_signal)
    try:
        rendered = _render(repository, replace_existing=replace_existing)
    finally:
        signal.signal(signal.SIGTERM, prior_handler)

    assert injected
    assert rendered == output
    assert output.read_text(encoding="utf-8").startswith('cask "dotsync-app" do\n')
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize("replace_existing", [False, True])
@pytest.mark.parametrize("postcommit_close_occurrence", [1, 2, 3])
def test_renderer_has_no_failing_descriptor_close_after_explicit_commit(
    tmp_path, monkeypatch, replace_existing, postcommit_close_occurrence
):
    repository = _repository(tmp_path)
    output = repository / "Casks" / "dotsync-app.rb"
    if replace_existing:
        output.write_bytes(b"prior-cask-before-close-seam\n")
    real_unlink = os.unlink
    real_close = os.close
    committed = False
    injected = False
    postcommit_close_count = 0

    def observe_commit(path, *args, **kwargs):
        nonlocal committed
        result = real_unlink(path, *args, **kwargs)
        if str(path).startswith(".dotsync-app."):
            committed = True
        return result

    def fail_first_close_after_commit(descriptor):
        nonlocal injected, postcommit_close_count
        real_close(descriptor)
        if committed:
            postcommit_close_count += 1
        if (
            not injected
            and postcommit_close_count == postcommit_close_occurrence
        ):
            injected = True
            raise OSError("injected close failure after explicit commit")

    monkeypatch.setattr(os, "unlink", observe_commit)
    monkeypatch.setattr(os, "close", fail_first_close_after_commit)

    rendered = _render(repository, replace_existing=replace_existing)

    assert injected
    assert rendered == output
    assert output.read_text(encoding="utf-8").startswith('cask "dotsync-app" do\n')
    assert list(output.parent.iterdir()) == [output]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _git(cwd: Path, real_git: str, *arguments: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        [real_git, *arguments],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _shim_program(*, real_git: str, real_tar: str, real_python: str) -> str:
    return f"""#!{real_python}
import hashlib
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
arguments = sys.argv[1:]
call = [name, *arguments]
log_path = Path(os.environ["DOTSYNC_RELEASE_CALL_LOG"])
prior_calls = []
if log_path.exists():
    prior_calls = [json.loads(line) for line in log_path.read_text().splitlines()]
with log_path.open("a", encoding="utf-8") as log:
    log.write(json.dumps(call) + "\\n")

def rebind_casks_directory(seam):
    repository = Path(os.environ["DOTSYNC_RELEASE_REPOSITORY"])
    casks = repository / "Casks"
    if os.environ.get("DOTSYNC_RELEASE_REBIND_CASKS_OUTSIDE"):
        original = Path(os.environ["DOTSYNC_RELEASE_REBOUND_CASKS_ROOT"]) / (
            "Casks.bound-original-" + seam
        )
    else:
        original = repository / ("Casks.bound-original-" + seam)
    casks.rename(original)
    casks.mkdir()
    (casks / "replacement-marker").write_text(
        "replacement directory must survive\\n",
        encoding="utf-8",
    )

failure = json.loads(os.environ.get("DOTSYNC_RELEASE_FAIL_PREFIX", "[]"))
failure_occurrence = int(os.environ.get("DOTSYNC_RELEASE_FAIL_OCCURRENCE", "1"))
matching_occurrence = 1 + sum(
    prior[:len(failure)] == failure for prior in prior_calls
)
if failure and call[:len(failure)] == failure and matching_occurrence == failure_occurrence:
    raise SystemExit(91)

stdout_failure_prefix = json.loads(
    os.environ.get("DOTSYNC_RELEASE_STDOUT_THEN_FAIL_PREFIX", "[]")
)
if stdout_failure_prefix and call[:len(stdout_failure_prefix)] == stdout_failure_prefix:
    sys.stdout.write(os.environ["DOTSYNC_RELEASE_STDOUT_THEN_FAIL_OUTPUT"])
    sys.stdout.flush()
    raise SystemExit(91)

rebind_prefix = json.loads(os.environ.get("DOTSYNC_RELEASE_REBIND_PREFIX", "[]"))
rebind_occurrence = 1 + sum(
    prior[:len(rebind_prefix)] == rebind_prefix for prior in prior_calls
)
if (
    rebind_prefix
    and call[:len(rebind_prefix)] == rebind_prefix
    and rebind_occurrence == 1
):
    rebind_kind = os.environ["DOTSYNC_RELEASE_REBIND_KIND"]
    if rebind_kind == "work":
        if name == "git" and "--output" in arguments:
            output = Path(arguments[arguments.index("--output") + 1])
            target = output.parent if output.is_absolute() else Path.cwd()
        else:
            target = Path.cwd().parent if Path.cwd().name == "source" else Path.cwd()
    elif rebind_kind == "source":
        target = Path.cwd()
        if target.name != "source":
            target = target / "source"
    else:
        raise SystemExit(93)
    moved = target.with_name(target.name + ".pinned-original")
    target.rename(moved)
    target.mkdir(mode=0o700)
    (target / "attacker-marker").write_text("replacement must survive\\n")
    if rebind_kind == "source":
        executable = target / "build" / "DotSync.app" / "Contents" / "MacOS" / "DotSync"
        executable.parent.mkdir(parents=True)
        executable.write_bytes({MALICIOUS_EXECUTABLE_BYTES!r})
        executable.chmod(0o755)
    record = os.environ.get("DOTSYNC_RELEASE_REBIND_RECORD")
    if record:
        Path(record).write_text(str(target), encoding="utf-8")

if name == "git":
    git_output_overrides = {{
        ("rev-parse", "--show-toplevel"): "DOTSYNC_RELEASE_TOPLEVEL_OUTPUT",
        ("rev-parse", "--path-format=absolute", "--git-dir"):
            "DOTSYNC_RELEASE_GIT_DIR_OUTPUT",
        ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            "DOTSYNC_RELEASE_COMMON_DIR_OUTPUT",
        ("branch", "--show-current"): "DOTSYNC_RELEASE_BRANCH_OUTPUT",
        ("status", "--porcelain=v1", "--untracked-files=all"):
            "DOTSYNC_RELEASE_STATUS_OUTPUT",
    }}
    override_name = git_output_overrides.get(tuple(arguments))
    if override_name is not None and override_name in os.environ:
        sys.stdout.write(os.environ[override_name])
        raise SystemExit(0)
    if arguments == ["rev-parse", "--path-format=absolute", "--git-common-dir"] and os.environ.get("DOTSYNC_RELEASE_LINKED"):
        print(os.environ["DOTSYNC_RELEASE_FAKE_COMMON_DIR"])
        raise SystemExit(0)
    if arguments == ["branch", "--show-current"] and "DOTSYNC_RELEASE_BRANCH" in os.environ:
        print(os.environ["DOTSYNC_RELEASE_BRANCH"])
        raise SystemExit(0)
    if arguments == ["rev-parse", "HEAD"] and "DOTSYNC_RELEASE_HEAD_OUTPUT" in os.environ:
        sys.stdout.write(os.environ["DOTSYNC_RELEASE_HEAD_OUTPUT"])
        raise SystemExit(0)
    if arguments == ["cat-file", "-t", "refs/tags/v0.3.0"] and "DOTSYNC_RELEASE_TAG_TYPE_OUTPUT" in os.environ:
        sys.stdout.write(os.environ["DOTSYNC_RELEASE_TAG_TYPE_OUTPUT"])
        raise SystemExit(0)
    if arguments == ["rev-parse", "--verify", "refs/tags/v0.3.0^{{commit}}"] and "DOTSYNC_RELEASE_TAG_COMMIT_OUTPUT" in os.environ:
        sys.stdout.write(os.environ["DOTSYNC_RELEASE_TAG_COMMIT_OUTPUT"])
        raise SystemExit(0)
    allowed_git = arguments in (
        ["rev-parse", "--show-toplevel"],
        ["rev-parse", "--path-format=absolute", "--git-dir"],
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        ["branch", "--show-current"],
        ["status", "--porcelain=v1", "--untracked-files=all"],
        ["rev-parse", "HEAD"],
        ["cat-file", "-t", "refs/tags/v0.3.0"],
        ["rev-parse", "--verify", "refs/tags/v0.3.0^{{commit}}"],
        ["archive", "--format=tar", "--prefix=source/", "--output", "source.tar", "refs/tags/v0.3.0"],
    )
    if not allowed_git:
        raise SystemExit(92)
    if arguments[:1] == ["archive"] and os.environ.get("DOTSYNC_RELEASE_WORK_RECORD"):
        Path(os.environ["DOTSYNC_RELEASE_WORK_RECORD"]).write_text(
            str(Path.cwd()),
            encoding="utf-8",
        )
    os.execv({real_git!r}, [{real_git!r}, *arguments])

if name == "tar":
    if arguments != ["-xf", "source.tar", "-C", "."]:
        raise SystemExit(92)
    os.execv({real_tar!r}, [{real_tar!r}, *arguments])

if name == "bash":
    if arguments != ["scripts/build_macos_app.sh"]:
        raise SystemExit(92)
    os.execv("/bin/bash", ["/bin/bash", *arguments])

if name == "python-release":
    if len(arguments) >= 1 and arguments[0].endswith("scripts/render_cask.py"):
        if (
            os.environ.get("DOTSYNC_RELEASE_REBIND_CASKS_SEAM") == "bind-to-render"
            and "--rollback-created" not in arguments
        ):
            rebind_casks_directory("bind-to-render")
        signal_prefix = json.loads(os.environ.get("DOTSYNC_RELEASE_SIGNAL_PREFIX", "[]"))
        if (
            signal_prefix
            and call[:len(signal_prefix)] == signal_prefix
            and "--rollback-created" not in arguments
        ):
            import subprocess
            completed = subprocess.run([{real_python!r}, *arguments])
            os.kill(os.getppid(), int(os.environ["DOTSYNC_RELEASE_SIGNAL_NUMBER"]))
            raise SystemExit(completed.returncode)
        os.execv({real_python!r}, [{real_python!r}, *arguments])
    if len(arguments) >= 1 and arguments[0].endswith("scripts/macos_release_support.py"):
        if len(arguments) < 2 or arguments[1] not in (
            "validate-temp-root", "identity-current", "identity-here",
            "identity-parent", "identity-path-entry", "read-cask-binding",
            "identity-directory-fd", "verify-canonical-directory-fd",
            "read-app-plist-versions", "cleanup-current",
        ):
            raise SystemExit(92)
        if (
            arguments[1] == "read-cask-binding"
            and os.environ.get("DOTSYNC_RELEASE_REBIND_CASKS_SEAM") == "render-to-audit"
        ):
            import subprocess
            completed = subprocess.run(
                [{real_python!r}, *arguments],
                capture_output=True,
                text=True,
                pass_fds=(9,),
            )
            if completed.returncode == 0:
                rebind_casks_directory("render-to-audit")
            sys.stdout.write(completed.stdout)
            sys.stderr.write(completed.stderr)
            raise SystemExit(completed.returncode)
        if (
            arguments[1] == "cleanup-current"
            and os.environ.get("DOTSYNC_RELEASE_SECOND_SIGNAL_DURING_CLEANUP")
        ):
            os.kill(
                os.getppid(),
                int(os.environ["DOTSYNC_RELEASE_SECOND_SIGNAL_DURING_CLEANUP"]),
            )
        signal_prefix = json.loads(
            os.environ.get("DOTSYNC_RELEASE_SIGNAL_PREFIX", "[]")
        )
        signal_occurrence = int(
            os.environ.get("DOTSYNC_RELEASE_SIGNAL_OCCURRENCE", "1")
        )
        matching_signal_occurrence = 1 + sum(
            prior[:len(signal_prefix)] == signal_prefix for prior in prior_calls
        )
        if (
            signal_prefix
            and call[:len(signal_prefix)] == signal_prefix
            and matching_signal_occurrence == signal_occurrence
        ):
            import subprocess
            completed = subprocess.run(
                [{real_python!r}, *arguments],
                capture_output=True,
                text=True,
                pass_fds=(9,),
            )
            os.kill(
                os.getppid(),
                int(os.environ["DOTSYNC_RELEASE_SIGNAL_NUMBER"]),
            )
            sys.stdout.write(completed.stdout)
            sys.stderr.write(completed.stderr)
            raise SystemExit(completed.returncode)
        os.execv({real_python!r}, [{real_python!r}, *arguments])
    if arguments[:1] == ["-c"]:
        allowed_code_prefixes = (
            "import pathlib, tomllib;", "import pathlib, plistlib;",
            "import re, sys; identity=", "import json, sys; data=json.load(sys.stdin);",
        )
        if len(arguments) not in (2, 3) or not arguments[1].startswith(allowed_code_prefixes):
            raise SystemExit(92)
        os.execv({real_python!r}, [{real_python!r}, *arguments])
    if arguments in (["-m", "pytest"], ["-m", "dotsync", "ui", "--check"]):
        raise SystemExit(0)
    raise SystemExit(92)

if name == "security":
    if arguments != ["find-identity", "-v", "-p", "codesigning"]:
        raise SystemExit(92)
    identity = os.environ["DEVELOPER_ID_APPLICATION"]
    if "DOTSYNC_RELEASE_SECURITY_OUTPUT" in os.environ:
        print(os.environ["DOTSYNC_RELEASE_SECURITY_OUTPUT"])
    elif not os.environ.get("DOTSYNC_RELEASE_IDENTITY_UNRESOLVABLE"):
        print(f'  1) AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "{{identity}}"')
    raise SystemExit(0)

if name == "lipo":
    if arguments == ["build/DotSync.app/Contents/MacOS/DotSync", "-verify_arch", "arm64", "x86_64"]:
        raise SystemExit(0)
    if arguments == ["build/DotSync.app/Contents/MacOS/DotSync", "-archs"]:
        print(os.environ.get("DOTSYNC_RELEASE_ARCHS", "x86_64 arm64"))
        raise SystemExit(0)
    raise SystemExit(92)

if name == "xcrun":
    if arguments[:2] == ["notarytool", "history"]:
        if arguments[2:] != ["--keychain-profile", os.environ["NOTARYTOOL_PROFILE"], "--output-format", "json"]:
            raise SystemExit(92)
        print(
            os.environ.get(
                "DOTSYNC_RELEASE_NOTARY_HISTORY_OUTPUT",
                '{{"history":[{{"id":"11111111-2222-3333-4444-555555555555","status":"Accepted"}}]}}',
            )
        )
        raise SystemExit(0)
    if arguments[:2] == ["notarytool", "submit"]:
        if arguments[3:] != ["--keychain-profile", os.environ["NOTARYTOOL_PROFILE"], "--wait", "--output-format", "json"]:
            raise SystemExit(92)
        print(
            os.environ.get(
                "DOTSYNC_RELEASE_NOTARY_SUBMIT_OUTPUT",
                '{{"id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","status":"Accepted","message":"Package Approved"}}',
            )
        )
        raise SystemExit(0)
    if arguments[:2] in (["stapler", "staple"], ["stapler", "validate"]) and len(arguments) == 3:
        raise SystemExit(0)
    raise SystemExit(92)

if name == "ditto":
    if len(arguments) != 5 or arguments[:3] != ["-c", "-k", "--keepParent"]:
        raise SystemExit(92)
    destination = Path(arguments[-1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.name.startswith("DotSync-notarization-"):
        destination.write_bytes(b"unsigned-notary-archive\\n")
    else:
        app = Path(arguments[-2])
        executable = app / "Contents" / "MacOS" / "DotSync"
        destination.write_bytes(b"archive:" + executable.read_bytes())
    raise SystemExit(0)

if name == "shasum":
    if len(arguments) != 3 or arguments[:2] != ["-a", "256"]:
        raise SystemExit(92)
    archive = Path(arguments[-1])
    output_override = os.environ.get("DOTSYNC_RELEASE_SHA_OUTPUT")
    if output_override is not None:
        print(output_override.replace("{{archive}}", str(archive)))
    else:
        print(f"{{hashlib.sha256(archive.read_bytes()).hexdigest()}}  {{archive}}")
    raise SystemExit(0)

if name == "gh":
    if arguments[:2] == ["release", "view"]:
        if arguments != ["release", "view", "v0.3.0", "--repo", {REPOSITORY_SLUG!r}, "--json", "id,assets"]:
            raise SystemExit(92)
        print(
            os.environ.get(
                "DOTSYNC_RELEASE_GH_VIEW_OUTPUT",
                '{{"id":"RE_kwDORel3as4AAAAA","assets":[]}}',
            )
        )
        raise SystemExit(0)
    if arguments[:2] == ["release", "upload"]:
        if arguments != ["release", "upload", "v0.3.0", "../DotSync-0.3.0-macOS.zip", "--repo", {REPOSITORY_SLUG!r}]:
            raise SystemExit(92)
        uploaded_copy = os.environ.get("DOTSYNC_RELEASE_UPLOADED_COPY")
        if uploaded_copy:
            Path(uploaded_copy).write_bytes(Path(arguments[3]).read_bytes())
        raise SystemExit(0)
    raise SystemExit(92)

if name == "codesign":
    if arguments[:1] == ["--force"]:
        if arguments != ["--force", "--options", "runtime", "--timestamp", "--sign", os.environ["DEVELOPER_ID_APPLICATION"], "build/DotSync.app"]:
            raise SystemExit(92)
        signed_copy = os.environ.get("DOTSYNC_RELEASE_SIGNED_COPY")
        if signed_copy:
            app = Path(arguments[-1])
            executable = app / "Contents" / "MacOS" / "DotSync"
            Path(signed_copy).write_bytes(executable.read_bytes())
        raise SystemExit(0)
    if arguments[:1] == ["--verify"]:
        if arguments != ["--verify", "--deep", "--strict", "--verbose=2", "build/DotSync.app"]:
            raise SystemExit(92)
        raise SystemExit(0)
    raise SystemExit(92)

if name == "brew" and os.environ.get("DOTSYNC_RELEASE_REPLACE_CASK_DURING_AUDIT"):
    cask = Path(arguments[-1])
    owned = cask.with_name("dotsync-app.created-by-renderer")
    cask.rename(owned)
    cask.write_bytes(b"replacement-cask-must-survive\\n")
    raise SystemExit(91)

if name == "brew" and os.environ.get("DOTSYNC_RELEASE_REPLACE_CASKS_DURING_AUDIT"):
    cask = Path(arguments[-1])
    casks = cask.parent
    owned = casks.with_name("Casks.created-by-renderer")
    casks.rename(owned)
    casks.mkdir()
    (casks / cask.name).write_bytes(b"replacement-directory-cask-must-survive\\n")
    raise SystemExit(91)

signal_prefix = json.loads(os.environ.get("DOTSYNC_RELEASE_SIGNAL_PREFIX", "[]"))
if signal_prefix and call[:len(signal_prefix)] == signal_prefix:
    os.kill(os.getppid(), int(os.environ["DOTSYNC_RELEASE_SIGNAL_NUMBER"]))

residue_prefix = json.loads(os.environ.get("DOTSYNC_RELEASE_RESIDUE_PREFIX", "[]"))
if residue_prefix and call[:len(residue_prefix)] == residue_prefix:
    archive_calls = [prior for prior in prior_calls if prior[:2] == ["git", "archive"]]
    if not archive_calls:
        raise SystemExit(93)
    archive_call = archive_calls[-1]
    output = Path(archive_call[archive_call.index("--output") + 1])
    work = output.parent if output.is_absolute() else Path.cwd()
    if work.name == "source":
        work = work.parent
    (work / "unowned-cleanup-residue").write_text("preserve me\\n")
    residue_record = os.environ.get("DOTSYNC_RELEASE_RESIDUE_RECORD")
    if residue_record:
        Path(residue_record).write_text(str(work), encoding="utf-8")

if name == "node":
    if arguments == ["--test", "tests/web/js/state.test.mjs", "tests/web/js/api-client.test.mjs"]:
        raise SystemExit(0)
    raise SystemExit(92)

if name == "swift":
    if arguments == ["test", "--package-path", "macos/DotSyncApp"]:
        raise SystemExit(0)
    raise SystemExit(92)

if name == "spctl":
    if arguments == ["--assess", "--type", "execute", "--verbose=4", "build/DotSync.app"]:
        raise SystemExit(0)
    raise SystemExit(92)

if name == "brew":
    if len(arguments) == 4 and arguments[:3] == ["audit", "--cask", "--strict"]:
        if os.environ.get("DOTSYNC_RELEASE_REBIND_CASKS_SEAM") == "audit-to-cleanup":
            rebind_casks_directory("audit-to-cleanup")
        raise SystemExit(0)
    raise SystemExit(92)

raise SystemExit(92)
"""


@pytest.fixture
def macos_release_repository(tmp_path):
    real_git = shutil.which("git")
    real_tar = shutil.which("tar")
    assert real_git is not None
    assert real_tar is not None

    repository = tmp_path / "release-repository"
    (repository / "scripts").mkdir(parents=True)
    (repository / "packaging").mkdir()
    (repository / "macos" / "DotSyncApp").mkdir(parents=True)
    (repository / "tests" / "web" / "js").mkdir(parents=True)
    shutil.copy2(RELEASE_SCRIPT, repository / "scripts" / RELEASE_SCRIPT.name)
    shutil.copy2(RENDERER_SCRIPT, repository / "scripts" / RENDERER_SCRIPT.name)
    shutil.copy2(
        RELEASE_SUPPORT_SCRIPT,
        repository / "scripts" / RELEASE_SUPPORT_SCRIPT.name,
    )
    shutil.copy2(CASK_TEMPLATE, repository / "packaging" / CASK_TEMPLATE.name)
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "dotsync"\nversion = "0.3.0"\n',
        encoding="utf-8",
    )
    (repository / "macos" / "DotSyncApp" / "Package.swift").write_text(
        "// release fixture\n",
        encoding="utf-8",
    )
    for test_name in ("state.test.mjs", "api-client.test.mjs"):
        (repository / "tests" / "web" / "js" / test_name).write_text(
            "// release fixture\n",
            encoding="utf-8",
        )
    (repository / ".gitignore").write_text("build/\nCasks/\n", encoding="utf-8")
    _write_executable(
        repository / "scripts" / "build_macos_app.sh",
        """#!/usr/bin/env bash
set -euo pipefail
mkdir -p build/DotSync.app/Contents/MacOS
printf '%s\\n' 'fixture-universal-binary' > build/DotSync.app/Contents/MacOS/DotSync
chmod 755 build/DotSync.app/Contents/MacOS/DotSync
plist_version="${DOTSYNC_RELEASE_BUILT_PLIST_VERSION:-0.3.0}"
plist_shape="${DOTSYNC_RELEASE_BUILT_PLIST_SHAPE:-regular}"
printf '%s\\n' \\
  '<?xml version="1.0" encoding="UTF-8"?>' \\
  '<plist version="1.0"><dict>' \\
  '<key>CFBundleShortVersionString</key>' \\
  "<string>$plist_version</string>" \\
  '<key>CFBundleVersion</key>' \\
  "<string>$plist_version</string>" \\
  '</dict></plist>' \\
  > build/DotSync.app/Contents/Info.plist.payload
case "$plist_shape" in
  regular)
    mv build/DotSync.app/Contents/Info.plist.payload build/DotSync.app/Contents/Info.plist
    ;;
  absent)
    rm build/DotSync.app/Contents/Info.plist.payload
    ;;
  symlink)
    ln -s Info.plist.payload build/DotSync.app/Contents/Info.plist
    ;;
  directory)
    mkdir build/DotSync.app/Contents/Info.plist
    ;;
  unreadable)
    mv build/DotSync.app/Contents/Info.plist.payload build/DotSync.app/Contents/Info.plist
    chmod 000 build/DotSync.app/Contents/Info.plist
    ;;
  *)
    exit 97
    ;;
esac
""",
    )

    git_env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release@example.invalid",
        "GIT_COMMITTER_NAME": "Release Test",
        "GIT_COMMITTER_EMAIL": "release@example.invalid",
    }
    _git(tmp_path, real_git, "init", "-b", "main", str(repository), env=git_env)
    _git(repository, real_git, "add", "-A", env=git_env)
    _git(repository, real_git, "commit", "-m", "release fixture", env=git_env)
    _git(repository, real_git, "tag", "-a", "v0.3.0", "-m", "v0.3.0", env=git_env)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    dispatcher = fake_bin / "dispatcher"
    _write_executable(
        dispatcher,
        _shim_program(
            real_git=real_git,
            real_tar=real_tar,
            real_python=sys.executable,
        ),
    )
    shim_names = (
        "git",
        "tar",
        "python-release",
        "node",
        "swift",
        "bash",
        "security",
        "lipo",
        "codesign",
        "ditto",
        "xcrun",
        "spctl",
        "shasum",
        "gh",
        "brew",
    )
    for name in shim_names:
        (fake_bin / name).symlink_to(dispatcher)

    call_log = tmp_path / "release-calls.jsonl"
    private_temp = tmp_path / "private-temp"
    private_temp.mkdir(mode=0o700)
    rebound_casks_root = tmp_path / "renamed-casks"
    rebound_casks_root.mkdir()
    env = {
        **git_env,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "PYTHON": str(fake_bin / "python-release"),
        "DEVELOPER_ID_APPLICATION": TEST_IDENTITY,
        "NOTARYTOOL_PROFILE": TEST_NOTARY_PROFILE,
        "DOTSYNC_RELEASE_CALL_LOG": str(call_log),
        "DOTSYNC_RELEASE_FAKE_COMMON_DIR": str(tmp_path / "other-common.git"),
        "DOTSYNC_RELEASE_REPOSITORY": str(repository),
        "DOTSYNC_RELEASE_REBOUND_CASKS_ROOT": str(rebound_casks_root),
        "TMPDIR": str(private_temp),
    }
    return {
        "repository": repository,
        "fake_bin": fake_bin,
        "call_log": call_log,
        "env": env,
    }


def _run_macos_release(
    fixture: dict[str, object],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    repository = fixture["repository"]
    env = fixture["env"]
    assert isinstance(repository, Path)
    assert isinstance(env, dict)
    return subprocess.run(
        ["/bin/bash", "scripts/release_macos_app.sh", *arguments],
        cwd=repository,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.no_subprocess_block
def test_fake_release_dispatcher_rejects_unknown_external_argv(
    macos_release_repository,
):
    fake_bin = macos_release_repository["fake_bin"]
    env = macos_release_repository["env"]
    assert isinstance(fake_bin, Path)
    assert isinstance(env, dict)

    result = subprocess.run(
        [str(fake_bin / "codesign"), "--unknown-release-argument"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 92


def _release_calls(fixture: dict[str, object]) -> list[list[str]]:
    call_log = fixture["call_log"]
    assert isinstance(call_log, Path)
    if not call_log.exists():
        return []
    return [json.loads(line) for line in call_log.read_text().splitlines()]


def _normalized_release_calls(fixture: dict[str, object]) -> list[list[str]]:
    repository = fixture["repository"]
    fake_bin = fixture["fake_bin"]
    env = fixture["env"]
    assert isinstance(repository, Path)
    assert isinstance(fake_bin, Path)
    assert isinstance(env, dict)
    calls = _release_calls(fixture)

    replacements = [
        (str(fake_bin / "python-release"), "$PYTHON"),
        (str(repository), "$REPOSITORY"),
        (str(env["TMPDIR"]), "$TMPDIR"),
    ]
    normalized: list[list[str]] = []
    for call in calls:
        normalized_call: list[str] = []
        for value in call:
            for original, replacement in replacements:
                value = value.replace(original, replacement)
            value = re.sub(
                r"^([^:]+):[0-9]+:[0-9]+:([df])$",
                r"\1:$DEV:$INO:\2",
                value,
            )
            if re.fullmatch(r"[0-9]+:[0-9]+", value):
                value = "$DEV:$INO"
            if re.fullmatch(r"dotsync-macos-release\.[A-Za-z0-9]+", value):
                value = "$WORK_NAME"
            if (
                value.isdigit()
                and normalized_call
                and normalized_call[-1]
                in {
                    "--expected-casks-dev",
                    "--expected-casks-ino",
                    "--casks-dev",
                    "--casks-ino",
                    "--cask-dev",
                    "--cask-ino",
                }
            ):
                value = "$NUMBER"
            normalized_call.append(value)
        normalized.append(normalized_call)
    return normalized


def _expected_success_calls() -> list[list[str]]:
    app = "build/DotSync.app"
    executable = f"{app}/Contents/MacOS/DotSync"
    notary_zip = "../DotSync-notarization-0.3.0.zip"
    final_zip = "../DotSync-0.3.0-macOS.zip"
    cask = "$REPOSITORY/Casks/dotsync-app.rb"
    support = "$REPOSITORY/scripts/macos_release_support.py"
    tagged_version_code = (
        'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml")'
        '.read_text(encoding="utf-8"))["project"]["version"])'
    )
    identity_code = (
        'import re, sys; identity=sys.argv[1]; pattern=re.compile(r"\\s*\\d+\\)'
        '\\s+[0-9A-Fa-f]{40}\\s+\\\"" + re.escape(identity) + r"\\\""); '
        'lines=sys.stdin.read().splitlines(); raise SystemExit(0 if sum(pattern.'
        'fullmatch(line) is not None for line in lines) == 1 else 1)'
    )
    history_code = (
        'import json, sys; data=json.load(sys.stdin); raise SystemExit(0 if '
        'isinstance(data, dict) and isinstance(data.get("history"), list) else 1)'
    )
    submit_code = (
        'import json, sys; data=json.load(sys.stdin); submission_id=data.get("id") '
        'if isinstance(data, dict) else None; status=data.get("status") if '
        'isinstance(data, dict) else None; sys.exit(1) if status != "Accepted" or '
        'not isinstance(submission_id, str) or not submission_id.strip() else '
        'print(submission_id)'
    )
    release_code = (
        'import json, sys; data=json.load(sys.stdin); release_id=data.get("id") if '
        'isinstance(data, dict) else None; assets=data.get("assets") if isinstance('
        'data, dict) else None; valid_assets=isinstance(assets, list) and all('
        'isinstance(asset, dict) and isinstance(asset.get("name"), str) and asset'
        '["name"] for asset in assets); collision=valid_assets and any(asset["name"] '
        '== sys.argv[1] for asset in assets); sys.exit(1) if not isinstance('
        'release_id, str) or not release_id.strip() or not valid_assets or collision '
        'else print(release_id)'
    )
    return [
        ["git", "rev-parse", "--show-toplevel"],
        ["git", "rev-parse", "--path-format=absolute", "--git-dir"],
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        ["git", "branch", "--show-current"],
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        ["git", "rev-parse", "HEAD"],
        ["git", "cat-file", "-t", "refs/tags/v0.3.0"],
        ["git", "rev-parse", "--verify", "refs/tags/v0.3.0^{commit}"],
        ["python-release", support, "validate-temp-root", "$TMPDIR"],
        ["python-release", support, "identity-current"],
        ["python-release", support, "identity-current", "--require-mode", "0700"],
        [
            "git",
            "archive",
            "--format=tar",
            "--prefix=source/",
            "--output",
            "source.tar",
            "refs/tags/v0.3.0",
        ],
        ["python-release", support, "identity-here", "source.tar"],
        ["tar", "-xf", "source.tar", "-C", "."],
        ["python-release", support, "identity-here", "source"],
        ["python-release", support, "identity-current"],
        ["python-release", "-c", tagged_version_code],
        ["python-release", "-m", "pytest"],
        [
            "node",
            "--test",
            "tests/web/js/state.test.mjs",
            "tests/web/js/api-client.test.mjs",
        ],
        ["swift", "test", "--package-path", "macos/DotSyncApp"],
        ["python-release", "-m", "dotsync", "ui", "--check"],
        ["bash", "scripts/build_macos_app.sh"],
        ["python-release", support, "read-app-plist-versions"],
        [
            "lipo",
            executable,
            "-verify_arch",
            "arm64",
            "x86_64",
        ],
        ["lipo", executable, "-archs"],
        ["security", "find-identity", "-v", "-p", "codesigning"],
        ["python-release", "-c", identity_code, TEST_IDENTITY],
        [
            "xcrun",
            "notarytool",
            "history",
            "--keychain-profile",
            TEST_NOTARY_PROFILE,
            "--output-format",
            "json",
        ],
        ["python-release", "-c", history_code],
        [
            "codesign",
            "--force",
            "--options",
            "runtime",
            "--timestamp",
            "--sign",
            TEST_IDENTITY,
            app,
        ],
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", app],
        ["ditto", "-c", "-k", "--keepParent", app, notary_zip],
        ["python-release", support, "identity-parent", "DotSync-notarization-0.3.0.zip"],
        [
            "xcrun",
            "notarytool",
            "submit",
            notary_zip,
            "--keychain-profile",
            TEST_NOTARY_PROFILE,
            "--wait",
            "--output-format",
            "json",
        ],
        ["python-release", "-c", submit_code],
        ["xcrun", "stapler", "staple", app],
        ["xcrun", "stapler", "validate", app],
        ["spctl", "--assess", "--type", "execute", "--verbose=4", app],
        ["ditto", "-c", "-k", "--keepParent", app, final_zip],
        ["python-release", support, "identity-parent", "DotSync-0.3.0-macOS.zip"],
        ["shasum", "-a", "256", final_zip],
        [
            "gh", "release", "view", "v0.3.0", "--repo", REPOSITORY_SLUG,
            "--json", "id,assets",
        ],
        ["python-release", "-c", release_code, "DotSync-0.3.0-macOS.zip"],
        [
            "gh",
            "release",
            "upload",
            "v0.3.0",
            final_zip,
            "--repo",
            REPOSITORY_SLUG,
        ],
        ["python-release", support, "identity-current"],
        ["python-release", support, "identity-path-entry", "$REPOSITORY", "Casks"],
        ["python-release", support, "identity-directory-fd", "9", "$DEV:$INO"],
        ["python-release", support, "identity-here", "cask-binding.json"],
        [
            "python-release",
            "$REPOSITORY/scripts/render_cask.py",
            "--version",
            "0.3.0",
            "--sha256",
            FINAL_ARCHIVE_SHA256,
            "--url",
            VALID_URL,
            "--output",
            cask,
            "--repository-root",
            "$REPOSITORY",
            "--expected-casks-dev",
            "$NUMBER",
            "--expected-casks-ino",
            "$NUMBER",
            "--casks-fd",
            "9",
        ],
        [
            "python-release", support, "read-cask-binding", "cask-binding.json",
            "cask-binding.json:$DEV:$INO:f",
        ],
        [
            "python-release", support, "verify-canonical-directory-fd",
            "9", "$DEV:$INO", "$REPOSITORY", "Casks",
        ],
        ["brew", "audit", "--cask", "--strict", cask],
        [
            "python-release", support, "cleanup-current",
            "--parent", "$TMPDIR",
            "--name", "$WORK_NAME",
            "--parent-identity", "$DEV:$INO",
            "--work-identity", "$DEV:$INO",
            "--owned", "source.tar:$DEV:$INO:f",
            "--owned", "source:$DEV:$INO:d",
            "--owned", "DotSync-notarization-0.3.0.zip:$DEV:$INO:f",
            "--owned", "DotSync-0.3.0-macOS.zip:$DEV:$INO:f",
            "--owned", "cask-binding.json:$DEV:$INO:f",
        ],
        [
            "python-release", support, "verify-canonical-directory-fd",
            "9", "$DEV:$INO", "$REPOSITORY", "Casks",
        ],
    ]


@pytest.mark.no_subprocess_block
def test_signed_macos_release_runs_every_gate_once_in_exact_order_then_stops(
    macos_release_repository,
):
    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    repository = macos_release_repository["repository"]
    assert isinstance(repository, Path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _normalized_release_calls(macos_release_repository) == _expected_success_calls()
    cask = repository / "Casks" / "dotsync-app.rb"
    assert cask.is_file()
    assert f'  sha256 "{FINAL_ARCHIVE_SHA256}"' in cask.read_text(encoding="utf-8")
    assert "explicit publication confirmation" in result.stdout
    assert not any(call[:2] == ["git", "push"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "arguments",
    [(), ("0.3.0", "unexpected"), ("0.0.0",), ("00.3.0",), ("v0.3.0",)],
)
def test_signed_macos_release_requires_one_canonical_nonzero_version(
    macos_release_repository, arguments
):
    result = _run_macos_release(macos_release_repository, *arguments)

    assert result.returncode != 0
    assert _release_calls(macos_release_repository) == []


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "environment_change",
    [
        {"DOTSYNC_RELEASE_LINKED": "1"},
        {"DOTSYNC_RELEASE_BRANCH": "feature"},
    ],
)
def test_signed_macos_release_rejects_non_primary_or_non_main_checkout_before_export(
    macos_release_repository, environment_change
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env.update(environment_change)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(call[:2] == ["git", "archive"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
def test_signed_macos_release_rejects_dirty_checkout_before_export(
    macos_release_repository,
):
    repository = macos_release_repository["repository"]
    assert isinstance(repository, Path)
    (repository / "untracked-release-state").write_text("dirty", encoding="utf-8")

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(call[:2] == ["git", "archive"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
def test_signed_macos_release_requires_tag_to_resolve_to_exact_head(
    macos_release_repository,
):
    repository = macos_release_repository["repository"]
    env = macos_release_repository["env"]
    assert isinstance(repository, Path)
    assert isinstance(env, dict)
    real_git = shutil.which("git", path=os.environ["PATH"])
    assert real_git is not None
    (repository / "later").write_text("different HEAD", encoding="utf-8")
    _git(repository, real_git, "add", "later", env=env)
    _git(repository, real_git, "commit", "-m", "different head", env=env)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(call[:2] == ["git", "archive"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
def test_signed_macos_release_rejects_lightweight_tag_before_export(
    macos_release_repository,
):
    repository = macos_release_repository["repository"]
    env = macos_release_repository["env"]
    assert isinstance(repository, Path)
    assert isinstance(env, dict)
    real_git = shutil.which("git", path=os.environ["PATH"])
    assert real_git is not None
    _git(repository, real_git, "tag", "-d", "v0.3.0", env=env)
    _git(repository, real_git, "tag", "v0.3.0", env=env)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(call[:2] == ["git", "archive"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
def test_signed_macos_release_rejects_tagged_project_version_mismatch_before_build(
    macos_release_repository,
):
    repository = macos_release_repository["repository"]
    env = macos_release_repository["env"]
    assert isinstance(repository, Path)
    assert isinstance(env, dict)
    real_git = shutil.which("git", path=os.environ["PATH"])
    assert real_git is not None
    _git(repository, real_git, "tag", "-d", "v0.3.0", env=env)
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "dotsync"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    _git(repository, real_git, "add", "pyproject.toml", env=env)
    _git(repository, real_git, "commit", "-m", "mismatched tagged version", env=env)
    _git(repository, real_git, "tag", "-a", "v0.3.0", "-m", "v0.3.0", env=env)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    calls = _release_calls(macos_release_repository)
    assert any(call[:2] == ["git", "archive"] for call in calls)
    assert not any(call[:2] == ["python-release", "-m"] for call in calls)
    assert not any(call[:2] == ["bash", "scripts/build_macos_app.sh"] for call in calls)


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "variable,value",
    [
        ("DOTSYNC_RELEASE_HEAD_OUTPUT", ""),
        ("DOTSYNC_RELEASE_HEAD_OUTPUT", "0" * 40 + "\n"),
        ("DOTSYNC_RELEASE_HEAD_OUTPUT", "A" * 40 + "\n"),
        ("DOTSYNC_RELEASE_TAG_TYPE_OUTPUT", ""),
        ("DOTSYNC_RELEASE_TAG_TYPE_OUTPUT", "commit\n"),
        ("DOTSYNC_RELEASE_TAG_COMMIT_OUTPUT", ""),
        ("DOTSYNC_RELEASE_TAG_COMMIT_OUTPUT", "0" * 40 + "\n"),
        ("DOTSYNC_RELEASE_TAG_COMMIT_OUTPUT", "A" * 40 + "\n"),
        ("DOTSYNC_RELEASE_GIT_DIR_OUTPUT", ""),
        ("DOTSYNC_RELEASE_GIT_DIR_OUTPUT", "relative/.git\n"),
        ("DOTSYNC_RELEASE_COMMON_DIR_OUTPUT", ""),
        ("DOTSYNC_RELEASE_COMMON_DIR_OUTPUT", "relative/.git\n"),
    ],
)
def test_signed_macos_release_rejects_empty_or_malformed_tag_provenance_output(
    macos_release_repository, variable, value
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env[variable] = value

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(call[:2] == ["git", "archive"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
def test_signed_macos_release_rejects_matching_all_zero_head_and_tag_oids(
    macos_release_repository,
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    zero_oid = "0" * 40 + "\n"
    env["DOTSYNC_RELEASE_HEAD_OUTPUT"] = zero_oid
    env["DOTSYNC_RELEASE_TAG_COMMIT_OUTPUT"] = zero_oid

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(
        call[:2] == ["git", "archive"]
        for call in _release_calls(macos_release_repository)
    )


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "command_key",
    [
        "toplevel",
        "git-dir",
        "common-dir",
        "branch",
        "status",
        "head",
        "tag-type",
        "tag-commit",
    ],
)
def test_each_captured_git_preflight_rejects_plausible_stdout_with_nonzero_exit(
    macos_release_repository, command_key
):
    repository = macos_release_repository["repository"]
    env = macos_release_repository["env"]
    assert isinstance(repository, Path)
    assert isinstance(env, dict)
    real_git = shutil.which("git", path=os.environ["PATH"])
    assert real_git is not None
    head = _git(repository, real_git, "rev-parse", "HEAD", env=env)
    cases = {
        "toplevel": (["git", "rev-parse", "--show-toplevel"], f"{repository}\n"),
        "git-dir": (
            ["git", "rev-parse", "--path-format=absolute", "--git-dir"],
            f"{repository / '.git'}\n",
        ),
        "common-dir": (
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            f"{repository / '.git'}\n",
        ),
        "branch": (["git", "branch", "--show-current"], "main\n"),
        "status": (
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            "",
        ),
        "head": (["git", "rev-parse", "HEAD"], f"{head}\n"),
        "tag-type": (
            ["git", "cat-file", "-t", "refs/tags/v0.3.0"],
            "tag\n",
        ),
        "tag-commit": (
            ["git", "rev-parse", "--verify", "refs/tags/v0.3.0^{commit}"],
            f"{head}\n",
        ),
    }
    prefix, plausible_output = cases[command_key]
    env["DOTSYNC_RELEASE_STDOUT_THEN_FAIL_PREFIX"] = json.dumps(prefix)
    env["DOTSYNC_RELEASE_STDOUT_THEN_FAIL_OUTPUT"] = plausible_output

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(
        call[:2] == ["git", "archive"]
        for call in _release_calls(macos_release_repository)
    )


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize("trailing_slashes", ["/", "///"])
def test_signed_macos_release_rejects_symlinked_temporary_root_without_touching_target(
    macos_release_repository, tmp_path, trailing_slashes
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    external = tmp_path / "external-temporary-root"
    external.mkdir()
    temporary_link = tmp_path / "temporary-link"
    temporary_link.symlink_to(external, target_is_directory=True)
    env["TMPDIR"] = f"{temporary_link}{trailing_slashes}"

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert list(external.iterdir()) == []
    assert not any(call[:2] == ["git", "archive"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize("mode", [0o770, 0o707, 0o777])
def test_signed_macos_release_rejects_group_or_other_writable_real_tmpdir(
    macos_release_repository, tmp_path, mode
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    unsafe_root = tmp_path / "unsafe-real-temp"
    unsafe_root.mkdir(mode=mode)
    unsafe_root.chmod(mode)
    env["TMPDIR"] = str(unsafe_root)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert list(unsafe_root.iterdir()) == []
    assert not any(call[:2] == ["git", "archive"] for call in _release_calls(macos_release_repository))


def test_release_support_rejects_tmpdir_not_owned_by_effective_user(
    tmp_path, monkeypatch
):
    private_root = tmp_path / "private-root"
    private_root.mkdir(mode=0o700)
    actual_uid = private_root.stat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: actual_uid + 1)

    with pytest.raises(ValueError, match="owned by the effective user"):
        macos_release_support.validate_temp_root(private_root)


def test_release_support_rejects_lexical_alias_of_filesystem_root(monkeypatch):
    root_alias = Path("/private/..")
    root_uid = Path("/").stat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: root_uid)

    with pytest.raises(ValueError, match="non-root"):
        macos_release_support.validate_temp_root(root_alias)


@pytest.mark.no_subprocess_block
def test_signed_macos_release_preserves_replacement_of_workdir_name_and_fails(
    macos_release_repository, tmp_path
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    record = tmp_path / "work-rebind-record"
    env["DOTSYNC_RELEASE_REBIND_PREFIX"] = json.dumps(["git", "archive"])
    env["DOTSYNC_RELEASE_REBIND_KIND"] = "work"
    env["DOTSYNC_RELEASE_REBIND_RECORD"] = str(record)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    rebound_name = Path(record.read_text(encoding="utf-8"))
    assert result.returncode != 0
    assert (rebound_name / "attacker-marker").read_text() == "replacement must survive\n"


@pytest.mark.no_subprocess_block
def test_signed_and_uploaded_bytes_stay_in_pinned_source_after_name_replacement(
    macos_release_repository, tmp_path
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    signed_copy = tmp_path / "signed-executable"
    uploaded_copy = tmp_path / "uploaded-archive"
    env["DOTSYNC_RELEASE_REBIND_PREFIX"] = json.dumps(["lipo"])
    env["DOTSYNC_RELEASE_REBIND_KIND"] = "source"
    env["DOTSYNC_RELEASE_REBIND_RECORD"] = str(tmp_path / "source-rebind-record")
    env["DOTSYNC_RELEASE_SIGNED_COPY"] = str(signed_copy)
    env["DOTSYNC_RELEASE_UPLOADED_COPY"] = str(uploaded_copy)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert signed_copy.read_bytes() == FIXTURE_EXECUTABLE_BYTES
    assert uploaded_copy.read_bytes() == FINAL_ARCHIVE_BYTES


@pytest.mark.no_subprocess_block
def test_signed_macos_release_reports_cleanup_failure_and_preserves_unknown_entry(
    macos_release_repository, tmp_path
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    record = tmp_path / "cleanup-residue-record"
    env["DOTSYNC_RELEASE_RESIDUE_PREFIX"] = json.dumps(["brew", "audit"])
    env["DOTSYNC_RELEASE_RESIDUE_RECORD"] = str(record)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    work = Path(record.read_text(encoding="utf-8"))
    repository = macos_release_repository["repository"]
    assert isinstance(repository, Path)
    assert result.returncode != 0
    assert (work / "unowned-cleanup-residue").read_text() == "preserve me\n"
    assert not (repository / "Casks" / "dotsync-app.rb").exists()
    assert "explicit publication confirmation" not in result.stdout


@pytest.mark.no_subprocess_block
def test_audit_failure_never_unlinks_a_replacement_of_the_created_cask(
    macos_release_repository,
):
    env = macos_release_repository["env"]
    repository = macos_release_repository["repository"]
    assert isinstance(env, dict)
    assert isinstance(repository, Path)
    env["DOTSYNC_RELEASE_REPLACE_CASK_DURING_AUDIT"] = "1"

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    cask = repository / "Casks" / "dotsync-app.rb"
    assert result.returncode != 0
    assert cask.read_bytes() == b"replacement-cask-must-survive\n"
    assert (repository / "Casks" / "dotsync-app.created-by-renderer").is_file()


@pytest.mark.no_subprocess_block
def test_audit_failure_preserves_replacement_of_bound_casks_directory(
    macos_release_repository,
):
    env = macos_release_repository["env"]
    repository = macos_release_repository["repository"]
    assert isinstance(env, dict)
    assert isinstance(repository, Path)
    env["DOTSYNC_RELEASE_REPLACE_CASKS_DURING_AUDIT"] = "1"

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    replacement = repository / "Casks" / "dotsync-app.rb"
    owned = repository / "Casks.created-by-renderer" / "dotsync-app.rb"
    assert result.returncode != 0
    assert replacement.read_bytes() == b"replacement-directory-cask-must-survive\n"
    assert not owned.exists()


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "seam",
    ["bind-to-render", "render-to-audit", "audit-to-cleanup"],
)
@pytest.mark.parametrize("outside_canonical_parent", [False, True])
def test_release_preserves_casks_directory_replacement_at_every_identity_seam(
    macos_release_repository, seam, outside_canonical_parent
):
    env = macos_release_repository["env"]
    repository = macos_release_repository["repository"]
    assert isinstance(env, dict)
    assert isinstance(repository, Path)
    env["DOTSYNC_RELEASE_REBIND_CASKS_SEAM"] = seam
    if outside_canonical_parent:
        env["DOTSYNC_RELEASE_REBIND_CASKS_OUTSIDE"] = "1"

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    replacement = repository / "Casks"
    rebound_root = Path(env["DOTSYNC_RELEASE_REBOUND_CASKS_ROOT"])
    bound_original = (
        rebound_root if outside_canonical_parent else repository
    ) / f"Casks.bound-original-{seam}"
    assert result.returncode != 0
    assert (replacement / "replacement-marker").read_text(encoding="utf-8") == (
        "replacement directory must survive\n"
    )
    assert bound_original.is_dir()
    assert list(repository.rglob("dotsync-app.rb")) == []
    assert list(rebound_root.rglob("dotsync-app.rb")) == []


@pytest.mark.no_subprocess_block
def test_second_signal_during_finalizer_is_deferred_until_exact_cleanup(
    macos_release_repository, tmp_path
):
    env = macos_release_repository["env"]
    repository = macos_release_repository["repository"]
    assert isinstance(env, dict)
    assert isinstance(repository, Path)
    work_record = tmp_path / "second-signal-work-record"
    env["DOTSYNC_RELEASE_WORK_RECORD"] = str(work_record)
    env["DOTSYNC_RELEASE_SIGNAL_PREFIX"] = json.dumps(["brew", "audit"])
    env["DOTSYNC_RELEASE_SIGNAL_NUMBER"] = str(signal.SIGHUP)
    env["DOTSYNC_RELEASE_SECOND_SIGNAL_DURING_CLEANUP"] = str(signal.SIGTERM)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    work = Path(work_record.read_text(encoding="utf-8"))
    assert result.returncode == 128 + signal.SIGHUP
    assert not work.exists()
    assert not (repository / "Casks" / "dotsync-app.rb").exists()


@pytest.mark.no_subprocess_block
def test_signal_immediately_before_release_commit_rolls_back_and_fails(
    macos_release_repository,
):
    env = macos_release_repository["env"]
    repository = macos_release_repository["repository"]
    assert isinstance(env, dict)
    assert isinstance(repository, Path)
    env["DOTSYNC_RELEASE_SIGNAL_PREFIX"] = json.dumps(
        [
            "python-release",
            str(repository / "scripts" / "macos_release_support.py"),
            "verify-canonical-directory-fd",
        ]
    )
    env["DOTSYNC_RELEASE_SIGNAL_OCCURRENCE"] = "2"
    env["DOTSYNC_RELEASE_SIGNAL_NUMBER"] = str(signal.SIGHUP)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode == 128 + signal.SIGHUP
    assert not (repository / "Casks" / "dotsync-app.rb").exists()
    assert "only if this process exits 0" in result.stdout


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize("boundary", ["renderer", "audit"])
@pytest.mark.parametrize("signal_number", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
def test_signals_after_cask_generation_converge_on_failing_rollback(
    macos_release_repository, boundary, signal_number
):
    env = macos_release_repository["env"]
    repository = macos_release_repository["repository"]
    assert isinstance(env, dict)
    assert isinstance(repository, Path)
    if boundary == "renderer":
        prefix = ["python-release", str(repository / "scripts" / "render_cask.py")]
    else:
        prefix = ["brew", "audit"]
    env["DOTSYNC_RELEASE_SIGNAL_PREFIX"] = json.dumps(prefix)
    env["DOTSYNC_RELEASE_SIGNAL_NUMBER"] = str(signal_number)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not (repository / "Casks" / "dotsync-app.rb").exists()


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "variable",
    ["DEVELOPER_ID_APPLICATION", "NOTARYTOOL_PROFILE"],
)
def test_signed_macos_release_rejects_missing_or_blank_credentials_before_signing(
    macos_release_repository, variable
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env[variable] = "   "

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    calls = _release_calls(macos_release_repository)
    assert not any(call[0] == "codesign" for call in calls)
    assert not any(call[:3] == ["gh", "release", "upload"] for call in calls)
    repository = macos_release_repository["repository"]
    assert isinstance(repository, Path)
    assert not (repository / "Casks" / "dotsync-app.rb").exists()


@pytest.mark.no_subprocess_block
def test_signed_macos_release_rejects_unresolvable_developer_identity(
    macos_release_repository,
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env["DOTSYNC_RELEASE_IDENTITY_UNRESOLVABLE"] = "1"

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    calls = _release_calls(macos_release_repository)
    assert any(call[0] == "security" for call in calls)
    assert not any(call[0] == "codesign" for call in calls)


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "security_output",
    [
        f'not-an-identity "{TEST_IDENTITY}"',
        f'  1) SHORT "{TEST_IDENTITY}"',
        f'  1) {"A" * 40} "{TEST_IDENTITY}" trailing',
        f'  1) {"A" * 40} "{TEST_IDENTITY[:-1]}X"',
    ],
)
def test_signed_macos_release_rejects_malformed_identity_tool_output(
    macos_release_repository, security_output
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env["DOTSYNC_RELEASE_SECURITY_OUTPUT"] = security_output

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(call[0] == "codesign" for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "identity",
    [
        "Apple Development: Release Test (TEAMID1234)",
        "Developer ID Installer: Release Test (TEAMID1234)",
        "Developer ID Application:",
        " Developer ID Application: Release Test (TEAMID1234)",
    ],
)
def test_signed_macos_release_rejects_wrong_or_malformed_identity_class(
    macos_release_repository, identity
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env["DEVELOPER_ID_APPLICATION"] = identity

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(call[0] == "codesign" for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "architectures",
    [
        "x86_64 arm64 i386",
        "x86_64 arm64 arm64",
        "arm64",
        "",
        "x86_64 arm64\nunexpected",
        " x86_64 arm64",
        "x86_64 arm64 ",
        "x86_64 arm64\n",
    ],
)
def test_signed_macos_release_requires_exact_universal_architecture_set(
    macos_release_repository, architectures
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env["DOTSYNC_RELEASE_ARCHS"] = architectures

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    calls = _release_calls(macos_release_repository)
    assert any(call[-1:] == ["-archs"] for call in calls)
    assert not any(call[0] == "security" for call in calls)


@pytest.mark.no_subprocess_block
def test_signed_macos_release_rejects_built_plist_version_mismatch(
    macos_release_repository,
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env["DOTSYNC_RELEASE_BUILT_PLIST_VERSION"] = "9.9.9"

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    calls = _release_calls(macos_release_repository)
    assert any(call[:2] == ["bash", "scripts/build_macos_app.sh"] for call in calls)
    assert not any(call[0] == "lipo" for call in calls)
    assert not any(call[0] == "codesign" for call in calls)


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize("plist_shape", ["absent", "symlink", "directory", "unreadable"])
def test_signed_macos_release_requires_exact_regular_readable_info_plist(
    macos_release_repository, plist_shape
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env["DOTSYNC_RELEASE_BUILT_PLIST_SHAPE"] = plist_shape

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    calls = _release_calls(macos_release_repository)
    assert any(call[:2] == ["bash", "scripts/build_macos_app.sh"] for call in calls)
    assert not any(call[0] == "lipo" for call in calls)
    assert not any(call[0] == "codesign" for call in calls)


GATE_FAILURE_CASES = [
    (["git", "rev-parse", "--show-toplevel"], 1),
    (["git", "rev-parse", "--path-format=absolute", "--git-dir"], 1),
    (["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], 1),
    (["git", "branch", "--show-current"], 1),
    (["git", "status"], 1),
    (["git", "rev-parse", "HEAD"], 1),
    (["git", "cat-file", "-t"], 1),
    (["git", "rev-parse", "--verify"], 1),
    (["python-release", "SUPPORT", "validate-temp-root"], 1),
    (["python-release", "SUPPORT", "identity-current"], 1),
    (["python-release", "SUPPORT", "identity-current"], 2),
    (["git", "archive"], 1),
    (["python-release", "SUPPORT", "identity-here"], 1),
    (["tar", "-xf"], 1),
    (["python-release", "SUPPORT", "identity-here"], 2),
    (["python-release", "SUPPORT", "identity-current"], 3),
    (["python-release", "-c"], 1),
    (["python-release", "-m", "pytest"], 1),
    (["node", "--test"], 1),
    (["swift", "test"], 1),
    (["python-release", "-m", "dotsync"], 1),
    (["bash", "scripts/build_macos_app.sh"], 1),
    (["python-release", "SUPPORT", "read-app-plist-versions"], 1),
    (["lipo"], 1),
    (["lipo"], 2),
    (["security"], 1),
    (["python-release", "-c"], 2),
    (["xcrun", "notarytool", "history"], 1),
    (["python-release", "-c"], 3),
    (["codesign", "--force"], 1),
    (["codesign", "--verify"], 1),
    (["ditto", "-c"], 1),
    (["python-release", "SUPPORT", "identity-parent"], 1),
    (["xcrun", "notarytool", "submit"], 1),
    (["python-release", "-c"], 4),
    (["xcrun", "stapler", "staple"], 1),
    (["xcrun", "stapler", "validate"], 1),
    (["spctl"], 1),
    (["ditto", "-c"], 2),
    (["python-release", "SUPPORT", "identity-parent"], 2),
    (["shasum"], 1),
    (["gh", "release", "view"], 1),
    (["python-release", "-c"], 5),
    (["gh", "release", "upload"], 1),
    (["python-release", "SUPPORT", "identity-current"], 4),
    (["python-release", "SUPPORT", "identity-path-entry"], 1),
    (["python-release", "SUPPORT", "identity-directory-fd"], 1),
    (["python-release", "SUPPORT", "identity-here"], 3),
    (["python-release", "RENDERER"], 1),
    (["python-release", "SUPPORT", "read-cask-binding"], 1),
    (["python-release", "SUPPORT", "verify-canonical-directory-fd"], 1),
    (["brew", "audit"], 1),
    (["python-release", "SUPPORT", "cleanup-current"], 1),
    (["python-release", "SUPPORT", "verify-canonical-directory-fd"], 2),
]


def _matching_occurrence_index(
    calls: list[list[str]], prefix: list[str], occurrence: int
) -> int:
    matching = [
        index for index, call in enumerate(calls) if call[: len(prefix)] == prefix
    ]
    assert len(matching) >= occurrence
    return matching[occurrence - 1]


def _expected_cleanup_call_before(
    success_calls: list[list[str]], failure_index: int
) -> list[str]:
    cleanup = next(
        call for call in success_calls if call[:3] == [
            "python-release",
            "$REPOSITORY/scripts/macos_release_support.py",
            "cleanup-current",
        ]
    )
    expected = cleanup[:11]
    owned_entries = [
        (
            [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "identity-here",
                "source.tar",
            ],
            "source.tar:$DEV:$INO:f",
        ),
        (
            [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "identity-here",
                "source",
            ],
            "source:$DEV:$INO:d",
        ),
        (
            [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "identity-parent",
                "DotSync-notarization-0.3.0.zip",
            ],
            "DotSync-notarization-0.3.0.zip:$DEV:$INO:f",
        ),
        (
            [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "identity-parent",
                "DotSync-0.3.0-macOS.zip",
            ],
            "DotSync-0.3.0-macOS.zip:$DEV:$INO:f",
        ),
        (
            [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "identity-here",
                "cask-binding.json",
            ],
            "cask-binding.json:$DEV:$INO:f",
        ),
    ]
    for creator, owned_entry in owned_entries:
        if success_calls.index(creator) < failure_index:
            expected.extend(["--owned", owned_entry])
    return expected


def _expected_finalizer_suffix(
    success_calls: list[list[str]], failure_index: int
) -> list[list[str]]:
    support_prefix = [
        "python-release",
        "$REPOSITORY/scripts/macos_release_support.py",
    ]
    work_binding = support_prefix + ["identity-current", "--require-mode", "0700"]
    casks_binding = support_prefix + ["identity-path-entry", "$REPOSITORY", "Casks"]
    casks_descriptor_binding = support_prefix + [
        "identity-directory-fd", "9", "$DEV:$INO"
    ]
    casks_revalidation = support_prefix + [
        "verify-canonical-directory-fd",
        "9", "$DEV:$INO", "$REPOSITORY", "Casks",
    ]
    cask_binding_file = support_prefix + ["identity-here", "cask-binding.json"]
    renderer = ["python-release", "$REPOSITORY/scripts/render_cask.py"]
    read_binding = support_prefix + [
        "read-cask-binding",
        "cask-binding.json",
        "cask-binding.json:$DEV:$INO:f",
    ]
    cleanup = next(
        call for call in success_calls if call[:3] == support_prefix + ["cleanup-current"]
    )
    final_revalidation_index = len(success_calls) - 1

    work_active = failure_index > success_calls.index(work_binding)
    cask_active = failure_index > success_calls.index(casks_descriptor_binding)
    cask_binding_owned = failure_index > success_calls.index(cask_binding_file)
    read_binding_index = success_calls.index(read_binding)
    cleanup_index = success_calls.index(cleanup)
    suffix: list[list[str]] = []

    if cask_active and failure_index <= read_binding_index:
        expected_binding = read_binding if cask_binding_owned else read_binding[:-1] + [""]
        suffix.append(expected_binding)
    if work_active and failure_index < cleanup_index:
        suffix.append(_expected_cleanup_call_before(success_calls, failure_index))
    if cask_active and failure_index != final_revalidation_index:
        suffix.append(casks_revalidation)
    if cask_active:
        rollback = renderer + [
            "--rollback-created",
            "--repository-root",
            "$REPOSITORY",
            "--casks-dev",
            "$NUMBER",
            "--casks-ino",
            "$NUMBER",
            "--casks-fd",
            "9",
        ]
        if failure_index >= read_binding_index:
            rollback.extend(
                [
                    "--cask-dev",
                    "$NUMBER",
                    "--cask-ino",
                    "$NUMBER",
                ]
            )
        rollback.append("--remove-casks-directory")
        suffix.append(rollback)
    return suffix


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize("failure_prefix,failure_occurrence", GATE_FAILURE_CASES)
def test_each_failed_release_gate_stops_without_cask_or_trailing_external_calls(
    macos_release_repository, failure_prefix, failure_occurrence
):
    env = macos_release_repository["env"]
    repository = macos_release_repository["repository"]
    assert isinstance(env, dict)
    assert isinstance(repository, Path)
    actual_prefix = list(failure_prefix)
    if actual_prefix[:2] == ["python-release", "SUPPORT"]:
        actual_prefix[1] = str(repository / "scripts" / "macos_release_support.py")
    elif actual_prefix == ["python-release", "RENDERER"]:
        actual_prefix = [
            "python-release",
            str(repository / "scripts" / "render_cask.py"),
        ]
    env["DOTSYNC_RELEASE_FAIL_PREFIX"] = json.dumps(actual_prefix)
    env["DOTSYNC_RELEASE_FAIL_OCCURRENCE"] = str(failure_occurrence)

    normalized_failure_prefix = list(actual_prefix)
    normalized_failure_prefix[1:] = [
        value.replace(str(repository), "$REPOSITORY")
        for value in normalized_failure_prefix[1:]
    ]
    success_calls = _expected_success_calls()
    failure_index = _matching_occurrence_index(
        success_calls,
        normalized_failure_prefix,
        failure_occurrence,
    )
    expected_calls = success_calls[: failure_index + 1]
    expected_calls.extend(_expected_finalizer_suffix(success_calls, failure_index))

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert _normalized_release_calls(macos_release_repository) == expected_calls

    temporary_root = Path(env["TMPDIR"])
    preserved_work_failures = {
        success_calls.index(
            [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "identity-current",
                "--require-mode",
                "0700",
            ]
        ),
        success_calls.index(
            [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "identity-here",
                "source.tar",
            ]
        ),
        success_calls.index(
            [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "identity-here",
                "source",
            ]
        ),
        success_calls.index(
            [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "identity-parent",
                "DotSync-notarization-0.3.0.zip",
            ]
        ),
        success_calls.index(
            [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "identity-parent",
                "DotSync-0.3.0-macOS.zip",
            ]
        ),
        success_calls.index(
            [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "identity-here",
                "cask-binding.json",
            ]
        ),
        next(
            index
            for index, call in enumerate(success_calls)
            if call[:3]
            == [
                "python-release",
                "$REPOSITORY/scripts/macos_release_support.py",
                "cleanup-current",
            ]
        ),
    }
    work_directories = list(temporary_root.glob("dotsync-macos-release.*"))
    assert len(work_directories) == (1 if failure_index in preserved_work_failures else 0)

    assert not (repository / "Casks" / "dotsync-app.rb").exists()
    initial_casks_binding_index = success_calls.index(
        [
            "python-release",
            "$REPOSITORY/scripts/macos_release_support.py",
            "identity-path-entry",
            "$REPOSITORY",
            "Casks",
        ]
    )
    casks_descriptor_binding_index = success_calls.index(
        [
            "python-release",
            "$REPOSITORY/scripts/macos_release_support.py",
            "identity-directory-fd",
            "9",
            "$DEV:$INO",
        ]
    )
    assert (repository / "Casks").exists() == (
        failure_index in {initial_casks_binding_index, casks_descriptor_binding_index}
    )


@pytest.mark.no_subprocess_block
def test_notary_profile_validation_failure_stops_before_sign_or_upload(
    macos_release_repository,
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env["DOTSYNC_RELEASE_FAIL_PREFIX"] = json.dumps(
        ["xcrun", "notarytool", "history"]
    )

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    calls = _release_calls(macos_release_repository)
    assert not any(call[0] == "codesign" for call in calls)
    assert not any(call[:3] == ["gh", "release", "upload"] for call in calls)


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "variable,value",
    [
        ("DOTSYNC_RELEASE_NOTARY_HISTORY_OUTPUT", ""),
        ("DOTSYNC_RELEASE_NOTARY_HISTORY_OUTPUT", "not-json"),
        ("DOTSYNC_RELEASE_NOTARY_SUBMIT_OUTPUT", ""),
        ("DOTSYNC_RELEASE_NOTARY_SUBMIT_OUTPUT", "not-json"),
        ("DOTSYNC_RELEASE_NOTARY_SUBMIT_OUTPUT", '{"id":"","status":"Accepted"}'),
        (
            "DOTSYNC_RELEASE_NOTARY_SUBMIT_OUTPUT",
            '{"id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","status":"Invalid"}',
        ),
        (
            "DOTSYNC_RELEASE_NOTARY_SUBMIT_OUTPUT",
            '[{"id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","status":"Accepted"}]',
        ),
    ],
)
def test_signed_macos_release_rejects_empty_or_malformed_notary_json(
    macos_release_repository, variable, value
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env[variable] = value

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(
        call[:3] == ["gh", "release", "upload"]
        for call in _release_calls(macos_release_repository)
    )


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "sha_output",
    [
        "",
        FINAL_ARCHIVE_SHA256,
        f"{FINAL_ARCHIVE_SHA256} ../wrong-name.zip",
        f"{FINAL_ARCHIVE_SHA256}  ../wrong-name.zip",
        f"{FINAL_ARCHIVE_SHA256}  {{archive}} trailing",
        f"{FINAL_ARCHIVE_SHA256.upper()}  {{archive}}",
        f"{'0' * 64}  {{archive}}",
    ],
)
def test_signed_macos_release_rejects_malformed_or_misbinding_sha_output(
    macos_release_repository, sha_output
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env["DOTSYNC_RELEASE_SHA_OUTPUT"] = sha_output

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(
        call[:3] == ["gh", "release", "view"]
        for call in _release_calls(macos_release_repository)
    )


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "release_output",
    [
        "",
        "not-json",
        '{"id":"","assets":[]}',
        '{"id":"RE_kwDORel3as4AAAAA","assets":{}}',
        (
            '{"id":"RE_kwDORel3as4AAAAA","assets":'
            '[{"name":"DotSync-0.3.0-macOS.zip"}]}'
        ),
        '{"id":"RE_kwDORel3as4AAAAA","assets":[{}]}',
    ],
)
def test_signed_macos_release_validates_release_id_and_rejects_asset_collision(
    macos_release_repository, release_output
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env["DOTSYNC_RELEASE_GH_VIEW_OUTPUT"] = release_output

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(
        call[:3] == ["gh", "release", "upload"]
        for call in _release_calls(macos_release_repository)
    )
