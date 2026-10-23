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

Text volume is measured first, and vector drawing enumeration is deferred until
the text volume is known to be inside the bounds: enumerating drawings is the
most expensive of these measurements on vector-heavy pages, and a page already
excluded by its volume cannot reach the costly detector.

The 30-drawing ceiling preserves the pre-existing parser guard. The word and
text ceilings are conservative page-volume limits, not a benchmark-derived
runtime model. The largest of the three authorized development pages had 607
words and 4,130 text characters; the additional limits are about five times
those observed amounts, leaving headroom for denser pages while excluding
outlier page volume. These pages had 43–1,085 drawings, so they use the
fallback path based on drawing complexity. No universal worst-case runtime
guarantee is claimed. A future change to these limits requires measured
workload evidence and tests for the diagnostic and fallback behavior.

### Page-scoring compatibility

The scoring formula is unchanged. The legacy `has_tables` signal follows the
same rule as before whenever the workload guard allows the detector to run.
The precise deviation is on pages the guard excludes: the legacy rule could
call `find_tables()` on a page with 1–30 drawings and more than 3,000 words or
20,000 text characters, and the guard cannot. On those pages `has_tables` falls
back to the tabular-line heuristic, the page score loses its 2.0 table
component, and a page whose total score was within 2.0 of the candidate
threshold can lose candidate status. The deviation is bounded to pages outside
the documented workload bounds and is covered by an explicit test.

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

Rows are grouped into one candidate only when the columns continue and the row
rhythm continues. Two different detectors are used, because they answer
different questions.

*Row boundaries.* The boundary between two blocks of rows is decided locally: a
gap is treated as a table boundary when it is more than three times the
smallest row pitch that surrounds it, and never on a gap of less than one and a
half text heights. A page-wide average is deliberately not used for this
decision, because it is inflated by the very gap it should detect on a page
with few rows, and because it would penalise a table whose own rows are more
widely spaced than another table on the same page.

*Heading bridges.* A bridge is a text-only line inside a table, such as a
subheading, and it has its own, more permissive ceiling: the vertical gap it
may span is at most three and a half times the same local row pitch, and never
less than five text heights. A bridge therefore survives a subheading that is
separated from the rows below it by more space than two table blocks would be,
which the boundary rule would otherwise treat as a discontinuity. A bridge is
only accepted with the structural evidence it needs: a plausible intervening
heading line, continuing columns, and a continuing label position. A page-wide
average is not reintroduced as the bridge reference.

A row block that is separated from the block above it by whitespace, or by a
table of its own, is emitted as its own candidate. A block that is separated but
does not independently carry enough recurring numeric columns is left
unresolved and reported (`aligned_numeric_block_dropped`) rather than merged
into a confident candidate, so a candidate may be abstained from where the
evidence does not reach it. A row whose value cells are all reporting periods
is a header row: it governs the rows below it and it ends the block above it.

*Widened fusion window.* Because a bridge's ceiling is more permissive than the
row-boundary rule, two headerless row blocks separated by a single heading-like
text line are fused into one candidate when the row-top pitch across the heading
falls in the widened window (roughly three to four and a half times the local
row pitch, and never beyond the bridge ceiling). This is deliberate: it is what
lets a legitimate internal subheading keep a table together, but it means the
adapter cannot always tell whether a heading-like line is an internal subheading
or the start of a new table. That ambiguity is not resolvable from geometry
alone, so the fused behaviour is a documented trade-off rather than a defect.

A reporting period is recognised with or without a trailing footnote marker, so
`2024*` is a period and not the number 2024. The marked text is preserved in the
emitted cell. Quarterly labels are recognised with and without a fiscal-year
prefix, so `Q4 2024`, `Q1 2024` and `Q4 FY2024` are periods.

A label that is printed across several words is parsed as one label: `Q4` and
`2024` next to each other form the single label `Q4 2024`. The longest run of
words that is itself a period label wins, so `Q4 2024 2025` reads as the two
labels `Q4 2024` and `2025`. By contrast, one cell that carries two different
period labels (for example `2023 2024`) cannot label a single column: the cell
is ambiguous, the period association stays unresolved, and `period_header_conflict`
is reported. The borderless path and the gap-based path share this policy; the
aligned path has no cell concept for headers, so a header line whose tokens
cannot be justified one per column is left unresolved there too.

For right-aligned grids, a period header is matched to the value columns in
reading order rather than by comparing a header's centre with a value's right
edge; a centred header over a right-aligned value column is therefore attached
to the column it labels. The assignment must also be justified by the header's
own geometry: the offsets between the header tokens and the columns they
govern may not differ from each other by more than 0.6 of the column spacing.
The same policy applies to the gap-based path, which measures those offsets
from the regions its columns occupy rather than from recurring numeric
positions; a header whose column assignment the geometry cannot justify is
reported there as well (`period_header_conflict`) and is not attached to a
column. When the tokens compete for one column, when they are crowded between
the same two columns, when one cell carries more than one period token, or
when a detected period header cannot govern the block, the association is
reported (`period_header_conflict`, `period_header_not_associated`,
`ambiguous_period_header`) and the period association stays unresolved. A
single page-wide period row may also govern separated row blocks only when all
of that block's recurring value columns receive an unambiguous period cell; the
adapter retains separate row-block candidates and records that this association
used the page-wide grid.

A candidate's bounding box covers the rows the candidate proposes. A period
header that governs a block from elsewhere on the page is referenced by its own
cell and cell bounding box, and it does not extend the candidate's region over
the other table's rows (`period_header_outside_candidate_region`). Competing
or partial headers remain unresolved.

The behaviour described here is bounded by the synthetic layouts in the test
suite. It is not a claim of general PDF-table coverage.

## Known remaining limits

* Dot leaders and other filler between a label and its value defeat the
  gap-based column model, because the leader fills the whitespace the gap
  detector measures. Such tables are not reconstructed by that path.
* The KPI label/value-group detector only recognises upper-case labels that
  are left-aligned with their value on the following line. Title-case,
  sentence-case, right-aligned and same-line label/value cards are not
  detected.
* A wrapped row label is reported as unresolved; the preceding line's words are
  not merged back onto the row. In dense layouts the diagnostic is also raised
  for rows that are not wrapped, so it is not a reliable signal on its own.
* Multi-level headers are not represented as a header path. A header line that
  carries more period tokens than the candidate has value columns is attached
  only when the assignment is unambiguous; otherwise the period association
  stays unresolved.
* Tables whose ruling produces a non-rectangular grid are dropped by the
  bordered path rather than partially represented.
* Candidate identifiers are positional (`p<page>-t<index>`), so they are stable
  for a fixed page ordering but are not stable across detector changes.
* Two blocks of rows that are separated by a gap but are otherwise identical in
  structure cannot always be told apart from one loosely spaced table. The
  boundary rule prefers the local rhythm, so a gap that is only a few times the
  surrounding row pitch is treated as part of the same table.
