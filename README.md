# atom

Starting from 'A': the root and architectural skeleton for all future projects — orchestrating personal Claude agents, automated harnesses, and modular skillsets.

**Atom** is a meta-repository that acts as the Single Source of Truth (SSOT) for a personal ecosystem of Claude-driven projects. Every new project starts as a clone of atom, inherits its rules, agent configuration, and verification harnesses, and receives later revisions through `git pull upstream main`.

## Goals

Each goal exists because of a recurring, real pain — not an aspiration:

| # | Goal | Pain it removes |
|---|------|-----------------|
| 1 | **Configuration SSOT** — rules, agent roles, skills, hooks, and MCP config are versioned here, once | Corrections trapped in per-project memory; every machine and project configured differently |
| 2 | **Project Bootstrap** — a new project starts fully equipped by cloning this skeleton | Rebuilding structure, settings, and tooling from scratch for every project |
| 3 | **Enforced Quality Loop** — rules are promoted from advice to deterministic checks (harnesses, hooks, CI) | "Done" declared without verification; unrequested changes slipping through |
| 4 | **Session Continuity** — decisions survive across sessions: progress context in issue comments, durable rationale in curated docs | Context and rationale lost between sessions |
| 5 | **Agent Pipelines** — standard workflows chaining agent roles (e.g. implement → review → test → docs) | Manual re-orchestration of proven multi-agent flows |

Open questions (which agent roles, which pipelines, rule migration) are tracked in GitHub Issues, not in this file.

## Repository layout

```
├── CLAUDE.md          # guidance for Claude Code in this repo
├── meta/              # the meta layer every child project inherits
│   ├── rules/         #   rule SSOT (frontmatter-declared deployment)
│   ├── templates/     #   CLAUDE.template.md and other scaffolds
│   ├── harness/       #   verification harnesses (one subpackage each)
│   └── infra/         #   on-demand test infrastructure (docker compose stacks)
├── docs/              # curated design docs
└── (child projects add their product layer: services/, infra/, libs/ …)
```

## Getting started — creating a child project

GitHub cannot fork a repository into the same account, and "Use this template" severs the upstream link (`git pull upstream main` stops working). Use the clone recipe instead:

```bash
# 1. create an empty repository on GitHub (e.g. myproject), then:
git clone https://github.com/Styleefi/atom.git myproject
cd myproject
git remote rename origin upstream
git remote add origin https://github.com/Styleefi/myproject.git
git push -u origin main

# 2. adapt the skeleton
#    - install uv once per machine (https://docs.astral.sh/uv/) — runs the meta harnesses
#    - copy meta/templates/CLAUDE.template.md over the root CLAUDE.md and fill it in
#    - clear atom's session records under docs/ and start your own
#    - rewrite this README for your project

# 3. later, pull rule/harness revisions from atom
git pull upstream main
```

Merge conflicts on upstream pulls are expected for the files you replaced: keep **yours** for `CLAUDE.md`, `README.md`, and `docs/`; take **upstream's** for `meta/`, `.claude/`, and `.github/`.

## License

[MIT](LICENSE)
