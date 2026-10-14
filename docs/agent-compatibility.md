# Agent compatibility

Agents differ in three independent ways, and they do not line up:

1. **Skill discovery** — whether the agent scans a skills directory and reads `SKILL.md`.
2. **Local process execution** — whether it can run Python and Java itself.
3. **File access** — whether it can read this directory at all.

A package cannot assume any of them.

| Agent | Skill discovery | Runs local scripts | Status |
|---|---|---|---|
| **DeepSeek Harness** | yes — `<projectRoot>\.dsh\skills\<name>\SKILL.md` (rank 1) or `.agents\skills\...` (rank 2); a bare root `SKILL.md` is **not** scanned | yes | **Verified** — this is the environment the skill was developed and tested in |
| Any agent reading plain files | manual — pass `SKILL.md` as context | unknown | **Documentation only** |
| Agents without process execution | manual | no | **Documentation only** — analysis only, see below |

**Only DeepSeek Harness is verified.** Everything else is documentation, and this table says so
deliberately: claiming untested compatibility is worse than admitting the gap.

## Entry point format

`SKILL.md` uses the widely used YAML-frontmatter shape:

```yaml
---
name: logisim-circuit
description: <one paragraph: what it does, when to use it>
---
```

* `name` must match the directory name for loaders that check it.
* `description` should state **both** what the skill does and **when** to use it — many loaders route
  on it.
* **A UTF-8 BOM breaks the frontmatter**: the first line becomes `\ufeff---`. Save without a BOM.
* Some loaders require the file to be exactly `SKILL.md`, others accept `skill.md`. Ship `SKILL.md`.

## Adapters

If an agent needs a different layout, add it under `adapters/<agent>/` rather than changing the core
files, and record it in this table. None are included yet, because none have been tested.

## Agents that cannot execute local processes

The entire knowledge base is plain text and JSON, so **analysis works without running anything**:

* read `references/logisim-format.md` for the file format;
* read `references/component-catalog.json` for measured component geometry and attributes;
* read `references/evidence-log.md` for what was measured and how.

**Generation and verification do not work this way.** They need Python and Java. With such an agent,
the agent produces the spec and the XML, and **you** run:

```bash
python scripts/py/circmodel.py build <spec> <out.circ>
python scripts/py/verify_model.py <out.circ> --spec <spec>
```

Do not describe the skill as "fully automatic" on an agent that cannot run these commands.
