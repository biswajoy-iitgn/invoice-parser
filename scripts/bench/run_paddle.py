"""PaddleOCR pipelines, run with ~/paddle-env/bin/python (Paddle runs on the CPU on a Mac).

  ppocr        PP-OCRv6 medium detection + recognition (text lines with boxes)
  ppstructure  PP-StructureV3: PP-DocLayout_plus-L layout, PP-OCRv5 server OCR, SLANeXt / SLANet+ tables, reading order
  paddlevl     PaddleOCR-VL 1.6: PP-DocLayoutV3 layout + the 0.9B VL recognizer, served by mlx_vlm.server (Metal)

Preprocessing (orientation, unwarping) stays off, as in each pipeline's default config, so boxes stay in page pixels.
Usage: run_paddle.py PIPELINE [--server http://127.0.0.1:8111/v1]
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import html_tables, image_path, md_tables, reading_order, run, to_text  # noqa: E402

import cv2  # noqa: E402
import faulthandler  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'invoice_extractor'))
from ppocr_worker import prepare  # noqa: E402

# OpenCV 5's KleidiCV resize segfaults on Apple Silicon for some page sizes and Paddle's crash handler then spins
# forever. As in ppocr_worker.py: one OpenCV thread, pages pre-sized by Pillow to multiples of 32, and Python's
# fault handler so a native crash exits instead of hanging.
cv2.setNumThreads(1)
faulthandler.enable(file=sys.stderr, all_threads=False)


def page(item):
    """The page as a padded BGR array, and the factor that maps its pixels back to the image's."""
    arr, _, _, scale = prepare(image_path(item), 2560)
    return arr, 1 / scale


def unscale(box, k):
    return [float(v) * k for v in box]


def get(r, k, default=None):
    try:
        return r[k]
    except (KeyError, TypeError, IndexError):
        return getattr(r, k, default)


def blocks(res):
    """parsing_res_list as [(label, bbox, content)] in the pipeline's reading order."""
    out = []
    for b in get(res, 'parsing_res_list', []) or []:
        label = get(b, 'block_label', get(b, 'label', ''))
        bbox = [float(v) for v in (get(b, 'block_bbox', get(b, 'bbox', [0, 0, 0, 0])) or [0, 0, 0, 0])[:4]]
        content = get(b, 'block_content', get(b, 'content', '')) or ''
        out.append((label, bbox, content))
    return out


def structured(res, k=1.0):
    """Text, tables and regions from a PP-StructureV3 or PaddleOCR-VL page result."""
    parts, tables, regions = [], [], []
    for label, bbox, content in blocks(res):
        regions.append(dict(bbox=unscale(bbox, k), label=label))
        if '<table' in content.lower():
            tables += html_tables(content)
        elif label == 'table':
            tables += md_tables(content)
        parts.append(content)
    md = get(res, 'markdown', {}) or {}
    markdown = get(md, 'markdown_texts', '') or ''
    return dict(text=to_text('\n\n'.join(parts)), markdown=markdown, tables=tables, regions=regions)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pipeline', choices=['ppocr', 'ppstructure', 'paddlevl'])
    ap.add_argument('--server', default='http://127.0.0.1:8111/v1')
    ap.add_argument('--model', help='model path the MLX server should serve (paddlevl)')
    a = ap.parse_args()

    if a.pipeline == 'ppocr':
        from paddleocr import PaddleOCR
        eng = PaddleOCR(text_detection_model_name='PP-OCRv6_medium_det', text_recognition_model_name='PP-OCRv6_medium_rec',
                        use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
                        text_det_limit_side_len=2560, text_det_limit_type='max', device='cpu')

        def fn(item):
            arr, k = page(item)
            r = eng.predict(arr)[0]
            boxes = [(*unscale(b, k), t) for b, t in zip(get(r, 'rec_boxes'), get(r, 'rec_texts')) if t.strip()]
            return dict(text=reading_order(boxes), tables=[], regions=[],
                        words=[dict(bbox=list(b[:4]), text=b[4]) for b in boxes])

    elif a.pipeline == 'ppstructure':
        from paddleocr import PPStructureV3
        # PP-OCRv6 for the text: the default PP-OCRv5 server recognizer drops the spaces in English lines
        # ("DACHSERINDIAPVT.LTD."); layout, tables and reading order stay as shipped.
        eng = PPStructureV3(text_detection_model_name='PP-OCRv6_medium_det', text_recognition_model_name='PP-OCRv6_medium_rec',
                            use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
                            use_seal_recognition=False, use_formula_recognition=False, use_chart_recognition=False, device='cpu')

        def fn(item):
            arr, k = page(item)
            return structured(eng.predict(arr)[0], k)

    else:
        from paddleocr import PaddleOCRVL
        # mlx_vlm.server lists every cached model, so the client must be told which one to ask for
        eng = PaddleOCRVL(pipeline_version='v1.6', vl_rec_backend='mlx-vlm-server', vl_rec_server_url=a.server, vl_rec_api_model_name=a.model,
                          use_doc_orientation_classify=False, use_doc_unwarping=False, device='cpu')

        def fn(item):
            arr, k = page(item)
            return structured(eng.predict(arr)[0], k)

    run(a.pipeline, fn)


if __name__ == '__main__':
    main()
