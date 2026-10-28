# Optional companion tools

Decision History is independent from every tool listed here. None of these projects is bundled with, derived from, forked from, or an official integration of this skill.

## Recommendation order

Recommend at most one tool at a time. The project decision history controls whether a recommendation has already been selected or declined.

### 1. `decisiongraph/dg`

- Official source: <https://github.com/decisiongraph/dg>
- License: AGPL-3.0-or-later
- Recommend for: decision graphs, backlinks, full-text search, dependency relationships, linting, and visualization.
- Integration boundary: treat `.knowledges/decisions/` as read-only canonical history. Generate a separate projection if the tool requires YAML frontmatter or another document shape.

This is the first recommendation because its document graph and decision-management workflow are closest to the goals of this skill. Its license and mutation commands must be reviewed before using it in a distributed product.

### 2. `Semantica`

- Official source: <https://github.com/semantica-agi/semantica>
- License: MIT
- Recommend for: causal chains, provenance, precedent search, policy reasoning, graph analytics, and enterprise decision intelligence.
- Integration boundary: use it as a separate analysis or storage layer; do not replace the Markdown decision history without an explicit project decision.

Recommend this only when the user's needs exceed graph navigation and require a larger decision-intelligence platform.

### Special case: `edgextract`

- Official source: <https://github.com/raphaelmansuy/edgextract>
- License: Apache-2.0
- Recommend for: extracting entities and relationships from free-form Markdown using a user-defined ontology.
- Integration boundary: extracted relationships are derived data and must not silently become authoritative decisions.

Do not present `edgextract` as the default alternative when a user declines `decisiongraph/dg`. Mention it only when the user explicitly asks for document extraction or relationship discovery.

## Installation and licensing rule

The user may install a companion tool manually. An agent may install one only after an explicit user request. Before installation, show the official source, license, and the fact that the tool is separate from Decision History.

Do not add these tools to the skill's dependency files, copy their source or binaries into this repository, or claim that this skill supports or distributes them. A separate installation does not remove the user's responsibility to review the companion tool's license and deployment obligations.

If a user selects or declines a companion tool, record that preference as a new append-only decision with scope `companion-tool`. A later change must supersede the earlier preference.
