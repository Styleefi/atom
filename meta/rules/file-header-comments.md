---
id: file-header-comments
tier: convention
enforce: skill
deployed-to: .claude/skills/code-comments/SKILL.md
---

# File header comments in Korean

**First line of every new source file: a one-line Korean comment stating its
role.** When creating a new file:

- TypeScript/JavaScript: `// 사용자 인증 상태를 관리하는 Context Provider`
- Python: `# KIS API 호출을 비동기로 래핑하는 클라이언트`
- SQL: `-- 일별 집계 결과를 저장하는 머티리얼라이즈드 뷰`
- Place it directly under required directives (`'use client'`, `'use server'`,
  shebang).
- Skip config files (`*.config.ts`, `package.json`, etc.).

Why: agents read files selectively, not whole codebases. A one-line Korean
header gives instant context so the next session (human or agent) can navigate
without re-reading the entire file.
