# How lrclean was built

These documents were written during the build. They are kept unedited, so that they still say what was reviewed.

- `specs/` holds the designs: what the tool must do, the rules its code holds, and how it is released.
- `plans/` holds the implementation plans and phase reports, one file per piece of work.
- `outside-reads/` holds the prompts given to an independent reader for a second opinion on a design or a plan.

Four things to know when you read them:

- "The owner" in them is Peter.
- The command was `python -m lrdup` then. It is `python -m lrclean` now.
- Where they point to `CLAUDE.md`, the ledgers or `out/`, that material is not published.
- The rules in `CLAUDE.md` that are about the code and its data (H1 to H4, R1) are summarised in `AGENTS.md`.

For using the tool, read the [guide](../guide.md).
