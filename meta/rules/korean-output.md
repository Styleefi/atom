---
id: korean-output
tier: convention
enforce: claude-md
deployed-to: CLAUDE.md
---

# Korean output

**End Korean sentences with a period, not a colon.** When the owner writes in
Korean, your output is also Korean:

- Don't end sentences with `:` even if the next line is a list or example.
- LLMs trained on English docs leak the colon habit into Korean. Catch it.
- The test: every Korean sentence terminator should be `.`, `?`, or `!` - not `:`.
- Colons are fine inside code, key-value pairs, or labels. Not as sentence enders.
