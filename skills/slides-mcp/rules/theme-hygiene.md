# Theme hygiene

## The living-theme model

Themes are NOT gatekeepers — they're a living document. When a deck uses a color or font that isn't in the active theme, two paths:

1. **Accept it** — `promote_to_theme` adds the drift value as a named role. Theme file updated.
2. **Fix it** — `patch_slide` the offending slot with the nearest-role theme value.

The theme does NOT auto-snap drifts. You decide per case.

## Audit a deck

```
audit(deck_url, kind="colors")  # theme/sub_theme default to "example"/"primary"
```

Returns a drift report:

```yaml
colors:
  - hex: "#ff6b35"
    count: 14
    nearest_role: "accent_orange"
    example_locations: [...]
  - hex: "#0000ff"
    count: 3
    nearest_role: null
    example_locations: [...]
fonts:
  - family: "Consolas"
    size_pt: 10
    count: 7
    nearest_role: "body_font"
    example_locations: [...]
```

The `nearest_role` suggestion uses perceptual distance — it's a hint, not a decision.

## Promote a drift to the theme

```
promote_to_theme(
    theme="joon",
    sub_theme="default",
    role_name="accent_coral",
    kind="color",
    value="#ff6b35",
)
```

Writes to the first writable theme file found in the search path, typically `~/.config/slides-mcp/themes/joon.yaml`. It NEVER writes to the bundled `example.yaml` (that's generic, ships with the package).

## Theme resolution order

1. `$SLIDES_MCP_THEMES_DIR` (if set)
2. `$XDG_CONFIG_HOME/slides-mcp/themes/` (default `~/.config/slides-mcp/themes/`)
3. Project-local `./themes/` (if present)
4. Bundled `slides_mcp/themes/` (generic `example.yaml`; never overwritten)

First hit wins. Use `list_registry(kind="themes")` to see what's resolvable now.

## Privacy boundary

Your real brand theme NEVER goes in the repo. The bundled `themes/example.yaml` is intentionally generic. When you run `promote_to_theme`, the drift lands in your user config dir — not in any committed file.

## When to fix vs. promote

| Signal | Action |
|--------|--------|
| Drift is pollution (e.g., `#0000FF` hard-blue from Windows-export) | `patch_slide` — fix the slot to use `palette.role` |
| Drift is a real brand value not yet in theme | `promote_to_theme` — codify it |
| Drift is a one-off client color | `patch_slide` — fix it; don't pollute the theme |
| Font drift is `Consolas` from code paste | `patch_slide` — fix to `body_font` |
| Font drift is intentional (co-brand) | `promote_to_theme` — add `google_cobrand_font` role |

## Typical loop

```
1. audit(deck_url, kind="colors") → drift report
2. For each drift:
   - Check example_locations — is this on a slot meant to be on-brand?
   - Yes + value is the real brand    → promote_to_theme
   - Yes + value is pollution         → patch_slide to nearest_role
   - No (decorative, client-specific) → patch_slide; don't pollute theme
3. Re-audit → confirm drift count drops
```
