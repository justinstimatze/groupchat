#!/usr/bin/env python3
"""
Meme selection benchmark.

Three modes:
  python3 run_eval.py baseline     — score the old keyword approach (find_meme)
  python3 run_eval.py model        — score model-native selection via Anthropic API
  python3 run_eval.py anti         — score anti-drop cases (should not drop)

Requires ANTHROPIC_API_KEY for model mode.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

EVAL_FILE = Path(__file__).parent / "evals.json"
DB_FILE = Path(__file__).parent.parent / "memes.json"
CACHE_DIR = Path.home() / ".cache" / "groupchat" / "eval"


def load_evals():
    with open(EVAL_FILE) as f:
        return json.load(f)


def load_db():
    with open(DB_FILE) as f:
        return json.load(f)


def _cache_key(model, prompt):
    h = hashlib.sha256(f"{model}|{prompt}".encode()).hexdigest()[:16]
    return h


def _cache_get(model, prompt):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = CACHE_DIR / f"{_cache_key(model, prompt)}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())["pick"]
        except (json.JSONDecodeError, KeyError):
            p.unlink(missing_ok=True)
    return None


def _cache_set(model, prompt, pick):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = CACHE_DIR / f"{_cache_key(model, prompt)}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"model": model, "pick": pick}))
    tmp.rename(p)


def _api_call(client, model, prompt, max_tokens=20):
    """Call API with disk cache — temperature=0 results are deterministic."""
    cached = _cache_get(model, prompt)
    if cached is not None:
        return cached
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    if not msg.content or not hasattr(msg.content[0], "text"):
        raise ValueError(f"unexpected API response: {msg.content!r}")
    pick = msg.content[0].text.strip().split("\n")[0].strip()
    _cache_set(model, prompt, pick)
    return pick


# ---------------------------------------------------------------------------
# Baseline: keyword search (the old find_meme approach)
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "is", "are", "was", "were",
        "be", "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "could", "should", "may", "might", "shall",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "it", "its", "this", "that", "these", "those", "they", "them",
        "not", "no", "so", "if", "when", "where", "which", "who", "what",
        "how", "than", "then", "as", "up", "out", "about", "into", "just",
        "more", "also", "very", "too", "can", "i", "you", "we", "he", "she",
        "one", "two", "all", "get", "got",
    }
)


def tokenize(text):
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOPWORDS and len(w) > 2}


def meme_corpus(m):
    return tokenize(
        m["deploy_when"] + " " + " ".join(m.get("situations", [])) + " " + m.get("affect", "")
    )


def keyword_top3(situation, memes):
    qt = tokenize(situation)
    if not qt:
        return []
    scored = sorted(memes, key=lambda m: len(qt & meme_corpus(m)), reverse=True)
    return [m["name"] for m in scored if len(qt & meme_corpus(m)) > 0][:3]


def run_baseline():
    memes = load_db()
    evals = [e for e in load_evals() if e.get("should_drop") and e.get("gold")]
    hits1 = hits3 = 0
    failures = []
    for e in evals:
        top3 = keyword_top3(e["situation"], memes)
        if top3 and top3[0] == e["gold"]:
            hits1 += 1
        if e["gold"] in top3:
            hits3 += 1
        else:
            failures.append({"id": e["id"], "gold": e["gold"], "got": top3, "situation": e["situation"][:60]})

    n = len(evals)
    if n == 0:
        print("Baseline keyword search — no drop cases found")
        return
    print(f"Baseline keyword search — {n} drop cases")
    print(f"  P@1: {hits1}/{n} = {hits1/n:.0%}")
    print(f"  P@3: {hits3}/{n} = {hits3/n:.0%}")
    if failures:
        print(f"\n  Misses ({len(failures)}):")
        for f in failures:
            print(f"    [{f['id']}] want={f['gold']:30s} got={f['got']}")
            print(f"         '{f['situation']}'")


# ---------------------------------------------------------------------------
# Model-native selection via Anthropic API
# ---------------------------------------------------------------------------

import datetime as _dt

ROSTER_PROMPT = """You are a meme selection assistant. Given a situation description, pick the
most appropriate meme from the roster below. Reply with ONLY the meme name, nothing else.

CRITICAL RULES:
- The meme must be a PRECISE template match, not a loose resemblance.
- Routine task requests, basic questions, interpersonal tension, and high-stakes moments are always NONE.
- When uncertain, reply NONE — an approximate fit is worse than no meme.
- Dated memes ([dated:YYYY]) require a 3σ fit — the template must be unmistakably on-point.

NONE examples (do NOT pick a meme for these):
  "can you fix this bug" → NONE  (routine task, no situational charge)
  "performance seems slow" → NONE  (vague observation, no ironic template)
  "need to ship by end of day" → NONE  (high stakes, wrong moment)
  "colleague is upset about the decision" → NONE  (interpersonal tension)

Roster (name | td=text_dependent | mech | plat | key — unique template signature):
{roster}
{context_block}Situation: {situation}"""


_PLAT_ABBR = {"reddit": "rd", "twitter": "tw", "tiktok": "tt", "tumblr": "tm", "youtube": "yt", "discord": "dc"}


def build_roster(memes):
    mech_abbr = {"incongruity": "I", "benign_violation": "B", "superiority": "S", "relief": "R"}
    current_year = _dt.date.today().year
    lines = []
    for m in memes:
        td = "t" if m["text_dependent"] else "f"
        mechs = "".join(mech_abbr.get(x, "?") for x in m.get("mechanism", []))
        plat = _PLAT_ABBR.get(m.get("native_platform", ""), "??")
        key = m.get("key", "")
        vitality = m.get("vitality", "evergreen")
        vdate = m.get("vitality_date", "")
        if vitality == "somewhat dated" and vdate:
            years_old = current_year - int(str(vdate)[:4])
            key = f"{key}  [dated:{vdate} {years_old}y]"
        elif vitality == "retro" and vdate:
            years_old = current_year - int(str(vdate)[:4])
            key = f"{key}  [retro:{vdate} {years_old}y — oldness is the joke; needs ironic register]"
        lines.append(f"{m['name']:32s} {td}  {mechs:4s}  {plat}  {key}")
    return "\n".join(lines)


def run_model(model="claude-haiku-4-5-20251001"):
    try:
        import anthropic
    except ImportError:
        print("pip install anthropic  then set ANTHROPIC_API_KEY")
        sys.exit(1)

    client = anthropic.Anthropic()
    memes = load_db()
    roster = build_roster(memes)
    evals = [e for e in load_evals() if e.get("should_drop") and e.get("gold")]

    hits1 = hits1_adj = 0
    failures = []
    for e in evals:
        ctx = e.get("context", "")
        context_block = f"Recent conversation context:\n{ctx}\n\n" if ctx else ""
        prompt = ROSTER_PROMPT.format(roster=roster, situation=e["situation"], context_block=context_block)
        raw = _api_call(client, model, prompt)
        pick = raw.lower().replace(" ", "-")
        correct = pick == e["gold"]
        acceptable = correct or pick in e.get("acceptable_alternates", [])
        if correct:
            hits1 += 1
        if acceptable:
            hits1_adj += 1
        else:
            failures.append({"id": e["id"], "gold": e["gold"], "got": pick, "situation": e["situation"][:60]})

    n = len(evals)
    if n == 0:
        print(f"Model-native selection ({model}) — no drop cases found")
        return
    n_amb = sum(1 for e in evals if e.get("acceptable_alternates"))
    print(f"Model-native selection ({model}) — {n} drop cases ({n_amb} have acceptable alternates)")
    print(f"  P@1 strict:   {hits1}/{n} = {hits1/n:.0%}")
    print(f"  P@1 adjusted: {hits1_adj}/{n} = {hits1_adj/n:.0%}  (counts acceptable alternates)")
    if failures:
        print(f"\n  Misses ({len(failures)}):")
        for f in failures:
            print(f"    [{f['id']}] want={f['gold']:30s} got={f['got']}")
            print(f"         '{f['situation']}'")


# ---------------------------------------------------------------------------
# Anti-drop: model should say NONE for these
# ---------------------------------------------------------------------------


def run_anti(model="claude-haiku-4-5-20251001"):
    try:
        import anthropic
    except ImportError:
        print("pip install anthropic  then set ANTHROPIC_API_KEY")
        sys.exit(1)

    client = anthropic.Anthropic()
    memes = load_db()
    roster = build_roster(memes)
    evals = [e for e in load_evals() if not e.get("should_drop")]

    false_positives = []
    for e in evals:
        ctx = e.get("context", "")
        context_block = f"Recent conversation context:\n{ctx}\n\n" if ctx else ""
        prompt = ROSTER_PROMPT.format(roster=roster, situation=e["situation"], context_block=context_block)
        pick = _api_call(client, model, prompt)
        if pick.upper() != "NONE":
            false_positives.append({"id": e["id"], "picked": pick, "situation": e["situation"][:60]})

    n = len(evals)
    if n == 0:
        print(f"Anti-drop test [{model}] — no anti-drop cases found")
        return
    fp = len(false_positives)
    print(f"Anti-drop test [{model}] — {n} no-drop cases")
    print(f"  False positive rate: {fp}/{n} = {fp/n:.0%}")
    if false_positives:
        print(f"\n  False positives ({fp}):")
        for f in false_positives:
            print(f"    [{f['id']}] picked={f['picked']:25s} '{f['situation']}'")


# ---------------------------------------------------------------------------
# Discrimination: A vs B pairs
# ---------------------------------------------------------------------------


def run_discrimination(model="claude-haiku-4-5-20251001"):
    try:
        import anthropic
    except ImportError:
        print("pip install anthropic  then set ANTHROPIC_API_KEY")
        sys.exit(1)

    client = anthropic.Anthropic()
    memes = {m["name"]: m for m in load_db()}
    evals = [e for e in load_evals() if e.get("discrimination")]

    n = len(evals)
    if n == 0:
        print("Discrimination test — no discrimination cases found")
        return

    def meme_hint(name):
        m = memes.get(name, {})
        return f"{name} — deploy when: {m.get('deploy_when', '?')}. Key: {m.get('key', '?')}"

    disc_prompt = """Two memes are candidates for this situation. Pick the one that fits more precisely.
Reply with ONLY the exact meme name (slug form), nothing else.

Situation: {situation}

Candidate 1: {a}
Candidate 2: {b}"""

    correct = 0
    for e in evals:
        # Stable randomisation by case ID — prevents gold from always being candidate A
        gold_first = int(hashlib.md5(e["id"].encode(), usedforsecurity=False).hexdigest(), 16) % 2 == 0
        cand_a = e["gold"] if gold_first else e["wrong"]
        cand_b = e["wrong"] if gold_first else e["gold"]
        prompt = disc_prompt.format(
            situation=e["situation"],
            a=meme_hint(cand_a),
            b=meme_hint(cand_b),
        )
        raw = _api_call(client, model, prompt, max_tokens=30).lower()
        # Extract meme name: check if either candidate name appears in response
        if e["gold"] in raw:
            pick = e["gold"]
        elif e["wrong"] in raw:
            pick = e["wrong"]
        else:
            pick = raw.replace(" ", "-")
        if pick == e["gold"]:
            correct += 1
        else:
            print(f"  [{e['id']}] chose {pick!r} over {e['gold']!r} — {e['why'][:60]}")

    print(f"Discrimination test [{model}] — {n} pairs: {correct}/{n} = {correct/n:.0%}")


# ---------------------------------------------------------------------------


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    model_arg = sys.argv[2] if len(sys.argv) > 2 else "claude-haiku-4-5-20251001"
    if mode == "baseline":
        run_baseline()
    elif mode == "model":
        run_model(model=model_arg)
    elif mode == "anti":
        run_anti(model=model_arg)
    elif mode == "discrimination":
        run_discrimination(model=model_arg)
    elif mode == "all":
        run_baseline()
        print()
        run_model(model=model_arg)
        print()
        run_anti(model=model_arg)
        print()
        run_discrimination(model=model_arg)
    else:
        print("Usage: run_eval.py [baseline|model|anti|discrimination|all]")
