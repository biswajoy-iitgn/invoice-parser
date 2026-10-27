"""VLM parsers, run with work/bench/venv/bin/python against a local OpenAI-compatible server (Metal).

  hunyuan  HunyuanOCR 1B Q8_0 on llama-server; the model card's document-parsing prompt (Markdown, tables as HTML)
  glmocr   GLM-OCR's own SDK pipeline: PP-DocLayoutV3 layout, then GLM-OCR per region (text / table / formula task
           prompts), model served by mlx_vlm.server
  granite  Granite-Docling-258M (Apache-2.0) on mlx_vlm.server: page to DocTags, converted with docling_core
  qwen3vl  Qwen3-VL-8B-Instruct (Apache-2.0) on mlx_vlm.server with a plain "markdown, tables as HTML" prompt
  lighton  LightOnOCR-2-1B (Apache-2.0) on mlx_vlm.server, its native image-to-text mode
  chandra  Chandra OCR 2 through the chandra-ocr package (ocr_layout prompt, its HTML parser), served by mlx_vlm.server

Headers and footers are kept wherever the pipeline lets us choose: on an invoice they hold the seller and the totals.
Usage: run_vlm.py ENGINE --server URL --model NAME_OR_PATH
"""
import argparse, base64, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import html_tables, image_path, md_tables, run, to_text  # noqa: E402

HUNYUAN_PARSE = '提取文档图片中正文的所有信息用markdown格式表示，其中页眉、页脚部分忽略，表格用html格式表达，文档中公式用latex格式表示，按照阅读顺序组织进行解析。'
HUNYUAN_JUNK = re.compile(r'<｜hy_[^｜]*｜>(\(\d+,\d+\),\(\d+,\d+\))?')  # special tokens, as stripped in invoice_extractor/ingest.py


def regions_from(obj, width, height):
    """Every dict with a label and a box anywhere in a nested result; boxes on a 0-1000 grid become pixels."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            box = o.get('bbox_2d') or o.get('bbox')
            if box and len(box) == 4 and ('label' in o or 'native_label' in o):
                out.append(dict(bbox=[float(v) for v in box], label=str(o.get('label') or o.get('native_label')), content=o.get('content') or ''))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    if out and max(max(r['bbox']) for r in out) <= 1000 and max(width, height) > 1100:
        for r in out:
            x0, y0, x1, y1 = r['bbox']
            r['bbox'] = [x0 * width / 1000, y0 * height / 1000, x1 * width / 1000, y1 * height / 1000]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('engine', choices=['hunyuan', 'glmocr', 'chandra', 'granite', 'qwen3vl', 'lighton'])
    ap.add_argument('--server', required=True)
    ap.add_argument('--model', required=True)
    a = ap.parse_args()

    if a.engine == 'hunyuan':
        from openai import OpenAI
        client = OpenAI(base_url=a.server, api_key='none', timeout=600)

        def fn(item):
            data = base64.b64encode(open(image_path(item), 'rb').read()).decode()
            # repeat penalty 1.08 as in invoice_extractor/ingest.py: without it the 0.5B decoder can loop on long tables
            r = client.chat.completions.create(model=a.model, temperature=0, max_tokens=8192, extra_body={'repeat_penalty': 1.08}, messages=[{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{data}'}}, {'type': 'text', 'text': HUNYUAN_PARSE}]}])
            out = HUNYUAN_JUNK.sub('', r.choices[0].message.content or '')
            return dict(text=to_text(out), markdown=out, tables=html_tables(out) + md_tables(out), regions=[],
                        tokens=getattr(r.usage, 'completion_tokens', None))

    elif a.engine == 'glmocr':
        from urllib.parse import urlparse
        import glmocr
        import yaml
        u = urlparse(a.server)
        # The SDK's default config abandons headers, footers, footnotes and page numbers; on an invoice the header holds
        # the seller and the footer the bank and tax details, so they are read as text here. Longer timeout: the model
        # shares the GPU with other runs.
        cfg = yaml.safe_load(open(os.path.join(os.path.dirname(glmocr.__file__), 'config.yaml')))
        lm = cfg['pipeline']['layout']['label_task_mapping']
        keep = [l for l in lm['abandon'] if l in ('header', 'footer', 'number', 'footnote', 'aside_text')]
        lm['text'] += keep
        lm['abandon'] = [l for l in lm['abandon'] if l not in keep]
        cfg['pipeline']['maas']['enabled'] = False
        cfg['pipeline']['ocr_api'].update(api_host=u.hostname, api_port=u.port, model=a.model, request_timeout=600)
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'work', 'bench', 'glmocr-config.yaml')
        yaml.safe_dump(cfg, open(path, 'w'))
        parser = glmocr.GlmOcr(config_path=path, mode='selfhosted', model=a.model, ocr_api_host=u.hostname, ocr_api_port=u.port, layout_device='cpu')

        def fn(item):
            r = parser.parse(image_path(item))
            r = r[0] if isinstance(r, list) else r
            md = r.markdown_result or ''
            regs = regions_from(r.json_result, item['width'], item['height'])
            tables = []
            for reg in regs:
                if '<table' in reg['content'].lower():
                    tables += html_tables(reg['content'])
                elif reg['label'] == 'table':
                    tables += md_tables(reg['content'])
            if not tables:
                tables = html_tables(md) + md_tables(md)
            return dict(text=to_text('\n\n'.join(r_['content'] for r_ in regs) or md), markdown=md, tables=tables,
                        regions=[dict(bbox=r_['bbox'], label=r_['label']) for r_ in regs])

    elif a.engine in ('granite', 'qwen3vl', 'lighton'):
        from openai import OpenAI
        from PIL import Image
        client = OpenAI(base_url=a.server, api_key='none', timeout=900)
        prompt = {'granite': 'Convert this page to docling.', 'lighton': None,
                  'qwen3vl': 'Convert this document image to Markdown in reading order. Write every table as an HTML <table> with rowspan and colspan '
                             'for merged cells. Copy text exactly as printed; do not add, translate or correct anything. Output only the content.'}[a.engine]

        def fn(item):
            data = base64.b64encode(open(image_path(item), 'rb').read()).decode()
            parts = [{'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{data}'}}] + ([{'type': 'text', 'text': prompt}] if prompt else [])
            r = client.chat.completions.create(model=a.model, temperature=0, max_tokens=8192, messages=[{'role': 'user', 'content': parts}])
            out = r.choices[0].message.content or ''
            if a.engine == 'granite':
                from docling_core.types.doc.document import DocTagsDocument, DoclingDocument
                dt = DocTagsDocument.from_doctags_and_image_pairs([out], [Image.open(image_path(item)).convert('RGB')])
                doc = DoclingDocument.load_from_doctags(dt, document_name='page')
                tables = [t for tb in doc.tables for t in html_tables(tb.export_to_html(doc=doc))]
                md = doc.export_to_markdown()
                return dict(text=to_text(md), markdown=md, tables=tables, regions=[], doctags=out, tokens=getattr(r.usage, 'completion_tokens', None))
            return dict(text=to_text(out), markdown=out, tables=html_tables(out) + md_tables(out), regions=[], tokens=getattr(r.usage, 'completion_tokens', None))

    else:
        os.environ['VLLM_API_BASE'] = a.server
        os.environ['VLLM_MODEL_NAME'] = a.model
        from PIL import Image
        from chandra.model import InferenceManager
        from chandra.model.schema import BatchInputItem
        manager = InferenceManager(method='vllm')

        def fn(item):
            img = Image.open(image_path(item)).convert('RGB')
            out = manager.generate([BatchInputItem(image=img, prompt_type='ocr_layout')], include_headers_footers=True, vllm_api_base=a.server)[0]
            if out.error:
                raise RuntimeError('chandra generation error')
            chunks = out.chunks if isinstance(out.chunks, list) else out.chunks.get('blocks', []) if isinstance(out.chunks, dict) else []
            regs = [dict(bbox=[float(v) for v in c['bbox']], label=str(c.get('label', ''))) for c in chunks if isinstance(c, dict) and c.get('bbox')]
            return dict(text=to_text(out.html), markdown=out.markdown, tables=html_tables(out.html), regions=regs, tokens=out.token_count)

    run(a.engine, fn)


if __name__ == '__main__':
    main()
