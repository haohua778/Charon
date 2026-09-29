"""Run bounded fixed-input or full-path experiments through run_review."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from app.agent.graph import run_review
from app.core.config import PROJECT_ROOT, Settings
from app.core.errors import CharonError
from app.core.rubrics import load_rubric, validate_variant_structure
from app.llm.client import JsonLLM
from app.llm.fake import FakeLLM
from app.schemas import FixedInput, ReviewRecord, ReviewRequest
from app.segment import resolve_target, segment_report
from eval.metrics import Adjudication, AnswerIssue, AnswerKey, compute_metrics, validate_adjudications

SYNTHETIC = PROJECT_ROOT / 'data' / 'synthetic'
Mode = Literal['fixed', 'end_to_end']


def _validate_human_inputs(report: str, answers: AnswerKey | None,
                           adjudications: list[Adjudication], *, settings: Settings,
                           variant: str, analysis_type: str = 'program_evaluation') -> None:
    units, _ = segment_report(report)
    checks = {check.check_id: check for check in load_rubric(analysis_type, variant, settings.rubrics_dir).checks}
    for item in [*(answers.issues if answers else []), *adjudications]:
        try:
            target = resolve_target(report, item.target_id, units)
        except (ValueError, CharonError) as exc:
            raise ValueError('Human input uses an unknown target; use current sentence, section, or doc IDs') from exc
        check = checks.get(item.check_id)
        if not check or check.kind == 'needs_standard' or target.scope != check.scope:
            raise ValueError('Human input has an unknown or wrong-scope check/target key')
        if isinstance(item, AnswerIssue):
            if item.excerpt is not None and item.excerpt not in report[target.start:target.end]:
                raise ValueError('Answer excerpt must exactly match text inside its current source target')
    validate_adjudications(answers, adjudications)


def _usage(records: list[ReviewRecord]) -> dict[str, Any]:
    def token_sum(field: str) -> int | None:
        values = [getattr(record.usage, field) for record in records]
        return sum(values) if values and all(value is not None for value in values) else None

    return {
        'calls': sum(record.usage.calls for record in records),
        'input_tokens': token_sum('input_tokens'),
        'output_tokens': token_sum('output_tokens'),
    }


async def evaluate(
    report: str, *, mode: Mode = 'end_to_end', runs: int = 3,
    analysis_type: str = 'program_evaluation',
    variant: Literal['specific', 'vague'] = 'specific', tools_enabled: bool = True,
    verify_enabled: bool = True, settings: Settings, fixed_input: FixedInput | None = None,
    answers: AnswerKey | None = None, adjudications: list[Adjudication] | None = None,
    live: bool = False, max_calls: int | None = None,
) -> dict[str, Any]:
    """Use a fresh model and full path per e2e run, or one fixed-input batch.

    The caller gives the remaining command budget as an integer. Every physical
    attempt, including repairs, consumes that budget. Returned records remain
    intact, including their local run indices and preparation failures.
    """
    if mode not in ('fixed', 'end_to_end'):
        raise ValueError('Unknown evaluation mode; use fixed or end_to_end')
    request = ReviewRequest(report=report, rubric_id=analysis_type, runs=runs, variant=variant,
                            tools_enabled=tools_enabled, verify_enabled=verify_enabled)
    decisions = adjudications or []
    _validate_human_inputs(report, answers, decisions, settings=settings, variant=variant,
                           analysis_type=analysis_type)
    if mode == 'fixed' and fixed_input is None:
        raise ValueError('Fixed mode requires --fixed-input with current sentence IDs and exact claim text')
    if mode == 'end_to_end' and fixed_input is not None:
        raise ValueError('end_to_end mode starts with the raw report; omit --fixed-input')
    if live:
        if settings.charon_offline:
            raise ValueError('Live evaluation requires CHARON_OFFLINE=false')
        if not settings.charon_allow_live:
            raise ValueError('Live calls require CHARON_ALLOW_LIVE=true and explicit user authorization')
        if report != (SYNTHETIC / 'report.txt').read_text(encoding='utf-8'):
            raise ValueError('Live evaluation is restricted to the bundled synthetic report')
    budget = settings.charon_max_calls if max_calls is None else max_calls
    if not 0 <= budget <= settings.charon_max_calls:
        raise ValueError('Invalid command call budget')
    records: list[ReviewRecord] = []
    used_calls = 0
    started = perf_counter()
    for batch_size in ([runs] if mode == 'fixed' else [1] * runs):
        remaining = max(0, budget - used_calls)
        llm = JsonLLM(settings, max_calls=remaining) if live else FakeLLM(max_calls=remaining)
        try:
            record = await run_review(request.model_copy(update={'runs': batch_size}), llm=llm,
                                      settings=settings, fixed_input=fixed_input)
        finally:
            used_calls += llm.usage.calls
            if live:
                await llm.aclose()
        records.append(record)
    elapsed = perf_counter() - started
    usage = _usage(records)
    evaluated_runs = [run for record in records for run in record.runs]
    preparation_failures = sum(record.requested_runs for record in records if not record.runs and record.status == 'failed')
    aggregated = [flag for record in records for flag in record.flags]
    metrics = compute_metrics(evaluated_runs, requested_runs=runs, answers=answers,
                              adjudications=decisions,
                              code_flags=[flag for flag in aggregated if flag.origin == 'code'],
                              aggregated_flags=aggregated, preparation_failed_runs=preparation_failures)
    return {
        'mode': mode,
        'analysis_type': analysis_type,
        'variant': variant,
        'tools_enabled': tools_enabled,
        'verify_enabled': verify_enabled,
        'provenance': ('measured_synthetic' if metrics['valid_runs'] and usage['calls'] else 'not_measured') if live else 'offline_fake',
        'wall_clock_seconds': elapsed,
        'review_elapsed_seconds_sum': sum(record.elapsed_seconds for record in records),
        'usage': usage,
        'remaining_command_calls': max(0, budget - used_calls),
        'metrics': metrics,
        'unchecked_check_ids': sorted({check_id for record in records for check_id in record.unchecked_check_ids}),
        'pending_standards': list({item.check_id: item.model_dump() for record in records for item in record.pending_standards}.values()),
        'verification_status_counts': {status: sum(record.verification_status == status for record in records)
                                       for status in ('not_run', 'complete', 'failed')},
        'evaluation_runs': [run.model_dump() for run in evaluated_runs],
        'records': [record.model_dump() for record in records],
    }


def _validate_comparison(settings: Settings, analysis_type: str = 'program_evaluation') -> None:
    validate_variant_structure(load_rubric(analysis_type, 'specific', settings.rubrics_dir),
                               load_rubric(analysis_type, 'vague', settings.rubrics_dir))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['fixed', 'end_to_end'], default='end_to_end')
    parser.add_argument('--analysis-type', choices=['kpi_comparison', 'program_evaluation'], default='program_evaluation')
    parser.add_argument('--report', type=Path, default=SYNTHETIC / 'report.txt')
    parser.add_argument('--fixed-input', type=Path, help='Required in fixed mode; current sentence IDs and exact claim text')
    parser.add_argument('--answers', type=Path, help='Optional draft or human-approved answer key with exact source excerpts')
    parser.add_argument('--adjudications', type=Path, help='JSON list of pending/confirmed_error/confirmed_valid decisions')
    parser.add_argument('--runs', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--variant', choices=['specific', 'vague'], default='specific')
    parser.add_argument('--compare', action='store_true', help='Run specific/vague × tools enabled/disabled (four cells)')
    parser.add_argument('--no-tools', action='store_true')
    parser.add_argument('--no-verify', action='store_true', help='Disable the single verification pass; not a comparison-matrix axis')
    parser.add_argument('--live', action='store_true', help='Paid synthetic calls; requires allow-live setting and explicit user authorization')
    parser.add_argument('--output', type=Path, help='Write complete JSON here; otherwise print it to stdout')
    return parser


def _read_fixed(path: Path) -> FixedInput:
    try:
        return FixedInput.model_validate_json(path.read_text(encoding='utf-8'))
    except ValidationError as exc:
        raise ValueError('Legacy or invalid fixed input: create a new snapshot with sentence IDs, exact claim text, section_id, unit_ids, and unit-based evidence; use end_to_end until it is available') from exc


def _read_answers(path: Path) -> AnswerKey:
    try:
        return AnswerKey.model_validate_json(path.read_text(encoding='utf-8'))
    except ValidationError as exc:
        raise ValueError('Legacy or invalid answer key: use current check and sentence/section/doc IDs, add exact excerpts for non-document issues, and keep status draft until human approval') from exc


async def _run_cli(args: argparse.Namespace) -> dict[str, Any]:
    if args.compare and args.no_tools:
        raise ValueError('--compare already includes both tool settings; omit --no-tools')
    if args.mode == 'end_to_end' and args.fixed_input:
        raise ValueError('--fixed-input is only valid in fixed mode')
    if args.mode == 'fixed' and not args.fixed_input:
        raise ValueError('Fixed mode requires --fixed-input; bundled legacy snapshots are not loaded automatically')
    settings = Settings() if args.live else Settings(_env_file=None)
    report = args.report.read_text(encoding='utf-8')
    fixed = _read_fixed(args.fixed_input) if args.fixed_input else None
    answers = _read_answers(args.answers) if args.answers else None
    adjudications = TypeAdapter(list[Adjudication]).validate_json(args.adjudications.read_text(encoding='utf-8')) if args.adjudications else []
    if args.compare:
        _validate_comparison(settings, args.analysis_type)
    cells = [(variant, enabled) for variant in ('specific', 'vague') for enabled in (True, False)] if args.compare else [(args.variant, not args.no_tools)]
    used_calls = 0
    results = []
    started = perf_counter()
    for variant, enabled in cells:
        result = await evaluate(report, mode=args.mode, runs=args.runs, analysis_type=args.analysis_type,
                                variant=variant, tools_enabled=enabled, verify_enabled=not args.no_verify,
                                settings=settings, fixed_input=fixed, answers=answers,
                                adjudications=adjudications, live=args.live,
                                max_calls=max(0, settings.charon_max_calls - used_calls))
        used_calls += result['usage']['calls']
        results.append(result)
    return {
        'experiment': 'synthetic_rubric_specificity_and_tools' if args.compare else 'single_configuration',
        'wall_clock_seconds': perf_counter() - started,
        'call_budget': settings.charon_max_calls,
        'remaining_calls': max(0, settings.charon_max_calls - used_calls),
        'complete': all(result['metrics']['complete_batch'] for result in results),
        'results': results,
    }


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        result = asyncio.run(_run_cli(args))
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding='utf-8')
            print(f'Evaluation saved to {args.output.resolve()}')
        else:
            print(rendered, end='')
        return 0 if result['complete'] else 2
    except ValidationError:
        print('Evaluation could not start: invalid evaluation input/configuration', file=sys.stderr)
        return 2
    except CharonError as exc:
        print(f'Evaluation could not start: {exc.code}', file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f'Evaluation could not start: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
