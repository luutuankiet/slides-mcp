# Documentation

Every page here is written for a maintainer six months from now who opened
exactly this file from a search result and has nothing else loaded.

This index is generated. Run `scripts/gen-docs-index.sh` after adding or
renaming a page; `--check` fails if it is stale.

<!-- BEGIN GENERATED INDEX -- edit the pages, not this block -->

## Where things live

One page per area of the system. Read before going looking for where
something is implemented.

| page | covers | verified |
|---|---|---|
| [Auth and entry points](architecture/auth-and-entry-points.md) | where token.json is read from, how OAuth refresh and scopes work, what slides-mcp and slides-mcp-auth do on startup, how HTTP mode (serve-http) picks up each caller's Google token | 2026-10-03 |
| [Deck scripts (run_deck_script)](architecture/deck-scripts.md) | how an agent's JavaScript runs against a deck, the read model the script sees, the worker process and its timeouts, commit phases, dry run, applying a dry run by plan_id, what is refused before any API call, where errors and warnings come from, the thumbnails a real apply returns | 2026-10-03 |
| [Packaging, release and the shipped skill](architecture/packaging-and-release.md) | how a version gets to PyPI and GitHub Releases, what the release workflow checks, where the agent skill that ships with the package lives | 2026-10-01 |
| [Read path (outline, read, search, thumbnail)](architecture/read-path.md) | how a deck URL becomes the slide dicts an agent sees, where detail modes, selectors, archetypes, titles and speaker notes come from | 2026-09-28 |
| [Write wedge (exec_batch_update, add_section_footers, write_speaker_notes, place_image)](architecture/write-wedge.md) | where writes to a deck happen, the destructive-request guard, dry run, the audit line, how post_state and affected_slide_ids are built, the thumbnail receipt each write returns, how section footers are placed, how Markdown notes become requests, how place_image rasterises, hosts and places an image | 2026-10-03 |

## Traps

Failure modes that produce no error message, indexed by the symptom you
would observe. Read before debugging behaviour that is wrong but not
crashing.

| symptom | page | area | verified |
|---|---|---|---|
|  | [image-replace-matching-nothing-returns-success](traps/image-replace-matching-nothing-returns-success.md) |  | 2026-10-03 |
|  | [svg-text-vanishes-in-slim-image](traps/svg-text-vanishes-in-slim-image.md) |  | 2026-10-03 |

## Reference

Simply true, and expensive to re-derive.

| page | summary | verified |
|---|---|---|
| [Cutting a release](reference/cutting-a-release.md) | the ordered steps that get a version onto GitHub Releases and PyPI without the tag-triggered workflow failing | 2026-09-28 |
| [Google's Slides MCP server compared with slides-mcp](reference/google-slides-mcp.md) | what Google's hosted Slides MCP server (Developer Preview) offers, measured token cost and write guards side by side with slides-mcp, and how to repeat the measurements | 2026-09-28 |

## Guides

Long-form pages that belong to no single area.

| page | summary | verified |
|---|---|---|
| [Deploying the shared server to Cloud Run](deploying-to-cloud-run.md) | the Google Cloud setup a deployer does once before running slides-mcp as a shared remote server (Firestore database, service account, client secret, settings, Google OAuth client), how a release tag is built and deployed with gcloud run deploy --source, how to check it is up, and the shared thumbnail quota | 2026-10-03 |

## Decisions

Why the repo is the way it is. A merged decision is immutable -- supersede
it with a new one rather than editing it.

- [Cut the slide-authoring surface and make v2 a reader](adr/0001-read-only-refactor.md)
- [Bring writes back as a raw passthrough for legwork, with post-state in the reply](adr/0002-legwork-write-wedge.md)
- [Keep repository knowledge in docs/, loaded on demand](adr/0003-agent-docs-layout.md)
- [Run agent scripts in embedded V8, in a worker process per call](adr/0004-deck-scripts-in-embedded-v8.md)
- [Deliver the agent skill through an MCP tool](adr/0005-skill-delivery-through-mcp-tool.md)
- [Keep self-hosting slides-mcp rather than adopt Google's Slides MCP server](adr/0006-keep-self-hosting-over-google-slides-mcp.md)
- [The caller's Google credentials come from request context, not a parameter](adr/0007-caller-credentials-from-request-context.md)
- [Point agents at the skill from the raw-requests tool, not from every tool](adr/0008-tools-tell-agents-to-load-the-skill.md)
- [Image placement is render-and-place legwork, not authorship](adr/0009-image-placement-is-render-and-place-legwork.md)

<!-- END GENERATED INDEX -->
