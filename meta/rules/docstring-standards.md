---
id: docstring-standards
tier: convention
enforce: skill
deployed-to: .claude/skills/code-comments/SKILL.md
---

# DocString & comment standards

**Every public module, class, and function gets a docstring in the language's
standard style.**

- **Python:** Google style docstrings. `Args:`, `Returns:`, `Raises:` sections
  required for public functions (the python-stack rule binds the meta layer to
  the same standard).
- **TypeScript:** TSDoc (`/** */`). Use `@param`, `@returns`, `@throws`.
  Compatible with typedoc and eslint-plugin-tsdoc.
- **JavaScript:** JSDoc with type annotations (`@param {string} name`). Gives
  editors type inference without TS.
- **All comments are written in Korean** - inline comments, block comments,
  docstring descriptions, and file headers (see file-header-comments).
  Docstring tags and structure (`Args:`, `@param`, etc.) follow each standard;
  code identifiers stay in English.
- Private helpers and trivial one-liners: skip the docstring, keep the code
  self-explanatory.
- Don't restate the obvious ("gets the user" on `getUser`). Document the *why*
  and the non-obvious constraints (units, side effects, error conditions).
