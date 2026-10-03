---
title: SVG text vanishes when rasterised in the slim container
summary: resvg-py drops every text element without an error when no font matches, which is the default in python:3.12-slim-bookworm, and still the case with DejaVu installed
verified: 2026-10-03
---

# SVG text vanishes when rasterised in the slim container

## Symptom

A diagram placed from SVG shows its boxes and arrows but no labels. The
rasteriser returned a valid PNG and raised nothing.

## Cause

`python:3.12-slim-bookworm` ships no fonts at all (`/usr/share/fonts` does not
exist). resvg loads fonts through its own font database, not fontconfig, and
resolves the generic families to fixed names: `sans-serif` to `Arial`,
`serif` to `Times New Roman`, `monospace` to `Courier New`. When nothing
matches, the text element is skipped. The only trace is a warning that
`resvg_py.svg_to_bytes` prints to stdout, and only when called with
`log_information=True`:

```text
Warning (in usvg::text:143): No match for 'sans-serif' font-family.
```

Installing `fonts-dejavu-core` alone does not fix it: the DejaVu files are
loaded, but no generic family resolves to them, so the output is byte for byte
the same as with no fonts.

## Fix

Install `fonts-liberation2` in the runtime image. With it, the generic families
resolve and every label renders. A named family the image lacks (for example
`Arial` or `Inter`) falls back to Liberation Serif rather than disappearing.
Characters outside Liberation's coverage (CJK, for example) render as empty
boxes.

## How to check

Render an SVG with a `<text>` element in the image and compare the PNG with
one rendered from the same SVG with the text removed. Equal byte counts mean
the text was dropped.
