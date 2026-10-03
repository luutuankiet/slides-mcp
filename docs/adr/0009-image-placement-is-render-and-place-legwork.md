# Image placement is render-and-place legwork, not authorship

Agents want to put a diagram on a slide. People sketch one elsewhere,
screenshot it and paste it in by hand. The Slides API only takes a PNG, JPEG
or GIF that Google fetches once from a public URL; it has no SVG support, no
raw-bytes upload and no HTML or iframe embed. So an agent holding SVG has no
way to get it onto a slide unless the server converts it and hosts it.

`0001` calls creative-layout tools "re-proposals of what this record
rejected", and `0002` puts pixel-accurate placement out of scope. A tool that
places images looks like both. It is not, because of who owns the design. The
server may **render and place** content the agent supplies, at a position the
agent supplies or the user's own deck defines: the box of a placeholder shape
the user drew, or a box copied from a reference slide. The server never ships
layouts, templates, themes or slide archetypes, and never decides where
anything goes. That is the part `0001` removed, and it stays removed.

This record narrows how `0001` and `0002` apply to images. It does not
supersede them.

## Considered options

- **Supersede `0001` and `0002` outright.** Rejected: it reopens authorship in
  general, and the next creative-layout proposal would cite it as precedent.
- **A separate package for rich content.** Rejected: a second install and a
  second auth setup for what is one tool.

## Consequences

- HTML and React rendering stay out, as do interactive embeds: Slides cannot
  show them, so only a static image would reach the deck, at the cost of a
  browser dependency.
- Hosting needs a URL Google can reach, so placing rendered SVG works only on
  the hosted server. The server keeps the file only until the write returns.
- Placement still goes through the one write path (`writes.apply_batch`) with
  the same destructive guard. Replacing a placeholder shape counts as a
  destructive kind.
- A future tool that picks a layout for the user, rather than reading one from
  the deck, is still a re-proposal of what `0001` rejected.
