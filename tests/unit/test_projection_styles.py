"""Unit tests for the include_styles channel in projection.project().

Covers:
- default-styled runs → no _styles channel (token hygiene)
- non-default runs → _styles emitted with only carrier fields
- multiple runs on same shape → all emitted (boundary information)
- faithful + fallback modes also get _styles when flag is set
"""
from __future__ import annotations

from slides_mcp.normalize import FlatShape, TextRun
from slides_mcp.projection import (
    _is_default_run,
    _projected_styles,
    _run_to_dict,
    project,
)
from slides_mcp.theme import load_theme


def _sub():
    return load_theme("example").sub("primary")


# ---------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------

class TestIsDefaultRun:
    def test_all_defaults(self):
        assert _is_default_run(TextRun(content="x"))

    def test_bold_is_signal(self):
        assert not _is_default_run(TextRun(content="x", bold=True))

    def test_color_is_signal(self):
        assert not _is_default_run(TextRun(content="x", color_hex="#FF0000"))

    def test_font_family_is_signal(self):
        assert not _is_default_run(TextRun(content="x", font_family="Inter"))

    def test_size_is_signal(self):
        assert not _is_default_run(TextRun(content="x", size_pt=24.0))

    def test_italic_is_signal(self):
        assert not _is_default_run(TextRun(content="x", italic=True))


class TestRunToDict:
    def test_plain_run_has_only_text(self):
        d = _run_to_dict(TextRun(content="hello"))
        assert d == {"text": "hello"}

    def test_bold_italic_color(self):
        d = _run_to_dict(TextRun(content="x", bold=True, italic=True, color_hex="#FF0000"))
        assert d == {"text": "x", "bold": True, "italic": True, "color_hex": "#FF0000"}

    def test_font_and_size(self):
        d = _run_to_dict(TextRun(content="x", font_family="Inter", size_pt=18.0))
        assert d == {"text": "x", "font_family": "Inter", "size_pt": 18.0}

    def test_false_fields_omitted(self):
        # bold=False should not appear in output (only true signals carry)
        d = _run_to_dict(TextRun(content="x", bold=False, italic=False))
        assert d == {"text": "x"}
        assert "bold" not in d
        assert "italic" not in d


class TestProjectedStyles:
    def test_empty_shapes(self):
        assert _projected_styles([]) == {}

    def test_single_default_run_skipped(self):
        shape = FlatShape(
            object_id="s_1", kind="text",
            left_in=0, top_in=0, w_in=1, h_in=1,
            text="hello",
            runs=[TextRun(content="hello")],
        )
        assert _projected_styles([shape]) == {}

    def test_single_styled_run_emitted(self):
        shape = FlatShape(
            object_id="s_1", kind="text",
            left_in=0, top_in=0, w_in=1, h_in=1,
            text="hello",
            runs=[TextRun(content="hello", bold=True, color_hex="#FF0000")],
        )
        result = _projected_styles([shape])
        assert result == {
            "s_1": [{"text": "hello", "bold": True, "color_hex": "#FF0000"}]
        }

    def test_multiple_runs_all_emitted(self):
        # Even if some runs are default, the boundary info matters when >1 run
        shape = FlatShape(
            object_id="s_1", kind="text",
            left_in=0, top_in=0, w_in=1, h_in=1,
            text="Hello World",
            runs=[
                TextRun(content="Hello "),
                TextRun(content="World", bold=True, color_hex="#FF0000"),
            ],
        )
        result = _projected_styles([shape])
        assert result == {
            "s_1": [
                {"text": "Hello "},
                {"text": "World", "bold": True, "color_hex": "#FF0000"},
            ]
        }

    def test_non_text_shape_skipped(self):
        shape = FlatShape(
            object_id="s_1", kind="picture",
            left_in=0, top_in=0, w_in=1, h_in=1,
        )
        assert _projected_styles([shape]) == {}

    def test_shape_without_id_skipped(self):
        shape = FlatShape(
            object_id="", kind="text",
            left_in=0, top_in=0, w_in=1, h_in=1,
            text="hello",
            runs=[TextRun(content="hello", bold=True)],
        )
        assert _projected_styles([shape]) == {}

    def test_mixed_styled_and_unstyled(self):
        plain = FlatShape(
            object_id="plain", kind="text",
            left_in=0, top_in=0, w_in=1, h_in=1,
            text="body",
            runs=[TextRun(content="body")],
        )
        styled = FlatShape(
            object_id="title", kind="text",
            left_in=0, top_in=0, w_in=1, h_in=1,
            text="Title",
            runs=[TextRun(content="Title", bold=True, size_pt=42.0)],
        )
        result = _projected_styles([plain, styled])
        assert "plain" not in result  # uniform default → no signal
        assert "title" in result
        assert result["title"] == [{"text": "Title", "bold": True, "size_pt": 42.0}]


# ---------------------------------------------------------------------
# project() integration tests with include_styles
# ---------------------------------------------------------------------

class TestProjectIncludeStyles:
    def _make_shapes(self):
        """Minimal text_heavy_body-ish shape set."""
        title = FlatShape(
            object_id="c_title", kind="text",
            left_in=0.5, top_in=0.5, w_in=15, h_in=0.8,
            text="Typography matters",
            runs=[TextRun(content="Typography matters", bold=True, italic=True,
                          color_hex="#E8612E", size_pt=42.0)],
        )
        body = FlatShape(
            object_id="c_body", kind="text",
            left_in=0.5, top_in=1.6, w_in=15, h_in=5,
            text="The first sentence sets the frame. It carries intent." + " " * 20,
            runs=[
                TextRun(content="The "),
                TextRun(content="first sentence", bold=True, color_hex="#E8612E"),
                TextRun(content=" sets the frame. It carries intent."),
            ],
        )
        return [title, body]

    def test_include_styles_false_no_styles_key(self):
        shapes = self._make_shapes()
        result = project(shapes, "text_heavy_body", "slide_1", "", _sub(),
                         mode="clean", include_styles=False)
        assert "_styles" not in result

    def test_include_styles_true_emits_styles(self):
        shapes = self._make_shapes()
        result = project(shapes, "text_heavy_body", "slide_1", "", _sub(),
                         mode="clean", include_styles=True)
        assert "_styles" in result
        assert "c_title" in result["_styles"]
        assert "c_body" in result["_styles"]
        # Title's single styled run
        title_runs = result["_styles"]["c_title"]
        assert title_runs == [
            {"text": "Typography matters", "bold": True, "italic": True,
             "color_hex": "#E8612E", "size_pt": 42.0}
        ]
        # Body's 3 runs preserved
        body_runs = result["_styles"]["c_body"]
        assert len(body_runs) == 3
        assert body_runs[1] == {"text": "first sentence", "bold": True, "color_hex": "#E8612E"}

    def test_include_styles_in_faithful_mode(self):
        shapes = self._make_shapes()
        result = project(shapes, "text_heavy_body", "slide_1", "", _sub(),
                         mode="faithful", include_styles=True)
        # _styles still emitted on faithful
        assert "_styles" in result
        assert "c_title" in result["_styles"]

    def test_include_styles_suppressed_when_no_signal(self):
        """If every text shape has a single default run, _styles is omitted."""
        shapes = [
            FlatShape(
                object_id="c_title", kind="text",
                left_in=0.5, top_in=0.5, w_in=15, h_in=0.8,
                text="Plain Title",
                runs=[TextRun(content="Plain Title")],
            ),
        ]
        result = project(shapes, "text_heavy_body", "slide_1", "", _sub(),
                         mode="clean", include_styles=True)
        # _styles absent because no shape had style signal
        assert "_styles" not in result
