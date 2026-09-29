import pytest

from app.agent.nodes import validated_evidence
from app.core.errors import CharonError
from app.schemas import EvidenceLink
from app.segment import claim_from_range, render_annotated, segment_report
from app.tools.cues import cue_tags, evidence_candidates


@pytest.mark.parametrize(('text', 'tags'), [
    ('Revenue rose because New York demand grew 12%.', ['causal', 'quant']),
    ('We expect higher growth than last year.', ['forecast', 'comparison']),
    ('A quiet factual statement.', []),
    ('Q3 results were driven by demand and will increase 10%.', ['causal', 'quant', 'forecast', 'comparison']),
])
def test_tags_are_deterministic_hints(text, tags):
    assert cue_tags(text) == tags
    units, _ = segment_report(text)
    rendered = render_annotated(text, units)
    assert text in rendered and '[p1.s1' in rendered


def test_candidates_rank_overlap_then_distance_exclude_claim_and_cap_at_five():
    report = ('New York grew 12% in Q3.\n\nQ3 saw growth.\n\n'
              'New York grew 12% in Q3 again.\n\nQ3 was strong.\n\nQ3 was measured.\n\n'
              'Q3 was reported.\n\nQ3 was reviewed.\n\nQ3 was archived.')
    units, _ = segment_report(report)
    claim = claim_from_range(report, units, 'p1.s1', 'p1.s1')
    candidates = evidence_candidates(report, units, claim, limit=99)
    assert candidates == ['p3.s1', 'p2.s1', 'p4.s1', 'p5.s1', 'p6.s1']
    assert 'p1.s1' not in candidates


def test_evidence_can_use_any_legal_unit_even_outside_hint_candidates():
    report = 'Training drove growth.\n\n| Group | Outcome |\n|---|---|\n| Control | Stable |'
    units, _ = segment_report(report)
    claim = claim_from_range(report, units, 'p1.s1', 'p1.s1')
    assert evidence_candidates(report, units, claim) == []
    link = EvidenceLink(claim_id=claim.claim_id, relation='contradicts',
                        unit_id='p2.s2', quote='| Control | Stable |')
    assert validated_evidence(report, units, [claim], [link]) == [link]


@pytest.mark.parametrize(('kind', 'error'), [
    ('missing_claim', 'incomplete_evidence'),
    ('unknown_unit', 'invalid_evidence_reference'),
    ('heading', 'invalid_evidence_reference'),
    ('wrong_quote', 'invalid_evidence_quote'),
    ('mixed_missing', 'invalid_evidence_reference'),
])
def test_evidence_coverage_kind_and_exact_quote_validation(kind, error):
    report = 'Training drove growth.\n\n# Control\n\nControl remained stable.'
    units, _ = segment_report(report)
    claim = claim_from_range(report, units, 'p1.s1', 'p1.s1')
    link = EvidenceLink(claim_id=claim.claim_id, relation='supports', unit_id='p3.s1',
                        quote='Control remained stable.')
    links = [link]
    if kind == 'missing_claim':
        links = []
    elif kind == 'unknown_unit':
        link.unit_id = 'p99.s1'
    elif kind == 'heading':
        link.unit_id, link.quote = 'p2', '# Control'
    elif kind == 'wrong_quote':
        link.quote = 'Invented evidence.'
    else:
        links.append(EvidenceLink(claim_id=claim.claim_id, relation='missing'))
    with pytest.raises(CharonError, match=f'^{error}$'):
        validated_evidence(report, units, [claim], links)
