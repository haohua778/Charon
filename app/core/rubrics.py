"""Local rubric loading and controlled specific/vague comparison."""
import re
from pathlib import Path

from pydantic import ValidationError

from app.core.config import PROJECT_ROOT
from app.core.errors import CharonError
from app.schemas import Rubric, Variant


def load_rubric(rubric_id: str, variant: Variant = 'specific',
                root: Path = PROJECT_ROOT / 'rubrics') -> Rubric:
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', rubric_id) or variant not in ('specific', 'vague'):
        raise CharonError('invalid_rubric')
    path = root / f'{rubric_id}.{variant}.json'
    try:
        if path.resolve().parent != root.resolve():
            raise CharonError('invalid_rubric')
        rubric = Rubric.model_validate_json(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise CharonError('rubric_not_found') from exc
    except (OSError, UnicodeError, ValidationError) as exc:
        raise CharonError('invalid_rubric') from exc
    if rubric.rubric_id != rubric_id or rubric.analysis_type != rubric_id or rubric.variant != variant:
        raise CharonError('invalid_rubric')
    return rubric


def validate_variant_structure(specific: Rubric, vague: Rubric) -> None:
    """Only description, question wording, and example wording may vary."""
    def structure(rubric: Rubric) -> dict:
        value = rubric.model_dump()
        value.pop('variant')
        for dimension in value['dimensions']:
            for check in dimension['checks']:
                check.pop('description')
                for question in check['questions']:
                    question.pop('ask')
                for example in check['examples']:
                    example.pop('text')
                    example.pop('why')
        return value

    if (specific.variant != 'specific' or vague.variant != 'vague'
            or structure(specific) != structure(vague)):
        raise CharonError('invalid_rubric')
