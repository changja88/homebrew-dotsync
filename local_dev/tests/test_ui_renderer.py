import io
import re

from local_dev.serena_mcp_management.ui import (
    AMBER,
    BoxModel,
    BoxRenderer,
    Item,
    _BANNER_SHADOW_RGB,
    render_box,
    render_inline_row,
)

_SHADOW_CODE = "48;2;{};{};{}".format(*_BANNER_SHADOW_RGB)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def test_render_box_includes_title_art_and_phase_label():
    model = BoxModel(phase="preflight", title="codex", items=[])
    text = render_box(model)
    plain = _strip_ansi(text)
    # Known clients render as a half-block pixel banner: solid color mass
    # with two pixel rows per terminal row. The old shadow-line block font
    # (██╗ …) read as hollow outlines on a light background.
    assert "▀" in plain
    assert "╗" not in plain
    assert "preflight" in plain


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


def test_render_box_aligns_multiline_values_under_value_column():
    model = BoxModel(
        phase="preflight",
        title="codex",
        items=[
            Item(
                id="sessions",
                label="sessions",
                value=(
                    "codex\n"
                    "├─ groups   58 total\n"
                    "└─ cleanup  inactive longer than 5 days"
                ),
                status="info",
            )
        ],
    )

    lines = _strip_ansi(render_box(model)).splitlines()
    parent = next(line for line in lines if "sessions" in line)
    groups = next(line for line in lines if "├─ groups" in line)
    cleanup = next(line for line in lines if "└─ cleanup" in line)

    value_column = parent.index("codex")
    assert groups.index("├─") == value_column
    assert cleanup.index("└─") == value_column


def test_render_box_sizes_border_by_longest_multiline_value():
    model = BoxModel(
        phase="preflight",
        title="codex",
        items=[
            Item(
                id="sessions",
                label="sessions",
                value=(
                    "codex\n"
                    "└─ records  855 total · 358 to delete · 497 to keep"
                ),
            )
        ],
    )

    plain_lines = _strip_ansi(render_box(model)).splitlines()
    border_width = max(
        len(line.strip()) for line in plain_lines if set(line.strip()) == {"─"}
    )
    record_line = next(line for line in plain_lines if "└─ records" in line)

    assert border_width == max(60, len(record_line) - 2)


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


def test_render_inline_row_uses_requested_spinner_frame():
    first = _strip_ansi(
        render_inline_row(
            "sessions",
            "deleting inactive sessions",
            status="spin",
            accent=AMBER,
            spin_frame=0,
        )
    )
    second = _strip_ansi(
        render_inline_row(
            "sessions",
            "deleting inactive sessions",
            status="spin",
            accent=AMBER,
            spin_frame=1,
        )
    )

    assert first.startswith("  ⠋ sessions")
    assert second.startswith("  ⠙ sessions")


def test_render_box_ends_with_newline():
    model = BoxModel(phase="preflight", title="codex", items=[])
    assert render_box(model).endswith("\n")


def test_box_renderer_first_draw_writes_text_only():
    stream = io.StringIO()
    renderer = BoxRenderer(stream=stream)
    model = BoxModel(phase="preflight", title="codex", items=[])
    renderer.draw(model)
    output = stream.getvalue()
    # codex pixel-banner fingerprint sits inside the rendered box.
    assert "▀" in _strip_ansi(output)
    # no cursor movement (up/erase) before first frame; color codes ok
    prefix = output[: output.find("▀")]
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


def test_render_box_uses_info_marker_for_info_items():
    model = BoxModel(
        phase="preflight",
        title="codex",
        items=[Item(id="workspace", label="workspace",
                    value="~/repo", status="info")],
    )
    text = render_box(model)
    assert "·" in text
