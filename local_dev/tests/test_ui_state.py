from local_dev.serena_mcp_management.ui import (
    BoxModel,
    Item,
    PhaseKind,
    ItemStatus,
)


def test_item_default_status_is_pending():
    item = Item(id="workspace", label="workspace", value="~/repo")
    assert item.status == "pending"


def test_box_model_holds_phase_and_items():
    items = [
        Item(id="workspace", label="workspace", value="~/repo", status="done"),
        Item(id="cleanup", label="cleanup", value="0 to delete . 103 to keep"),
    ]
    model = BoxModel(phase="preflight", title="codex", items=items)

    assert model.phase == "preflight"
    assert model.title == "codex"
    assert model.items[0].id == "workspace"
    assert model.items[1].status == "pending"


def test_box_model_replace_item_updates_by_id():
    model = BoxModel(
        phase="launch-prep",
        title="codex",
        items=[Item(id="cleanup", label="cleanup", value="pending"),
               Item(id="mcp", label="serena", value="pending")],
    )
    model.replace_item(Item(id="cleanup", label="cleanup",
                            value="0 deleted . 103 kept", status="done"))
    assert model.items[0].status == "done"
    assert model.items[0].value == "0 deleted . 103 kept"
    assert model.items[1].status == "pending"


def test_box_model_replace_item_unknown_id_raises():
    import pytest
    model = BoxModel(phase="preflight", title="codex", items=[])
    with pytest.raises(KeyError):
        model.replace_item(Item(id="nope", label="nope", value=""))


def test_phase_kinds_match_spec():
    valid: set[PhaseKind] = {"preflight", "serena-init", "launch-prep", "summary"}
    assert "preflight" in valid
    assert "summary" in valid


def test_item_statuses_match_spec():
    valid: set[ItemStatus] = {"pending", "spin", "done", "warn", "skip"}
    assert "pending" in valid
    assert "warn" in valid
