import io
import re

from local_dev.serena_mcp_management.ui import (
    AMBER,
    BoxModel,
    BoxRenderer,
    Item,
    PINK,
    PURPLE,
    _BANNER_SHADOW_RGB,
    _STAR_BRIGHT_RGB,
    _STAR_DIM_RGB,
    render_box,
    render_inline_row,
    style_action_value,
    style_mcp_inventory,
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


def test_render_inline_row_colors_session_start_with_requested_accent():
    rendered = render_inline_row(
        "sessions",
        "deleting inactive sessions",
        status="spin",
        accent=AMBER,
    )

    assert f"\x1b[{AMBER}m" in rendered
    assert _strip_ansi(rendered) == (
        "  ⠋ sessions    deleting inactive sessions\n"
    )


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


def test_style_action_value_wraps_complete_value_in_accent():
    assert style_action_value("8 sessions deleted", accent=AMBER) == (
        f"\x1b[{AMBER}m8 sessions deleted\x1b[0m"
    )


def test_style_mcp_inventory_renders_single_line_plain_text():
    text = style_mcp_inventory(
        ps_servers=3,
        managed_servers=2,
        orphan_servers=1,
        leases=3,
        stale_leases=1,
    )

    plain = _strip_ansi(text)
    assert plain == (
        "server processes[3] → managed servers[2] · "
        "orphaned servers[1] · leases[3] · stale leases[1]"
    )
    assert "ps[" not in plain


def test_style_mcp_inventory_highlights_orphan_and_stale_when_nonzero():
    text = style_mcp_inventory(
        ps_servers=3,
        managed_servers=2,
        orphan_servers=1,
        leases=3,
        stale_leases=1,
    )

    assert f"\x1b[{AMBER}m" in text
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


def test_render_box_claude_uses_pixel_block_font():
    """claude is unified with codex on the half-block pixel font."""
    model = BoxModel(phase="preflight", title="claude", items=[])
    plain = _strip_ansi(render_box(model))
    assert "▀" in plain
    assert "╗" not in plain


def test_render_box_applies_gradient_per_pixel():
    """Banner cells are colored pixel-by-pixel: many distinct foreground
    stops, plus background paint carrying the lower pixel of full cells."""
    model = BoxModel(phase="preflight", title="codex", items=[])
    text = render_box(model)
    fg = re.findall(r"\x1b\[38;2;\d+;\d+;\d+m", text)
    # Plenty of distinct interpolated stops, not a single line-wide color.
    assert len(set(fg)) >= 8
    assert re.search(r"\x1b\[48;2;\d+;\d+;\d+m", text)


def test_render_box_uses_double_gradient_border():
    """Top/bottom borders are doubled gradient ribbons: a heavy outer line
    starting at the pink endpoint plus a hairline echo starting at purple
    (the outline-offset look, tuned for a light background)."""
    model = BoxModel(phase="preflight", title="codex", items=[])
    text = render_box(model)
    lines = text.split("\n")
    top_outer, top_inner = lines[0], lines[1]
    bottom_inner, bottom_outer = lines[-3], lines[-2]

    truecolor = re.compile(r"\x1b\[1;38;2;\d+;\d+;\d+m")
    for line in (top_outer, top_inner, bottom_inner, bottom_outer):
        # A ribbon sweeps through many interpolated stops, not one flat color.
        assert len(set(truecolor.findall(line))) >= 8

    for line in (top_outer, bottom_outer):
        assert "━" in _strip_ansi(line)
        assert line.startswith("  " + f"\x1b[1;{PINK}m")
    for line in (top_inner, bottom_inner):
        assert "─" in _strip_ansi(line)
        assert line.startswith("  " + f"\x1b[1;{PURPLE}m")


def test_render_box_shifts_banner_gradient_per_row():
    """Art rows advance the gradient phase, producing a diagonal sweep: the
    same column shows different colors on different rows."""
    model = BoxModel(phase="preflight", title="codex", items=[])
    text = render_box(model)
    art_lines = [line for line in text.split("\n") if "\x1b[48;2;" in line]
    assert len(art_lines) >= 5

    # Glyph gradient hues are the only bright foregrounds on the band —
    # stars and shadow pixels stay well below this channel ceiling.
    first_stop = re.compile(r"\x1b\[38;2;(\d+);(\d+);(\d+)m")
    leading_colors = []
    for line in art_lines:
        for match in first_stop.finditer(line):
            if max(int(c) for c in match.groups()) >= 200:
                leading_colors.append(match.group(0))
                break
    assert len(set(leading_colors)) >= 4


def test_render_box_banner_draws_textured_hero_band():
    """The banner sits on a full-width dark hero band: not a flat fill but a
    subtle tint gradient with sparse star pixels, plus the darker drop
    shadow the glyphs cast down-right."""
    model = BoxModel(phase="preflight", title="codex", items=[])
    text = render_box(model)
    art_lines = [line for line in text.split("\n") if "\x1b[48;2;" in line]
    # 2 pad + 12 glyph + 2 shadow + 2 pad pixel rows = 9 terminal rows.
    assert len(art_lines) == 9
    # The band itself sweeps through several tints on a single padding row.
    top_tints = set(re.findall(r"\x1b\[48;2;\d+;\d+;\d+m", art_lines[0]))
    assert len(top_tints) >= 3
    # Sparse stars sparkle somewhere on the band.
    dim = "38;2;{};{};{}".format(*_STAR_DIM_RGB)
    bright = "38;2;{};{};{}".format(*_STAR_BRIGHT_RGB)
    assert any(dim in line or bright in line for line in art_lines)
    # The drop shadow appears as its own darker color on the band.
    assert any(_SHADOW_CODE in line for line in art_lines)
