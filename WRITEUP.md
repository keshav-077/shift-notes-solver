# Shift notes with a weak model

The model is `ibm-granite/granite-4.2-8b`, temperature 1.0, top_p 0.95, reasoning off. Two probes asked it for the finished JSON before any of this was built. One ran out of tokens in prose. The other emitted JSON with two people on one station and a station on someone the header left off a station. It can format. It does not search a rota.

Asked only to label each numbered line, with a one-phrase restatement, one worked example and `json_object` output, it got about 95% of lines right and did not invent constraints out of filler. What it still got wrong was stable: a line dropped to `none`, an adjacency read backwards, a station name in a person slot, and "later than whoever has station S" given the wrong type. Extraction is therefore model-based. The case, the assignments and the minimal conflicting set are symbolic.

## What it is asked to do

Each line is one of eight relations: on or off a block, on or off a station, earlier than, on touching blocks, between two people, or earlier or later than whoever holds a named station. Anything else is `none`. The model returns a line number and strings from the header. Citations are the original lines, looked up by number.

The solver enumerates the 720 assignments. No solution: the minimal conflicting set whose lines the samples agreed on most, then the smallest. One solution: `unique`. Two or more: `ambiguous`, every solution written out. A constraint the header already makes impossible is dropped when validation is on.

Extra budget is more translations, not a request to make the page consistent. At 3×, two full-page reads, then one read of only the lines still in dispute. At 10×, four full-page reads and up to six of those short reads. The short read is another vote. A tie drops the line, which under-constrains the rota and tends to come out `ambiguous`.

The gloss is that restatement, before the type, in the same call. The few-shot is one page I wrote (Alice through Eve, a station called dispatch that this set does not use). It does not copy the visible lines. `json_object`, with a parser that keeps finished objects from a cut-off reply, is there because an array-only prompt once collapsed into comments. Validation requires every name in a constraint to occur on that line, and substitutes when the line names exactly one and the model named another. Deduping collapses two copies of the same sentence under a lead-in that names nobody.

A regex over the visible phrasing, plus this solver, scores 1.0. `run` does not import it. The solver and the citation format are enough when the translation is perfect, so the score is extraction error.

## Numbers

Macro exact match. Two 1× runs: 0.817 and 0.800. Two 3× runs: 0.950 and 0.917. One 10× run: 0.950, the same three misses as the first 3× run, and it stopped at 5 or 6 calls. Line accuracy on the 1× logs was 1116/1137 and 1115/1137. On the 0.950 runs the case matrix is diagonal: the unique miss has two blocks swapped, and the inconsistent misses cite a non-minimal set (`inconsistent_label_only` 0.10, `ambiguous_partial` 1.0). The second 3× run adds two unique items, one called inconsistent and one called ambiguous. At 1× the off-diagonal misses are unique items called ambiguous. An end-to-end baseline scores 0.000 at 1×, 3× and 10×, one run each.

## What came out, and where it still breaks

An earlier prompt scored 0.833 at 1× and called an inconsistent item unique. This prompt is a little worse at 1×, cleaner on the case matrix, and is what 3× and 10× use. `holder_before` / `holder_after` were dropped after the model wrote a correct gloss and the opposite label. It now emits `holder_order` with `person_is`, and the gloss wins when they disagree. Letting an isolated re-read outvote the page was not shipped: on the disagreements in the 10× log the page was right 23 times and the re-read 8, usually by the re-read dropping a real constraint.

Gloss off scored 0.683 at 1× and 0.733 at 3×. Few-shot off scored 0.533 and 0.633. Those two carry the system; the rest of the table is in `ABLATION.md`. Ten page-votes and no isolated read scored 0.917 and kept both bad cores. One page-read plus isolated reads, cap 10, stopped by call 4 and scored 0.933: those cores disappeared, and a real inconsistency was called unique. Deduping changed nothing. `json_object` off scored as high or higher (0.867, 0.933) and stays, because it was added for replies that do not parse.

The three errors that voting does not fix are the model agreeing with itself:

- B2-002. "Nadia then Priya, back to back" was glossed as "Priya then Nadia" on 3 of 4 reads, and the fields followed. The answer is unique and swaps those two blocks.
- B2-012. "Nadia hands straight over to Priya" was read backwards on 4 of 4 page reads. The one isolated read got it right and lost the vote. The solver then cites a conflict of size 2. Every real core here has at least 3 lines.
- B2-023. The middle of a between-line was swapped on the page and corrected once in isolation, again outvoted. The citation has 6 lines.

The 10× run stops when the samples agree. They agree on the wrong direction. A phrase list for "A then B" was not added. It would be symbolic extraction tuned to this template.

## Other people's notes, and a month

The solver never sees English, only typed tuples. The few-shot teaches the eight relations with sentences I wrote, and the header regex falls back to the model's roster from the same call, so a changed header template does not cost a second call. What will not survive is a construction this model misreads the way it misreads "A then B". Grounding only checks that the names occur on the line, and a vote among copies of one mistake stays wrong. A new idiom fails item by item, not because the solver is fitted to this generator.

With a month I would not add a phrase list. On lines where every sample agrees and the gloss has swapped the two names, I would ask a different question: which of the two names, in the order written, is earlier. More copies of the same read are what the 10× plateau already showed do not help.

## Judgment calls

Hedges keep their force. Wishes, refused requests, past arrangements and open questions do not: the model emits `none`. Treating them as negations pulls them into cores the key does not recognise. A constraint the header makes impossible is dropped only while validation is on. A timeout is counted as a call, because the proxy may have seen it; only a connection that never opened is retried for free. An HTTP 429 or 5xx spends a call and is retried only if budget remains, so 1× does not retry past its one call. Vote and repair cannot fire at 1×.

Not done: a third 1× run and a second full 10× run. The 1× pair sits in a 0.017 band and the 3× pair in a 0.033 band. The 10× run had already stopped by call 6 on the first 3× run's three items, so a repeat of that policy was not the next experiment.
