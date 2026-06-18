"""ADR-14: the differentiator test suite -- the moat, executable.

Five invariants, every one with positive + adversarial tests. This
package doubles as the demo script and the sales claim:

1. **No silent writes** -- nothing becomes trusted memory without
   an authorizing decision; every other path must fail.
2. **Full provenance** -- every trusted fact resolves its complete
   chain: entry -> decision -> candidate -> span -> source.
3. **Reciprocal supersession** -- corrections are governed,
   reciprocal, and race-safe: exactly one winner, ever.
4. **Deterministic receipts** -- same memory + same request =>
   byte-identical fingerprint; the anchor chain catches every
   tamper.
5. **Grounded or refused** -- no uncited factual sentence, ever;
   when memory cannot support the question, the system says so.

If an invariant test is ever weakened to make a feature pass,
that change stops and escalates -- the invariants are the company.
"""
