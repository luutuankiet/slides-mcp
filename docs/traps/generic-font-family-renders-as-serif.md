---
title: A generic or unknown font family renders as a serif font
summary: updateTextStyle stores any fontFamily string verbatim, including CSS generic names like sans-serif, and Slides draws text with a family it does not know in a serif fallback with no error
verified: 2026-10-03
---

# A generic or unknown font family renders as a serif font

## Symptom

Text written through `updateTextStyle` (or created by converting SVG text)
shows up in a Times-like serif font, although the request asked for a sans
or monospace family. The batch returned HTTP 200 and no warning.

## Cause

Slides accepts any string as `fontFamily` and stores it as sent. Reading the
text back returns the same string, so the write looks correct:

```json
{"fontFamily": "sans-serif", "weightedFontFamily": {"fontFamily": "sans-serif", "weight": 400}}
```

Slides only knows real family names (Google Fonts plus a few aliases such as
`Helvetica`). CSS generic names (`sans-serif`, `serif`, `monospace`,
`system-ui`) are not families to it, and neither is a misspelled or
unavailable name. All of them render in the same serif fallback, `sans-serif`
and `monospace` included.

SVG authors and agents write `font-family="Arial, sans-serif"` or plain
`sans-serif` as a matter of habit, which is why this shows up when SVG text is
turned into text boxes.

## Fix

Never pass a CSS font list or a generic name through. Take the first family of
the list and map generic names to concrete families before sending:
`sans-serif` to `Arial`, `serif` to `Times New Roman`, `monospace` to
`Courier New`.

## How to check

Render the slide (`render_thumbnail`) and look at the glyphs; reading the text
style back proves nothing, because it echoes whatever was sent.
