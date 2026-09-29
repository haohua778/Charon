"""The shared deterministic offline substitute; outputs never measure model quality."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.errors import CharonError
from app.llm.client import T, repair_errors, repair_payload
from app.schemas import Usage

Responder = Callable[[str, dict[str, Any]], dict[str, Any] | BaseModel]


class FakeLLM:
    model = 'offline-fake'
    provenance = 'offline_fake'

    def __init__(self, responder: Responder | None = None, max_calls: int = 80) -> None:
        self.responder = responder or demo_response
        self.max_calls = max_calls
        self.usage = Usage()

    async def generate(self, stage: str, payload: dict[str, Any], response_model: type[T],
                       validator: Callable[[T], Any] | None = None) -> T:
        attempt_payload = payload
        for repair in range(2):
            if self.usage.calls >= self.max_calls:
                raise CharonError('call_budget_exceeded')
            self.usage.calls += 1
            value = self.responder(stage, attempt_payload)
            raw = value.model_dump() if isinstance(value, BaseModel) else value
            try:
                result = response_model.model_validate(raw)
                if validator is not None:
                    validator(result)
                return result
            except (ValidationError, CharonError) as exc:
                if repair:
                    code = exc.code if isinstance(exc, CharonError) else 'invalid_model_output'
                    raise CharonError(code) from exc
                attempt_payload = repair_payload(payload, raw, repair_errors(exc, response_model))
        raise CharonError('invalid_model_output')


def _annotated_units(report: str) -> dict[str, str]:
    markers = list(re.finditer(r'\[(p\d+\.s\d+|sec\d+)(?: \| [^\]\n]+)?\] ', report))
    return {m.group(1): report[m.end():markers[i + 1].start() if i + 1 < len(markers) else len(report)].strip()
            for i, m in enumerate(markers) if m.group(1).startswith('p')}


def demo_response(stage: str, payload: dict[str, Any]) -> dict[str, Any]:
    units = _annotated_units(payload['report'])
    if stage == 'extract':
        return {'claims': [{'first': uid, 'last': uid} for uid, text in units.items()
                           if not text.startswith('|')][:80]}
    if stage == 'evidence':
        return {'evidence': [{'claim_id': c['claim_id'], 'relation': 'missing'} for c in payload['claims']]}
    if stage == 'verify':
        return {'verifications': [{'flag_id': f['flag_id'], 'premise_supported': 'partial',
                                   'support': [], 'counter': [], 'note': 'Offline fixture only.'}
                                  for f in payload['flags']]}
    rows = []
    for check in payload['checks']:
        for target_id in check['target_ids']:
            # Fixed fixture answers exercise the protocol without access to decision rules.
            # They are not an assessment of what the report actually says.
            answers, quotes = {}, {}
            target_unit = next((uid for uid in units if uid == target_id or target_id.startswith(uid + '-')), None)
            for question in check['questions']:
                field = question['field']
                defaults = [True, False] if target_id == 'doc' else [False, True]
                choices = defaults if question['type'] == 'bool' else question['values']
                eligible = choices if units else [v for v in choices if v not in question.get('quote_required_when', [])]
                eligible = eligible or choices
                answer = eligible[0]
                answers[field] = answer
                if answer in question.get('quote_required_when', []):
                    uid = target_unit or next(iter(units), None)
                    if uid is not None:
                        # A marker-free first source line avoids including Markdown table separators.
                        text = units[uid].splitlines()[0].strip()
                        if text:
                            quotes[field] = {'unit_id': uid, 'text': text}
            rows.append({'check_id': check['check_id'], 'target_id': target_id,
                         'answers': answers, 'quotes': quotes, 'note': 'Offline fixture only.'})
    return {'assessments': rows, 'tool_requests': []}
