import io

from local_dev.serena_mcp_management.ui import (
    BoxModel,
    BoxRenderer,
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


def test_box_renderer_first_draw_writes_text_only():
    stream = io.StringIO()
    renderer = BoxRenderer(stream=stream)
    model = BoxModel(phase="preflight", title="codex", items=[])
    renderer.draw(model)
    output = stream.getvalue()
    assert "codex" in output
    # no cursor movement (up/erase) before first frame; color codes ok
    prefix = output[: output.find("codex")]
    assert "A\x1b[J" not in prefix  # cursor-up + erase sequence should not appear


def test_box_renderer_second_draw_emits_cursor_up_for_previous_lines():
    stream = io.StringIO()
    renderer = BoxRenderer(stream=stream)
    model = BoxModel(
        phase="preflight",
        title="codex",
        items=[Item(id="workspace", label="workspace", value="~/repo")],
    )
    renderer.draw(model)
    first_len = len(stream.getvalue())
    renderer.draw(model)
    second_chunk = stream.getvalue()[first_len:]
    assert "\x1b[" in second_chunk
    assert "A" in second_chunk  # cursor up
    assert "J" in second_chunk  # erase below


def test_box_renderer_clear_emits_cursor_up_and_erase():
    stream = io.StringIO()
    renderer = BoxRenderer(stream=stream)
    renderer.draw(BoxModel(phase="preflight", title="codex", items=[]))
    cleared_at = len(stream.getvalue())
    renderer.clear()
    chunk = stream.getvalue()[cleared_at:]
    assert "A" in chunk
    assert "J" in chunk


def test_box_renderer_clear_resets_line_count_for_next_draw():
    stream = io.StringIO()
    renderer = BoxRenderer(stream=stream)
    renderer.draw(BoxModel(phase="preflight", title="codex", items=[]))
    renderer.clear()
    after_clear = len(stream.getvalue())
    renderer.draw(BoxModel(phase="preflight", title="codex", items=[]))
    third_chunk = stream.getvalue()[after_clear:]
    assert "A" not in third_chunk  # treats next draw as first frame
