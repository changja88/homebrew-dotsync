from local_dev.serena_mcp_management.ui import (
    BoxModel,
    Item,
    render_box,
)


def test_render_box_includes_title_and_phase_label():
    model = BoxModel(phase="preflight", title="codex", items=[])
    text = render_box(model)
    assert "codex" in text
    assert "preflight" in text


def test_render_box_includes_each_item_label_and_value():
    model = BoxModel(
        phase="preflight",
        title="codex",
        items=[
            Item(id="workspace", label="workspace", value="~/repo", status="done"),
            Item(id="cleanup", label="cleanup", value="0 to delete . 103 to keep"),
        ],
    )
    text = render_box(model)
    assert "workspace" in text
    assert "~/repo" in text
    assert "cleanup" in text
    assert "0 to delete . 103 to keep" in text


def test_render_box_uses_done_marker_for_done_items():
    model = BoxModel(
        phase="launch-prep",
        title="codex",
        items=[Item(id="cleanup", label="cleanup",
                    value="0 deleted . 103 kept", status="done")],
    )
    text = render_box(model)
    assert "✓" in text


def test_render_box_uses_warn_marker_for_warn_items():
    model = BoxModel(
        phase="preflight",
        title="codex",
        items=[Item(id="serena", label="serena",
                    value="project config missing", status="warn")],
    )
    text = render_box(model)
    assert "!" in text


def test_render_box_spin_frame_cycles_through_braille_set():
    model = BoxModel(
        phase="launch-prep",
        title="codex",
        items=[Item(id="mcp", label="serena", value="preparing", status="spin")],
    )
    frame_zero = render_box(model, spin_frame=0)
    frame_one = render_box(model, spin_frame=1)
    assert "⠋" in frame_zero
    assert "⠙" in frame_one


def test_render_box_ends_with_newline():
    model = BoxModel(phase="preflight", title="codex", items=[])
    assert render_box(model).endswith("\n")
