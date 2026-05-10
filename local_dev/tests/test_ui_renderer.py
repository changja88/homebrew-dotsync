import io
import re

from local_dev.serena_mcp_management.ui import (
    BoxModel,
    BoxRenderer,
    Item,
    PINK,
    PURPLE,
    render_box,
    style_mcp_inventory,
)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def test_render_box_includes_title_art_and_phase_label():
    model = BoxModel(phase="preflight", title="codex", items=[])
    text = render_box(model)
    plain = _strip_ansi(text)
    # Known clients render as a block ASCII banner instead of plain text title.
    assert "██████╗" in plain
    assert "╚═════╝" in plain
    assert "preflight" in plain


def test_render_box_colors_known_title_art_pink_and_purple():
    model = BoxModel(phase="preflight", title="codex", items=[])
    text = render_box(model)
    # Both palette accents must appear: PINK shows up on the outer border
    # (non-bold), PURPLE reaches its endpoint at the gradient mid-cell.
    assert f"\x1b[{PINK}m" in text
    assert f"\x1b[1;{PURPLE}m" in text


def test_render_box_falls_back_to_plain_title_for_unknown_client():
    model = BoxModel(phase="preflight", title="other-client", items=[])
    text = render_box(model)
    assert "other-client" in text
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


def test_style_mcp_inventory_renders_single_line_plain_text():
    text = style_mcp_inventory(
        ps_servers=3,
        managed_servers=2,
        orphan_servers=1,
        leases=3,
        stale_leases=1,
    )

    assert _strip_ansi(text) == (
        "ps[3 servers] -> managed[2 servers] . "
        "orphan[1] . leases[3] . stale[1]"
    )


def test_style_mcp_inventory_highlights_orphan_and_stale_when_nonzero():
    text = style_mcp_inventory(
        ps_servers=3,
        managed_servers=2,
        orphan_servers=1,
        leases=3,
        stale_leases=1,
    )

    assert "\x1b[33m" in text
    assert "orphan" in text
    assert "stale" in text


def test_render_box_expands_border_for_long_mcp_inventory_row():
    model = BoxModel(
        phase="preflight",
        title="codex",
        items=[
            Item(
                id="serena-mcp",
                label="serena mcp",
                value=style_mcp_inventory(
                    ps_servers=123,
                    managed_servers=122,
                    orphan_servers=1,
                    leases=987,
                    stale_leases=1,
                ),
                status="warn",
            ),
        ],
    )

    plain_lines = _strip_ansi(render_box(model)).splitlines()
    border_width = max(
        len(line.strip()) for line in plain_lines if set(line.strip()) == {"─"}
    )
    item_width = max(len(line.strip()) for line in plain_lines if "serena mcp" in line)

    assert border_width >= item_width


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
    # codex art fingerprint sits inside the rendered box.
    assert "██████╗" in _strip_ansi(output)
    # no cursor movement (up/erase) before first frame; color codes ok
    plain = _strip_ansi(output)
    prefix = output[: output.find(plain[plain.find("██████╗"):plain.find("██████╗")+1])]
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


def test_box_renderer_clear_resets_line_count_for_next_draw():
    stream = io.StringIO()
    renderer = BoxRenderer(stream=stream)
    renderer.draw(BoxModel(phase="preflight", title="codex", items=[]))
    renderer.clear()
    after_clear = len(stream.getvalue())
    renderer.draw(BoxModel(phase="preflight", title="codex", items=[]))
    third_chunk = stream.getvalue()[after_clear:]
    assert "A" not in third_chunk  # treats next draw as first frame


# ----- Holographic Shimmer (concept 02) ---------------------------------------


def test_render_box_claude_uses_ansi_shadow_block_font():
    """claude is unified with codex on the ANSI Shadow block font."""
    model = BoxModel(phase="preflight", title="claude", items=[])
    plain = _strip_ansi(render_box(model))
    assert "██████╗" in plain
    assert "╚═════╝" in plain


def test_render_box_applies_horizontal_gradient_per_cell():
    """Each art line is colored cell-by-cell, producing many distinct stops."""
    model = BoxModel(phase="preflight", title="codex", items=[])
    text = render_box(model)
    truecolor = re.findall(r"\x1b\[1;38;2;\d+;\d+;\d+m", text)
    # Plenty of distinct interpolated stops, not a single line-wide color.
    assert len(set(truecolor)) >= 8


def test_render_box_uses_double_top_and_bottom_border():
    """Top and bottom borders are doubled (pink line + purple line)."""
    model = BoxModel(phase="preflight", title="codex", items=[])
    text = render_box(model)
    lines = text.split("\n")
    assert "─" in _strip_ansi(lines[0])
    assert "─" in _strip_ansi(lines[1])
    assert f"\x1b[{PINK}m" in lines[0] or f"\x1b[1;{PINK}m" in lines[0]
    assert f"\x1b[{PURPLE}m" in lines[1] or f"\x1b[1;{PURPLE}m" in lines[1]

