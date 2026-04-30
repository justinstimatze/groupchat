# References & Influences

## Architecture

### Meme retrieval
- **MemeCMD** — Nahtreom et al. (2025). *An Automatically Generated Chinese Multi-turn Dialogue Dataset with Contextually Retrieved Memes*. arXiv:2507.00891.
  Directly influenced: adaptive cooldown threshold (θ(t) = e^{-λt}), 4-dimensional meme scoring (scenario + emotion + motivation + penalization), the idea of annotating memes with scenario appropriateness and implicit emotion.

### Humor theory
- **HUMORCHAIN** — Zhang & Luo et al. (2025). *Theory-Guided Multi-Stage Reasoning for Interpretable Multimodal Humor Generation*. arXiv:2511.21732.
  Directly influenced: `mechanism` field taxonomy (incongruity, benign_violation, superiority, relief) and the idea of explicit mechanism-based reasoning before selecting a meme.

- **THInC** — De Marez, Winters & Rigouts Terryn (2024). *THInC: A Theory-Driven Framework for Computational Humor Detection*. arXiv:2409.01232. CREAI workshop at ECAI 2024.
  Confirmed: no single humor theory dominates; incongruity explains the most cases, benign violation and superiority explain complementary tails. F1=0.85 with theory-ensemble approach.

- **Tanaka et al.** (2022). *Learning to Evaluate Humor in Memes Based on the Incongruity Theory*. CAI 2022 Workshop at COLING 2022.
  CLIP embedding subtraction (image - caption) as a proxy for image-text incongruity; confirmed incongruity gap is a measurable predictor of perceived humor.

### Contextual meme understanding
- **MemeReaCon** — (2025). *Probing Contextual Meme Understanding in Large Vision-Language Models*. arXiv:2505.17433. EMNLP 2025.
  Key finding: LVLMs either miss conversational context OR over-focus on visual details and miss communicative intent. Confirmed that explicit textual annotations (`deploy_when`, `too_much_if`) are load-bearing, not optional.

### Information retrieval
- **IR literature consensus**: For candidate sets ≤ ~100 items, exhaustive cross-encoder scoring outperforms two-stage retrieval (bi-encoder + reranker). Motivated removing `find_meme` in favor of model-native selection over the full roster.

## Humor theory foundations

- **Benign Violation Theory** — McGraw & Warren (2010). *Benign Violations: Making Immoral Behavior Funny*. Psychological Science.
  Humor occurs when something simultaneously (1) violates how the world should be, (2) seems benign, and (3) both interpretations are held at once. Maps to `IB` (incongruity + benign_violation) memes in the roster.

- **Semantic Script Theory of Humor (SSTH)** — Raskin (1985). *Semantic Mechanisms of Humor*. Reidel.
  Humor from opposed scripts sharing surface text. Foundation for incongruity-resolution models.

- **General Theory of Verbal Humor (GTVH)** — Attardo & Raskin (1991). *Script theory revis(it)ed*. Humor: International Journal of Humor Research.
  Extended SSTH with 6 knowledge resources including logical mechanism and target. Relevant to `irony_modes` and `mechanism` fields.

### Sarcasm/irony in LLMs
- **Towards Evaluating LLMs on Sarcasm Understanding** (2024). arXiv:2408.11319.
  Key finding: few-shot IO prompting outperforms chain-of-thought for irony/sarcasm detection because sarcasm is holistic/intuitive, not step-by-step. Motivated using worked examples in CLAUDE.md rather than explicit reasoning chains.

## Tools & infrastructure

- **chafa** — https://hpjansson.org/chafa/ — terminal image/GIF renderer. `--animate off` renders static frame; `--size=WxH` controls dimensions.
- **FastMCP** — https://github.com/jlowin/fastmcp — MCP server framework used as transport.
- **Tenor** — https://tenor.com — GIF source for meme rendering.
- **MCP (Model Context Protocol)** — https://modelcontextprotocol.io — Claude Code tool extension protocol.

## Datasets considered

- **HatefulMemes** (Facebook AI, 2020) — multimodal hate detection benchmark. Not directly used but representative of the meme-understanding research landscape.
- **MemeCap** — meme captioning dataset. Not used; our memes have hand-curated annotations.

- **Ward (2026)** — *Internet Meme Marketing over the Fad Cycle*. Journal of Interactive Marketing. doi:10.1177/10949968251320612.
  Models meme attention dynamics as a fad cycle using Reddit data + ML classification. Cited in §6 (Freshness death spiral) as the base model; the `retro` vitality state extends Ward's framework to include the revival lobe (not empirically validated by this project).

## Prior art gap

No published work optimizes meme selection from a *fixed curated library* for *funniness in a 1:1 conversational context*. Every published system optimizes relevance, harm detection, or generation quality. The `deploy_when` / `too_much_if` / `mechanism` annotation schema in this project is, as far as we can tell, the current state of the art for this specific problem.
