---
title: A placeholder image replace that matches nothing returns success
summary: replaceAllShapesWithImage scoped to a slide reports success with an empty reply when the marker is missing, mistyped, or sits on the slide's layout or master
verified: 2026-10-03
---

# A placeholder image replace that matches nothing returns success

## Symptom

A `replaceAllShapesWithImage` request comes back HTTP 200 and the deck is
unchanged: the marker text is still in its box, or no box shows anything new.

## Cause

Slides answers a replace that matched no shape with an empty reply object, not
an error and not `occurrencesChanged: 0`:

```json
{"replaceAllShapesWithImage": {}}
```

This happens when the marker text is mistyped, when it is on another slide than
the one named in `pageObjectIds`, and when the marker sits in a shape on the
slide's layout or master. Layout and master shapes are on other pages, so a
request scoped to the slide never reaches them, even though they are drawn on
the slide. The API also refuses to put text into a layout or master
placeholder (`The operation is not allowed on the placeholder shape on a layout
or master.`), so a marker can only live in a layout or master shape that is not
a placeholder.

Without `pageObjectIds` the same request does reach the layout or master
shape, and replaces it there, which changes every slide that uses that layout.

The empty reply also hides fetch failures in one case: Slides checks for a
matching shape before fetching the image, so a bad URL sent with a marker that
matches nothing returns the same empty success instead of the fetch error.

## Fix

Treat a reply with no `occurrencesChanged` (or zero) as a failure and tell the
caller the marker was not found on that slide. Put marker boxes on the slide
itself, never on its layout or master.

`place_image` does both: it looks for the marker in the slide's own shapes
before uploading anything, and treats a reply without `occurrencesChanged`
as error kind `marker` (`_place_image` in `src/slides_mcp/server.py`). It
never sends an unscoped replace.

## How to check

Send the request and read `replies[i].replaceAllShapesWithImage.occurrencesChanged`.
Absent means nothing was replaced.
