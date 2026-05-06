from local_dev.serena_mcp_management.serena_mcp.paths import Scope, find_project_root, state_dir_for


def test_find_project_root_uses_git_root(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()

    assert find_project_root(nested) == repo.resolve()


def test_find_project_root_prefers_serena_project_root(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    (repo / ".serena").mkdir()
    (repo / ".serena" / "project.yml").write_text("name: repo\n")

    assert find_project_root(nested) == repo.resolve()


def test_find_project_root_uses_common_project_markers(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\n")

    assert find_project_root(nested) == repo.resolve()


def test_find_project_root_falls_back_to_cwd(tmp_path):
    assert find_project_root(tmp_path) == tmp_path.resolve()


def test_scope_key_separates_client_type(tmp_path):
    root = tmp_path.resolve()

    assert Scope(root, "codex").key != Scope(root, "claude").key


def test_state_dir_lives_under_project_serena_dir(tmp_path):
    scope = Scope(tmp_path.resolve(), "codex")

    assert state_dir_for(scope) == tmp_path.resolve() / ".serena" / "dotsync-mcp" / "codex"
