import pytest
from pydantic import ValidationError

from app.matching import match_items
from app.schemas import AggregatedFlag, Flag, RunResult, Target, Verification
from eval import metrics as metrics_module
from eval.metrics import Adjudication, AnswerIssue, AnswerKey, compute_metrics


def fixture_flag(check_id='causal_language', target_id='p2.s1', *, origin='model'):
    scope = 'document' if target_id == 'doc' else 'section' if target_id.startswith('sec') else 'claim'
    return Flag(check_id=check_id, dimension='interpretation', origin=origin,
                target=Target(target_id=target_id, scope=scope, start=0, end=1),
                severity='medium', reason='Synthetic test finding.', quote='Synthetic')


def result(index, flags=(), status='complete'):
    return RunResult(run_index=index, status=status, flags=list(flags), elapsed_seconds=0)


@pytest.mark.parametrize(('left', 'right', 'matched'), [
    ('p2.s1-s3', 'p2.s2', True), ('p2.s1', 'p2.s2', False),
    ('p2.s1-s3', 'p3.s2', False), ('sec1', 'sec1', True),
    ('sec1', 'sec2', False), ('doc', 'doc', True), ('doc', 'p1.s1', False),
])
def test_shared_matching_uses_sentence_overlap_and_exact_larger_targets(left, right, matched):
    assert bool(match_items([fixture_flag(target_id=left)], [fixture_flag(target_id=right)])) is matched


def test_matching_requires_same_check_and_consumes_each_object_once():
    broad = fixture_flag(target_id='p2.s1-s3')
    short = [fixture_flag(target_id='p2.s1'), fixture_flag(target_id='p2.s3')]
    assert match_items([broad], short) == [(0, 0)]
    assert match_items([broad], [fixture_flag('other_check', 'p2.s1')]) == []
    assert len(match_items(short, [broad])) == 1


def test_design_example_jaccard_is_one_third_and_ignores_code_findings(monkeypatch):
    left = [fixture_flag('causal_language', 'p2.s1-s2'), fixture_flag('unsupported_guarantee', 'p4.s1')]
    right = [fixture_flag('causal_language', 'p2.s2'), fixture_flag('unsupported_guarantee', 'p5.s1')]
    code = fixture_flag('arithmetic_mismatch', 'p6.s1', origin='code')
    calls = []
    original = metrics_module.match_items

    def tracked(a, b):
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(metrics_module, 'match_items', tracked)
    draft = AnswerKey(issues=[AnswerIssue(check_id='causal_language', target_id='p2.s2',
                                         excerpt='Synthetic', description='Synthetic draft issue.')])
    metrics = compute_metrics([result(0, [*left, code]), result(1, right)], requested_runs=2, answers=draft)
    assert metrics['consistency']['pairwise_jaccard'] == pytest.approx(1 / 3)
    assert metrics['recall']['value'] is None and metrics['answer_key_status'] == 'draft'
    assert sum(flag['matches_answer_key'] for flag in metrics['flags']) == 1
    assert any(a and b and isinstance(b[0], AnswerIssue) for a, b in calls)
    assert any(a and b and isinstance(a[0], Flag) and isinstance(b[0], Flag)
               and len(a) == len(b) == 2 for a, b in calls)
    code_result = next(flag for flag in metrics['flags'] if flag['origin'] == 'code')
    assert code_result['occurrences'] is None and code_result['confidence'] is None


def test_metrics_deduplicate_each_run_and_keep_human_work_pending():
    a, b, c = [fixture_flag(key) for key in ('a', 'b', 'c')]
    draft = AnswerKey(issues=[AnswerIssue(check_id='a', target_id='p2.s1', excerpt='Synthetic',
                                         description='Draft fixture; not a user answer.')])
    metrics = compute_metrics([result(0, [a, a, b]), result(1, [a, c])], requested_runs=2, answers=draft)
    assert metrics['unique_flag_count'] == 3
    assert {flag['check_id']: flag['confidence'] for flag in metrics['flags']} == {'a': 1, 'b': .5, 'c': .5}
    assert metrics['recall']['value'] is None
    assert metrics['error_flag_proportion']['value'] is None
    assert metrics['error_flag_proportion']['pending'] == 3
    assert all('answer_key_status' not in row for row in metrics['unmatched_flags'])


def test_partial_batches_and_double_empty_pairs_suppress_formal_metrics():
    partial = compute_metrics([result(0, [fixture_flag()]), result(1, [], 'failed')], requested_runs=3)
    assert partial['valid_runs'] == 1 and partial['missing_runs'] == 1
    assert partial['flags'][0]['confidence'] is None
    assert partial['consistency']['pairwise_jaccard'] is None
    assert partial['recall']['value'] is None
    assert partial['error_flag_proportion']['value'] is None
    empty = compute_metrics([result(0), result(1)], requested_runs=2)
    assert empty['consistency']['pairwise_jaccard'] is None
    assert empty['consistency']['double_empty_pairs'] == 1


def test_code_findings_replace_matching_model_findings_and_verification_distribution():
    model = fixture_flag(target_id='p2.s1-s2')
    code = fixture_flag(target_id='p2.s1', origin='code')
    other = fixture_flag('unsupported_guarantee', 'p3.s1')
    verified = AggregatedFlag(**other.model_dump(), flag_id='F2', occurrences=2, valid_runs=2,
                              confidence=1, verification=Verification(label='counter_evidence_found'))
    metrics = compute_metrics([result(0, [model, other]), result(1, [other])], requested_runs=2,
                              code_flags=[code], aggregated_flags=[AggregatedFlag(**code.model_dump(), flag_id='F1', valid_runs=2), verified],
                              adjudications=[Adjudication(check_id=other.check_id, target_id='p3.s1',
                                                         status='confirmed_error')])
    assert metrics['unique_flag_count'] == 2
    assert metrics['verification_distribution']['counter_evidence_found']['confirmed_error'] == 1
    assert metrics['verification_distribution']['unverified']['pending'] == 1


def test_answer_approval_note_and_local_excerpts_are_required():
    with pytest.raises(ValidationError):
        AnswerKey(status='approved', issues=[])
    with pytest.raises(ValidationError):
        AnswerIssue(check_id='causal_language', target_id='p2.s1', description='Missing excerpt.')
    assert AnswerIssue(check_id='question_stated', target_id='doc', description='Document issue.').excerpt is None


def test_e2e_verification_labels_count_each_review_observation():
    flag = fixture_flag()
    observed = [AggregatedFlag(**flag.model_dump(), flag_id='F1', valid_runs=1, occurrences=1,
                              confidence=1, verification=Verification(label=label))
                for label in ('supported', 'counter_evidence_found')]
    metrics = compute_metrics([result(0, [flag]), result(0, [flag])], requested_runs=2,
                              aggregated_flags=observed)
    assert metrics['unique_flag_count'] == 1
    assert metrics['verification_observation_count'] == 2
    assert metrics['verification_distribution']['supported']['pending'] == 1
    assert metrics['verification_distribution']['counter_evidence_found']['pending'] == 1


def test_simulated_approval_scores_code_and_one_to_one_overlap_and_rejects_conflicts():
    # This in-memory approval exercises the gate; it never approves project data.
    broad = fixture_flag('causal_language', 'p2.s1-s3')
    code = fixture_flag('arithmetic_mismatch', 'p1.s1', origin='code')
    simulated_answers = AnswerKey(
        status='approved',
        approval_note='isolated test simulation; not approval of project data',
        issues=[
            AnswerIssue(check_id='arithmetic_mismatch', target_id='p1.s1', excerpt='Synthetic',
                        description='Isolated arithmetic test issue.'),
            AnswerIssue(check_id='causal_language', target_id='p2.s1', excerpt='Synthetic',
                        description='Isolated first sentence test issue.'),
            AnswerIssue(check_id='causal_language', target_id='p2.s3', excerpt='Synthetic',
                        description='Isolated third sentence test issue.'),
        ],
    )
    scored = compute_metrics([result(0, [broad]), result(1, [broad])], requested_runs=2,
                             answers=simulated_answers, code_flags=[code])
    assert scored['recall']['value'] == pytest.approx(2 / 3)
    assert scored['recall']['matched_issue_count'] == 2
    assert scored['recall']['approved_issue_count'] == 3
    assert next(flag for flag in scored['flags'] if flag['origin'] == 'code')['matches_answer_key']
    assert len(match_items([broad], simulated_answers.issues)) == 1

    partial = compute_metrics([result(0, [broad]), result(1, [], 'failed')], requested_runs=2,
                              answers=simulated_answers, code_flags=[code])
    assert partial['recall']['value'] is None
    assert partial['recall']['matched_issue_count'] is None
    assert partial['recall']['unavailable_reason'] == 'incomplete_batch'

    overlap_conflict = Adjudication(check_id='causal_language', target_id='p2.s1-s2',
                                   status='confirmed_error')
    with pytest.raises(ValueError, match='contradicts'):
        compute_metrics([result(0, [broad]), result(1, [broad])], requested_runs=2,
                        answers=simulated_answers, code_flags=[code], adjudications=[overlap_conflict])
