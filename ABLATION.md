# Ablation

Headline is macro exact match: the mean of the unique, ambiguous and inconsistent exact-match rates. Sampling is temperature 1.0, top_p 0.95, reasoning off. Each live arm is one run unless the runs column says otherwise. `validate` and `dedupe` replay a logged run, so the model output is identical and only the removed component changes. `vote` and `repair` do nothing at 1×: the cap is one call, so those two rows are the full 1× runs and were not called again.

The naive row is not a component. It is the model writing the final JSON itself, with no solver.

| arm | budget | runs | how | macro | unique | ambiguous | inconsistent | ambiguous_partial | inconsistent_label_only |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| full | 1x | 2 | live | 0.817, 0.800 | 0.80, 0.80 | 0.90, 0.85 | 0.75, 0.75 | 0.90, 0.85 | 0.25, 0.20 |
| no-gloss | 1x | 1 | live | 0.683 | 0.50 | 0.75 | 0.80 | 0.75 | 0.05 |
| no-fewshot | 1x | 1 | live | 0.533 | 0.55 | 0.40 | 0.65 | 0.40 | 0.15 |
| no-json_mode | 1x | 1 | live | 0.867 | 0.80 | 0.95 | 0.85 | 0.95 | 0.10 |
| no-validate | 1x | 1 | replay of 1x r2 | 0.833 | 0.85 | 0.90 | 0.75 | 0.90 | 0.25 |
| no-dedupe | 1x | 1 | replay of 1x r2 | 0.817 | 0.80 | 0.90 | 0.75 | 0.90 | 0.25 |
| no-vote | 1x | — | same as full | — | — | — | — | — | — |
| no-repair | 1x | — | same as full | — | — | — | — | — | — |
| naive | 1x | 1 | live | 0.000 | 0.00 | 0.00 | 0.00 | 0.00 | 0.20 |
| full | 3x | 2 | live | 0.950, 0.917 | 0.95, 0.85 | 1.00, 1.00 | 0.90, 0.90 | 1.00, 1.00 | 0.10, 0.10 |
| no-gloss | 3x | 1 | live | 0.733 | 0.65 | 0.70 | 0.85 | 0.70 | 0.05 |
| no-fewshot | 3x | 1 | live | 0.633 | 0.60 | 0.55 | 0.75 | 0.55 | 0.05 |
| no-json_mode | 3x | 1 | live | 0.933 | 0.85 | 1.00 | 0.95 | 1.00 | 0.05 |
| no-validate | 3x | 1 | replay of 3x r1 | 0.933 | 0.95 | 0.95 | 0.90 | 0.95 | 0.10 |
| no-dedupe | 3x | 1 | replay of 3x r1 | 0.950 | 0.95 | 1.00 | 0.90 | 1.00 | 0.10 |
| no-vote | 3x | 1 | live | 0.950 | 0.85 | 1.00 | 1.00 | 1.00 | 0.00 |
| no-repair | 3x | 1 | live | 0.900 | 0.90 | 0.95 | 0.85 | 0.95 | 0.10 |
| naive | 3x | 1 | live | 0.000 | 0.00 | 0.00 | 0.00 | 0.00 | 0.10 |
| full | 10x | 1 | live | 0.950 | 0.95 | 1.00 | 0.90 | 1.00 | 0.10 |
| no-validate | 10x | 1 | replay of 10x r1 | 0.950 | 0.95 | 1.00 | 0.90 | 1.00 | 0.10 |
| no-dedupe | 10x | 1 | replay of 10x r1 | 0.950 | 0.95 | 1.00 | 0.90 | 1.00 | 0.10 |
| no-repair | 10x | 1 | live, 10 calls on every item | 0.917 | 0.85 | 1.00 | 0.90 | 1.00 | 0.10 |
| no-vote | 10x | 1 | live, stopped at 2–4 calls | 0.933 | 0.95 | 0.90 | 0.95 | 0.90 | 0.00 |
| naive | 10x | 1 | live, 10 calls on every item | 0.000 | 0.00 | 0.00 | 0.00 | 0.00 | 0.10 |

Confusion for the full system, rows are the true kind, columns are declared unique / ambiguous / inconsistent:

- 1× run 2: unique 17/3/0, ambiguous 0/20/0, inconsistent 0/0/20
- 1× run 3: unique 18/1/1, ambiguous 0/19/1, inconsistent 0/1/19
- 3× run 1 and the 10× run: unique 20/0/0, ambiguous 0/20/0, inconsistent 0/0/20
- 3× run 2: unique 18/1/1, ambiguous 0/20/0, inconsistent 0/0/20

An earlier prompt, not the submitted system, scored 0.833 at 1× (one run) with a worse case matrix. It is not in the table.

`python ablate.py --items candidate_package/items.json --key candidate_package/visible_key.json --budgets 1x,3x,10x` re-runs the live arms and writes `ablation/ABLATION.md`. It does not replace this file, which is the scored table reported in the writeup. Pass `--replay-from` to rescore `validate` and `dedupe` from a log. At 10× that script only turns off `vote`, `repair`, `validate` and `dedupe`; the prompt components are not re-run at 10× because the 10× question is how the extra calls are spent.
