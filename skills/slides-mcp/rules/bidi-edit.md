# Bidi editing — see and move

The killer feature: you can SEE the rendered slide natively AND MOVE shapes by editing coordinates, in the same MCP session.

## The loop

```
1. get_slide(deck_url, slide_id, include_elements=True)
   → DSL with elements: [{id, at:[x,y,w,h]}]
   → also auto-includes _object_ids for text slots

2. render_thumbnail(deck_url, slide_id, size="MEDIUM")
   → MCP ImageContent (PNG bytes) — agent sees it natively

3. Decide: "move the JOON block 0.5in up and left"

4. Modify elements[N].at in the DSL

5. patch_slide(deck_url, slide_id, new_dsl_yaml, verify="auto")
   → emits updatePageElementTransform with translateX/Y in EMU, applyMode: RELATIVE
   → auto-renders a fresh thumbnail (verify=auto fires on any geometry diff)

6. Consume the new thumbnail — was the move correct?

7. If no: goto 4. If yes: done.
```

## Why RELATIVE mode matters

The Slides API `updatePageElementTransform` has two modes:

- **ABSOLUTE** — replaces the entire transform. You MUST specify scale / rotation or they reset to 1 / 0.
- **RELATIVE** — composed with the existing transform. If the shape was scaled 0.72× and rotated 15°, those stick; only the translation adds.

`patch_slide` always emits RELATIVE with `scaleX: 1, scaleY: 1, translateX: Δ, translateY: Δ`. This is correct for translation-only writes. If you need to resize or rotate, you have to use `exec_batch_update` with an ABSOLUTE transform (and handle scale / rotation yourself) — resize/rotate writes are deferred to Phase 2.

## EMU units

The Slides API uses EMU (English Metric Units). `patch_slide` handles the conversion for you:

- 914,400 EMU = 1 inch
- 12,700 EMU = 1 pt

You edit `at` in inches in the DSL; the writer converts to EMU in the request.

## What's NOT a bidi edit (yet)

- Resize (width / height change in `elements[].at`) → warning, no write
- Rotate / shear → no DSL channel; use `exec_batch_update` with full transform
- Add a new shape → `create_shape`, not DSL add-element
- Remove a shape → `exec_batch_update` with `deleteObject` + `confirm_destructive=True`

## Render tiers

```
render_thumbnail(deck_url, slide_id, size="SMALL")   # fastest, low-res
render_thumbnail(deck_url, slide_id, size="MEDIUM")  # default, balanced
render_thumbnail(deck_url, slide_id, size="LARGE")   # high-res, slowest
```

MEDIUM is fine for most visual verification. Go LARGE only when you need to read small body text in the image.

## URL-only sibling

```
render_thumbnail_url(deck_url, slide_id, size="MEDIUM")
```

Returns just the short-lived contentUrl (no bytes). Use this for dashboard embeds or pipelines where you don't need the image in-context. Agents should use `render_thumbnail` (native bytes), not this URL form.

## Token budget note

Thumbnails are expensive — ~640 tok on SMALL up to ~2,765 tok on LARGE. The auto-rendering gate in `patch_slide` only fires on geometry changes for exactly this reason. For text-only edits, no thumbnail is rendered by default.

If you're in a many-turn session and the token budget is tight, pass `verify="never"` to suppress the auto-thumbnail, then render on-demand later with `render_thumbnail` when you actually need to see the result.
