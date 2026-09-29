"""Text-only document parsing on top of the MinerU source checkout.

Run this with the interpreter of the environment where the MinerU checkout is
installed (see README, "网页与文档解析"); Charon calls it as a subprocess and
never imports MinerU itself. Only the parts that yield text are used: the
office backend for .docx/.pptx/.xlsx (no models), and the pipeline backend in
``txt`` mode for PDFs (layout and table models, no OCR, no formula models).
Markdown is written as ``<output>/<stem>/<method>/<stem>.md``; tables stay as
HTML, which Charon rewrites into pipe tables.

    python scripts/mineru_text_parse.py -p report.pdf -o out_dir [-m txt|auto|ocr] [-b pipeline]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('-p', '--path', required=True, type=Path, help='input document')
    parser.add_argument('-o', '--output', required=True, type=Path, help='output directory')
    parser.add_argument('-m', '--method', default='txt', choices=['txt', 'auto', 'ocr'],
                        help='PDF parse method; txt reads embedded text only')
    parser.add_argument('-b', '--backend', default='pipeline', help='MinerU backend for PDFs and images')
    parser.add_argument('-l', '--lang', default='en')
    parser.add_argument('--source', type=Path, default=os.environ.get('MINERU_SOURCE_DIR'),
                        help='MinerU checkout to import when it is not installed in this interpreter')
    args = parser.parse_args()

    if args.source:
        sys.path.insert(0, str(args.source))
    from mineru.cli.common import do_parse, read_fn
    from mineru.utils.enum_class import MakeMode

    args.output.mkdir(parents=True, exist_ok=True)
    do_parse(
        output_dir=str(args.output),
        pdf_file_names=[args.path.stem],
        pdf_bytes_list=[read_fn(args.path)],
        p_lang_list=[args.lang],
        backend=args.backend,
        parse_method=args.method,
        formula_enable=False,
        table_enable=True,
        f_draw_layout_bbox=False,
        f_draw_span_bbox=False,
        f_dump_md=True,
        f_dump_middle_json=False,
        f_dump_model_output=False,
        f_dump_orig_pdf=False,
        f_dump_content_list=False,
        # NLP_MD would drop every table; MM_MD keeps them as HTML and Charon strips the images.
        f_make_md_mode=MakeMode.MM_MD,
    )
    produced = list(args.output.rglob('*.md'))
    if not produced:
        print('MinerU produced no markdown', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
