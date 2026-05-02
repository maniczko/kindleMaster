# Domain Dictionary For Text Cleanup

Use a domain dictionary when a publication contains specialist PL/EN terms, product names, acronyms, IDs, or terms that must not be split/merged by cleanup.

CLI usage:

```powershell
python kindlemaster.py convert input.pdf --output output.epub --domain-dictionary docs/domain-dictionary-example.json
```

Supported keys:

- `terms`: canonical terms and variants. Use `protected: true` for terms that cleanup must not split or merge.
- `forced_splits`: exact token rewrites from a glued token to a spaced term.
- `forced_merges`: exact phrase rewrites from split fragments to one protected term.

Cleanup reports include:

- `reason_code_counts.domain-dictionary`
- `domain_dictionary_decision_count`
- `domain_dictionary_path`

This is intentionally conservative: the dictionary only applies to exact normalized forms, and low-confidence guesses still go to review instead of becoming hidden edits.
