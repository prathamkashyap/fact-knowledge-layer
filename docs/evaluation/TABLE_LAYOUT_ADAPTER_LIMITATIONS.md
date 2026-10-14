# Table Layout Adapter: Workload and Scope Limits

The layout adapter emits page-local structural hypotheses. It does not decide
whether a detected region is a factual table, resolve an entity or reporting
period, normalize units, or create production facts. A candidate on one physical
page is not continued onto the next page; cross-page table continuation is not
implemented.

## PyMuPDF detector workload policy

Before calling `Page.find_tables()`, the adapter measures vector drawing count,
word count, and extracted text character count. It calls the detector only when
all measurements succeed and the page has 1–30 drawings, at most 3,000 words,
and at most 20,000 text characters. A missing measurement or a value outside
these bounds skips `find_tables()` and records the available measurements and
reason in `find_tables_skipped_workload`. Word-geometry and repeated label/value
group detection still run after a skip.

The 30-drawing ceiling preserves the pre-existing parser guard. The word and
text ceilings are conservative page-volume limits, not a benchmark-derived
runtime model. The largest of the three authorized development pages had 607
words and 4,130 text characters; the additional limits are about five times
those observed amounts, leaving headroom for denser pages while excluding
outlier page volume. These pages had 43–1,085 drawings, so they use the
fallback path based on drawing complexity. No universal worst-case runtime
guarantee is claimed. A future change to these limits requires measured
workload evidence and tests for the diagnostic and fallback behavior.

## Geometry and interpretation limits

Coordinates use unrotated PyMuPDF page coordinates: top-left origin, x grows
rightward, y grows downward, and units are PDF points. Rotated table boxes are
derotated before they are returned. Missing boxes remain absent; coordinates
are not estimated from text order.

Borderless grids and KPI-like label/value groups are heuristic layouts. A
repeated label/value pattern can still be semantically ambiguous, so confidence
remains unknown and diagnostics preserve unresolved context. The KPI grouping
path requires repeated labels and adjacent group headings; it intentionally
abstains on one-off definition lists. Merged or incomplete headers may remain
ambiguous. Unmatched and ambiguous structure must not be promoted to a `Fact`
without a later, separately tested semantic mapping phase.

For right-aligned grids, a nearby period row is associated by x alignment. A
single page-wide period row may also govern separated row blocks only when all
of that block's recurring value columns have a corresponding aligned period
cell; the adapter retains separate row-block candidates and records that this
association used the page-wide grid. Competing or partial headers remain
unresolved.
