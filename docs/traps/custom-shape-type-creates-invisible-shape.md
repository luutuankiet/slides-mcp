---
title: createShape with shapeType CUSTOM succeeds and draws nothing
summary: Slides accepts CUSTOM in createShape, stores a shape with that type and the default fill and outline, and renders nothing, so a request that should fail leaves an invisible element
verified: 2026-10-03
---

# createShape with shapeType CUSTOM succeeds and draws nothing

## Symptom

A `createShape` request with `"shapeType": "CUSTOM"` returns HTTP 200 with the
usual `createShape.objectId` reply. The slide shows nothing where the shape
should be, yet the element is there: reading the page back lists it.

## Cause

`CUSTOM` is the type Slides reports for shapes with free-form geometry, for
example shapes imported from PowerPoint. The API has no way to send that
geometry, but it does not refuse the type either. Reading the element back
gives a shape with no outline of its own:

```json
{"shapeType": "CUSTOM",
 "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"themeColor": "LIGHT2"}}},
                     "outline": {"outlineFill": {"solidFill": {"color": {"themeColor": "DARK2"}}}}}}
```

The theme fill and outline are set, but with no geometry there is nothing to
fill or stroke. Only `TYPE_UNSPECIFIED` is rejected
(`The shapeType is unspecified`).

## Fix

Never send `CUSTOM` in `createShape`. Anything that turns free-form drawing
(SVG paths, polygons) into requests must refuse it before the write instead of
mapping it to `CUSTOM`.

## How to check

Render the slide (`render_thumbnail`): the element's box is empty. The
post-state summary still lists the element, so it does not reveal the problem.
