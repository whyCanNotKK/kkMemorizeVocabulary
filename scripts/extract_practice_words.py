"""Convert space-separated vocabulary practice lines to the app's JSON v2.

Run from any directory with Python 3.12; no third-party packages are needed.
Phrases and excluded example sentences are explicit in the adjacent rules file.
Spelling is deliberately preserved, including suspected typos.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

# Support `python path/to/scripts/extract_practice_words.py` without installation.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.transfer import parse_entries, serialize_entries, write_text_atomically


RULES_PATH = Path(__file__).with_name("practice_extraction_rules.json")
EDGE_PUNCTUATION = ".,;:!?()[]{}<>\"\\/，。；：！？（）【】"
WORD = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")


def key(text: str) -> str:
    return text.strip().casefold()


def tokens(text: str) -> list[str]:
    return [part.strip(EDGE_PUNCTUATION) for part in text.split()]


def load_rules(path: Path) -> dict[str, list[str]]:
    rules = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(rules, dict) or set(rules) != {"phrases", "ignored_fragments"}:
        raise ValueError("Rules must contain phrases and ignored_fragments arrays.")
    for name, values in rules.items():
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not tokens(value)
            or any(not WORD.fullmatch(token) for token in tokens(value))
            for value in values
        ):
            raise ValueError(f"Invalid {name} rules; expected English words/phrases.")
    return rules


def extract(content: str, rules: dict[str, list[str]]) -> tuple[list[WordEntry], dict]:
    # Compare token sequences, ignoring both case and the number of spaces.
    # Longest first prevents `high vis jacket` becoming `high vis` + `jacket`.
    phrases = sorted({tuple(map(key, tokens(p))) for p in rules["phrases"]},
                     key=lambda p: (-len(p), p))
    ignored = sorted({tuple(map(key, tokens(p))) for p in rules["ignored_fragments"]},
                     key=lambda p: (-len(p), p))
    entries: dict[str, WordEntry] = {}
    sources: dict[str, dict] = {}
    related_seen: dict[str, set[str]] = {}
    phrase_sources: dict[str, dict] = {}
    report = {"statistics": {}, "phrases": [], "excluded_fragments": [],
              "punctuation_cleanup": [], "invalid_tokens": [], "entries": []}
    practice_lines = occurrences = 0
    lines = content.splitlines()

    for line_number, line in enumerate(lines, 1):
        original = line.split()
        parts = tokens(line)
        folded = list(map(key, parts))
        items = []
        index = 0
        while index < len(parts):
            excluded = next((p for p in ignored
                             if tuple(folded[index:index + len(p)]) == p), None)
            if excluded:
                end = index + len(excluded)
                report["excluded_fragments"].append({
                    "line": line_number, "text": " ".join(original[index:end]),
                    "source": line,
                })
                index = end
                continue
            phrase = next((p for p in phrases
                           if tuple(folded[index:index + len(p)]) == p), None)
            size = len(phrase) if phrase else 1
            end = index + size
            if not all(WORD.fullmatch(part) for part in parts[index:end]):
                report["invalid_tokens"].append({"line": line_number,
                                                 "text": original[index]})
                index += 1
                continue
            for offset in range(index, end):
                if parts[offset] != original[offset]:
                    report["punctuation_cleanup"].append({
                        "line": line_number, "before": original[offset],
                        "after": parts[offset],
                    })
            text = " ".join(parts[index:end])
            items.append(text)
            if phrase:
                phrase_sources.setdefault(key(text), {"text": text, "first_line": line_number})
            index = end

        if not items:
            continue
        practice_lines += 1
        occurrences += len(items)
        main, *related = items
        normalized = key(main)
        if normalized not in entries:
            entries[normalized] = WordEntry(main, [Sense("n.", "1")],
                                            sort_order=len(entries) + 1)
            sources[normalized] = {"text": main, "source_lines": [], "related": []}
            related_seen[normalized] = {normalized}
        sources[normalized]["source_lines"].append(line_number)
        # Merge new relationships even when the same main word was practiced before.
        for text in related:
            if key(text) not in related_seen[normalized]:
                related_seen[normalized].add(key(text))
                entries[normalized].related.append(RelatedWord(text, [Sense("n.", "1")]))
                sources[normalized]["related"].append({"text": text, "first_line": line_number})

    related_count = sum(len(entry.related) for entry in entries.values())
    report["statistics"] = {
        "total_lines": len(lines), "practice_lines": practice_lines,
        "main_words": len(entries), "related_words": related_count,
        "merged_practice_lines": practice_lines - len(entries),
        "removed_repetitions": occurrences - len(entries) - related_count,
        "recognized_phrases": len(phrase_sources),
        "excluded_fragments": len(report["excluded_fragments"]),
        "punctuation_cleanup": len(report["punctuation_cleanup"]),
        "invalid_tokens": len(report["invalid_tokens"]),
    }
    report["phrases"] = list(phrase_sources.values())
    report["entries"] = list(sources.values())
    return list(entries.values()), report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Source practice TXT (read only)")
    parser.add_argument("-o", "--output", required=True, type=Path, help="Import JSON to generate")
    parser.add_argument("--report", type=Path, help="Defaults to OUTPUT.report.json")
    parser.add_argument("--rules", type=Path, default=RULES_PATH, help="Phrase/example rules JSON")
    parser.add_argument("--encoding", default="utf-8-sig", help="Input encoding (default: utf-8-sig)")
    args = parser.parse_args()
    report_path = args.report or args.output.with_suffix(".report.json")
    paths = [p.resolve() for p in (args.input, args.output, report_path, args.rules)]
    if len(set(paths)) != len(paths):
        parser.error("Input, output, report and rules must use different paths.")
    try:
        rules = load_rules(args.rules)
        content = args.input.read_text(encoding=args.encoding)
        entries, report = extract(content, rules)
        if not entries:
            raise ValueError("No vocabulary found; output was not written.")
        serialized = serialize_entries(entries)
        parse_entries(serialized)  # Use the real importer before publishing the result.
        report.update(source=str(args.input.resolve()), rules=str(args.rules.resolve()),
                      encoding=args.encoding,
                      notes=["Spelling and first-seen case are preserved; no automatic correction.",
                             "Related words are deduplicated within each main word, not across owners.",
                             "All senses are placeholders: n. / 1; important=true, learned=false."])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomically(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        write_text_atomically(args.output, serialized)
    except (OSError, UnicodeError, ValueError, LookupError) as error:
        parser.exit(1, f"Extraction failed: {error}\n")
    print(json.dumps(report["statistics"], indent=2))
    print(f"Import JSON: {args.output.resolve()}")
    print(f"Audit report: {report_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
