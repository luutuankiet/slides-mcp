"""run_deck_script, write_speaker_notes and read_slides, called as an MCP
client would, against the fake Slides API. Assertions are about external
behaviour only: what the fake received, what came back."""
from __future__ import annotations

import json
import time

import pytest

from slides_mcp import auth, scripting, slides_api
from slides_mcp.server import read_slides, run_deck_script, write_speaker_notes

DECK = "deck_fixture"


async def run(script: str, **kw):
    return await run_deck_script(deck_url=DECK, script=script, **kw)


# ---- dry run / apply ------------------------------------------------------------


async def test_dry_run_is_default_and_sends_nothing(fake):
    out = await run("""
      for (const s of deck.select()) emit(setBackground(s, "#101010"));
      return {n: deck.slides.length};
    """)
    assert out["isError"] is False
    assert out["dry_run"] is True
    assert fake.batches == []
    assert out["result"] == {"n": 6}
    prev = out["preview"]
    assert prev["request_count"] == 6
    assert prev["request_kinds"] == {"updatePageProperties": 6}
    assert "requests" not in prev
    assert prev["slides"][0]["kinds"] == {"updatePageProperties": 1}
    assert prev["slides"][0]["colors"]["after"] == ["#101010"]


async def test_include_requests_returns_the_list(fake):
    out = await run("emit(setFill(deck.slide(5).element('translucent_badge'), '#FF0000'))",
                    include_requests=True)
    assert out["preview"]["requests"][0]["updateShapeProperties"]["objectId"] == "translucent_badge"


async def test_apply_sends_exactly_one_batch(fake):
    out = await run("""
      const s = deck.slide("slide_cases");
      emit(setFill(s.element("translucent_badge"), "#00FF00", 0.5));
      emit(styleText(s.element("transparent_box"), {color: "#FF0000"}));
      return "ok";
    """, dry_run=False)
    assert out["isError"] is False, out
    assert len(fake.batches) == 1
    assert [next(iter(r)) for r in fake.batches[0]] == ["updateShapeProperties", "updateTextStyle"]
    assert out["result"] == "ok"
    r = out["receipt"]
    assert r["applied_request_count"] == 2
    assert r["affected_slide_ids"] == ["slide_cases"]
    assert r["request_kinds"] == {"updateShapeProperties": 1, "updateTextStyle": 1}
    assert r["destructive_kinds"] == []


@pytest.mark.parametrize("value", [{"accent": "#123456"}, '{"accent": "#123456"}'])
async def test_input_native_or_stringified(fake, value):
    out = await run("return input.accent", input=value)
    assert out["result"] == "#123456"


async def test_plain_string_input_stays_a_string(fake):
    out = await run("return typeof input + ':' + input", input="hello world")
    assert out["result"] == "string:hello world"


# ---- guards ---------------------------------------------------------------------


async def test_destructive_refused_without_confirmation(fake):
    script = "emit({deleteObject: {objectId: 'stat_box'}})"
    out = await run(script, dry_run=False)
    assert out["isError"] is True
    assert out["error"]["kind"] == "destructive"
    assert fake.batches == []
    ok = await run(script, dry_run=False, confirm_destructive=True)
    assert ok["isError"] is False
    assert ok["receipt"]["destructive_kinds"] == ["deleteObject"]


async def test_unknown_object_id_refused_before_api(fake):
    out = await run("emit(setFill('no_such_shape', '#000000'))", dry_run=False)
    assert out["isError"] is True
    assert out["error"]["kind"] == "validation"
    assert "no_such_shape" in out["error"]["message"]
    assert fake.batches == []


async def test_ids_created_earlier_in_batch_are_accepted(fake):
    out = await run("""
      emit(textBox("slide_cases", {id: "new_box", text: "Hi", x: inch(1), y: inch(5), w: inch(2), h: inch(0.5), style: {sizePt: 10}}));
      emit(setFill("new_box", "#EEEEEE"));
    """, dry_run=False)
    assert out["isError"] is False, out
    kinds = [next(iter(r)) for r in fake.batches[0]]
    assert kinds == ["createShape", "insertText", "updateShapeProperties", "updateTextStyle",
                     "updateShapeProperties"]
    assert fake.batches[0][2]["updateShapeProperties"]["shapeProperties"] == {
        "autofit": {"autofitType": "NONE"}}


async def test_cpu_timeout_kills_busy_loop(fake):
    t = time.monotonic()
    out = await run("while (true) {}", cpu_timeout_s=1)
    assert out["isError"] is True
    assert out["error"]["kind"] == "timeout"
    assert "cpu_timeout_s" in out["error"]["message"]
    assert time.monotonic() - t < 10


async def test_cpu_timeout_covers_code_after_commit(fake):
    out = await run("await commit(); while (true) {}", dry_run=False, cpu_timeout_s=1)
    assert out["error"]["kind"] == "timeout"


async def test_slow_api_does_not_count_against_cpu(fake):
    fake.batch_delay = 1.5
    out = await run("""
      emit(setFill("translucent_badge", "#111111"));
      await commit();
      emit(setFill("translucent_badge", "#222222"));
      return "done";
    """, dry_run=False, cpu_timeout_s=1)
    assert out["isError"] is False, out
    assert len(fake.batches) == 2


async def test_wall_clock_timeout(fake):
    fake.batch_delay = 1.2
    out = await run("""
      for (let i = 0; i < 5; i++) { emit(setFill("translucent_badge", "#111111")); await commit(); }
    """, dry_run=False, timeout_s=2, cpu_timeout_s=5)
    assert out["isError"] is True
    assert out["error"]["kind"] == "timeout"
    assert "timeout_s" in out["error"]["message"]
    assert out["receipt"]["phases"] >= 1


async def test_request_cap(fake):
    out = await run("for (let i = 0; i < 20; i++) emit(setFill('translucent_badge', '#000000'))",
                    max_requests=10)
    assert out["isError"] is True
    assert "request cap" in out["error"]["message"]


async def test_350_requests_in_one_batch(fake):
    out = await run("""
      const els = deck.slides.flatMap(s => s.elements).filter(e => e.kind === "text" || e.kind === "shape");
      let n = 0;
      while (n < 350) { for (const e of els) { if (n >= 350) break; emit(setFill(e, "#ABCDEF")); n++; } }
      return n;
    """, dry_run=False)
    assert out["isError"] is False, out
    assert out["result"] == 350
    assert len(fake.batches) == 1
    assert len(fake.batches[0]) == 350


async def test_return_value_truncated(fake):
    out = await run("return 'x'.repeat(20000)", max_return_bytes=1000)
    assert out["isError"] is False
    assert out["result"]["truncated"] is True
    assert len(out["result"]["preview"]) < 1000
    assert any("max_return_bytes" in w for w in out["warnings"])


async def test_limits_above_ceiling_are_clamped(fake):
    out = await run("return 1", timeout_s=99999)
    assert any("ceiling" in w for w in out["warnings"])


# ---- commit ---------------------------------------------------------------------


async def test_commit_refreshes_snapshot(fake):
    out = await run("""
      const before = deck.element("translucent_badge").fill.hex;
      emit(setFill("translucent_badge", "#00AA00"));
      const d = await commit();
      return {before, after: d.element("translucent_badge").fill.hex,
              global: deck.element("translucent_badge").fill.hex};
    """, dry_run=False)
    assert out["isError"] is False, out
    assert out["result"] == {"before": "#4472C4", "after": "#00AA00", "global": "#00AA00"}
    assert out["receipt"]["phases"] == 1


async def test_dry_run_stops_at_first_commit(fake):
    out = await run("""
      emit(setFill("translucent_badge", "#00AA00"));
      await commit();
      emit(setFill("stat_box", "#FF0000"));
      return "unreachable";
    """)
    assert fake.batches == []
    assert out["result"] is None
    assert out["preview"]["stopped_at_commit"] is True
    assert out["preview"]["request_count"] == 1
    assert "dry_run=false" in out["preview"]["note"]


# ---- warnings -------------------------------------------------------------------


async def test_font_change_warns_about_weight_drop(fake):
    out = await run("emit(styleText('weighted_heading', {fontFamily: 'Inter'}))")
    assert any("drops weight [800]" in w for w in out["warnings"]), out["warnings"]


async def test_style_runs_keeps_weight_so_no_warning(fake):
    out = await run("emit(styleRuns(deck.element('weighted_heading'), null, {fontFamily: 'Inter'}))",
                    include_requests=True)
    assert not any("drops weight" in w for w in out["warnings"])
    req = out["preview"]["requests"][0]["updateTextStyle"]
    assert req["style"]["weightedFontFamily"] == {"fontFamily": "Inter", "weight": 800}


async def test_size_increase_warns_likely_overflow(fake):
    out = await run("emit(styleText('stat_box', {sizePt: 40}))")
    assert any("likely overflow" in w and "stat_box" in w for w in out["warnings"]), out["warnings"]
    quiet = await run("emit(styleText('stat_box', {sizePt: 20}))")
    assert not any("overflow" in w for w in quiet["warnings"])


# ---- errors ---------------------------------------------------------------------


async def test_runtime_error_reports_line_and_column(fake):
    out = await run("const a = 1;\nconst b = null;\nb.boom;")
    assert out["isError"] is True
    err = out["error"]
    assert err["kind"] == "script"
    assert "TypeError" in err["message"]
    assert err["line"] == 3


async def test_syntax_error_reports_line(fake):
    out = await run("let ok = 1;\nlet = = 2;")
    assert out["error"]["kind"] == "syntax"
    assert out["error"]["line"] == 2


async def test_api_error_reports_failing_request(fake):
    fake.fail = slides_api.SlidesApiError(
        "Slides API error 400: Invalid requests[1].updateTextStyle: bad range", status=400)
    out = await run("emit(setFill('stat_box', '#000000')); emit(styleText('stat_box', {bold: true}))",
                    dry_run=False)
    assert out["error"]["kind"] == "api"
    assert out["error"]["request_index"] == 1
    assert "updateTextStyle" in out["error"]["request"]


async def test_no_timers_or_network(fake):
    out = await run("return [typeof setTimeout, typeof fetch, typeof require, typeof process]")
    assert out["result"] == ["undefined"] * 4


async def test_read_only_token_refused_on_apply_not_dry_run(fake, monkeypatch):
    monkeypatch.setattr(auth, "credentials_info", lambda: {
        "exists": True, "scopes": ["https://www.googleapis.com/auth/presentations.readonly"]})
    applied = await run("emit(setFill('stat_box', '#000000'))", dry_run=False)
    assert applied["isError"] is True
    assert applied["error"]["kind"] == "auth"
    assert fake.batches == []
    dry = await run("emit(setFill('stat_box', '#000000'))")
    assert dry["isError"] is False


async def test_thumbnails_attached_after_apply(fake):
    out = await run("emit(setFill('stat_box', '#000000'))", dry_run=False,
                    render_slides=["slide_cases"])
    assert isinstance(out, list)
    receipt = json.loads(out[0])
    assert receipt["receipt"]["applied_request_count"] == 1
    assert len(out) == 2


async def test_audit_line_written(fake, monkeypatch, tmp_path):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("SLIDES_MCP_AUDIT_LOG", str(log))
    await run("emit(setFill('stat_box', '#000000'))", dry_run=False)
    rec = json.loads(log.read_text().strip())
    assert rec["deck_id"] == DECK
    assert rec["request_count"] == 1
    assert rec["kinds"] == {"updateShapeProperties": 1}
    assert len(rec["script_hash"]) == 16


# ---- read model -------------------------------------------------------------------


async def test_script_sees_faithful_read_model(fake):
    out = await run("""
      const s = deck.slide("slide_cases");
      const g = deck.slides[2].elements.find(e => e.parentId);
      const e = id => s.element(id);
      return {
        transparent: e("transparent_box").fill,
        badge: e("translucent_badge").fill,
        rot: [e("rotated_label").rotation, e("rotated_label").in.w > 0],
        weight: e("weighted_heading").runs[0].weight,
        theme: [e("theme_text").runs[0].color, e("theme_text").runs[0].colorTheme,
                e("theme_text").runs[0].end],
        dark: [deck.slides[0].isDark, s.isDark],
        groupChildOnPage: g.x + g.w <= deck.pageSize.w,
        notes: s.notes.markdown,
      };
    """)
    r = out["result"]
    assert r["transparent"] == {"kind": "none"}
    assert r["badge"] == {"kind": "solid", "hex": "#4472C4", "alpha": 0.2, "theme": "ACCENT1"}
    assert r["rot"] == [90.0, True]
    assert r["weight"] == 800
    assert r["theme"][0].startswith("#") and r["theme"][1] == "ACCENT2"
    # "Theme coloured " (15) + emoji (2 UTF-16 units) + " text\n" (6)
    assert r["theme"][2] == 23
    assert r["dark"] == [True, False]
    assert r["groupChildOnPage"] is True
    assert r["notes"] == "# Intro\nSay **this** now.\n- point\n  - *sub*"


def test_raw_omits_default_valued_new_fields(fake):
    out = read_slides(deck_url=DECK, slides=["slide_cases"], detail="raw")
    els = {e["id"]: e for e in out["slides"][0]["elements"]}
    plain = els["transparent_box"]
    for key in ("fill", "outline", "autofit", "rotation_deg", "parent_id"):
        assert key not in plain
    assert "fill_hex" not in plain  # not painted, so no fill colour
    assert els["translucent_badge"]["fill"]["alpha"] == 0.2
    assert els["rotated_label"]["rotation_deg"] == 90.0
    assert els["weighted_heading"]["runs"][0]["weight"] == 800
    assert "weight" not in els["stat_box"]["runs"][0]  # 700 + bold is the default
    assert out["slides"][0]["background"]["hex"] == "#FAFAFA"


def test_read_notes_markdown_and_text_default(fake):
    text = read_slides(deck_url=DECK, slides=["slide_cases"], detail="summary")
    assert "**" not in text["slides"][0]["notes"]
    md = read_slides(deck_url=DECK, slides=["slide_cases"], detail="summary",
                     notes_format="markdown")
    assert md["slides"][0]["notes"].startswith("# Intro\nSay **this** now.")


# ---- speaker notes ---------------------------------------------------------------

NOTES_MD = "# Talk track\nOpen with **the number**, then *pause*.\n- first\n  - nested\n- second"


def test_write_notes_round_trip(fake):
    out = write_speaker_notes(deck_url=DECK, notes={"slide_blank": NOTES_MD})
    assert out["isError"] is False
    assert out["slides_written"] == ["slide_blank"]
    assert len(fake.batches) == 1
    kinds = [next(iter(r)) for r in fake.batches[0]]
    assert "deleteText" not in kinds
    assert kinds[0] == "insertText" and kinds[-1] == "createParagraphBullets"
    inserted = fake.batches[0][0]["insertText"]["text"]
    assert "*" not in inserted and "#" not in inserted
    back = read_slides(deck_url=DECK, slides=["slide_blank"], detail="summary",
                       notes_format="markdown")
    assert back["slides"][0]["notes"] == NOTES_MD


def test_replacing_non_empty_notes_needs_confirmation(fake):
    refused = write_speaker_notes(deck_url=DECK, notes={"slide_cases": "new"})
    assert refused["isError"] is True
    assert fake.batches == []
    ok = write_speaker_notes(deck_url=DECK, notes={"slide_cases": "new"}, confirm_destructive=True)
    assert ok["isError"] is False
    back = read_slides(deck_url=DECK, slides=["slide_cases"], notes_format="markdown")
    assert back["slides"][0]["notes"] == "new"


def test_append_notes_needs_no_confirmation(fake):
    out = write_speaker_notes(deck_url=DECK, notes={"slide_cases": "- extra"}, mode="append")
    assert out["isError"] is False
    back = read_slides(deck_url=DECK, slides=["slide_cases"], notes_format="markdown")
    assert back["slides"][0]["notes"].endswith("  - *sub*\n- extra")


async def test_set_notes_inside_script(fake):
    out = await run("""
      emit(setNotes("slide_blank", "Hello **there**"));
      emit(setNotes("slide_blank", "- and more", {mode: "append"}));
    """, dry_run=False)
    assert out["isError"] is False, out
    back = read_slides(deck_url=DECK, slides=["slide_blank"], notes_format="markdown")
    assert back["slides"][0]["notes"] == "Hello **there**\n- and more"


def test_clamp_limits_rejects_non_positive():
    with pytest.raises(ValueError):
        scripting.clamp_limits({"timeout_s": 0})
