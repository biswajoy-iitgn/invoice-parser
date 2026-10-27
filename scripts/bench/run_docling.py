"""Docling's standard pipeline, run with work/bench/venv/bin/python: Heron layout, TableFormer (accurate mode),
reading order, on the CPU.

  docling-easyocr    page image, EasyOCR for the text
  docling-tesseract  page image, Tesseract for the text
  docling-native     digital pages only: the PDF page itself, its text layer instead of OCR
Usage: run_docling.py VARIANT
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, html_tables, image_path, manifest, run, to_text  # noqa: E402


def main():
    variant = sys.argv[1]
    import fitz
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions, TableFormerMode, TesseractCliOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
    from docling_core.types.doc import ContentLayer

    # Page headers and footers are 'furniture' and left out of exports by default; on an invoice they hold the seller
    global LAYERS
    LAYERS = {ContentLayer.BODY, ContentLayer.FURNITURE}

    opts = PdfPipelineOptions()
    # CPU: Heron's RT-DETR builds float64 tensors, which MPS rejects (as found in invoice_extractor/ingest.py)
    opts.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU)
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = True
    if variant == 'docling-native':
        opts.do_ocr = False
    else:
        opts.do_ocr = True
        opts.ocr_options = (EasyOcrOptions(lang=['en'], force_full_page_ocr=True) if variant == 'docling-easyocr'
                            else TesseractCliOcrOptions(lang=['eng'], force_full_page_ocr=True))
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
                                             InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts)})

    def fn(item):
        if variant == 'docling-native':  # one page of the original PDF, so the text comes from its text layer
            src = fitz.open(os.path.join(ROOT, item['pdf']))
            one = fitz.open()
            one.insert_pdf(src, from_page=item['page'], to_page=item['page'])
            path = os.path.join(tempfile.gettempdir(), f"bench-{item['id']}.pdf")
            one.save(path)
        else:
            path = image_path(item)
        doc = conv.convert(path).document
        page = doc.pages[min(doc.pages)]
        scale = item['width'] / page.size.width  # page units to image pixels
        regions = []
        for it, _ in doc.iterate_items(included_content_layers=LAYERS):
            for p in getattr(it, 'prov', []) or []:
                b = p.bbox.to_top_left_origin(page.size.height)
                regions.append(dict(bbox=[b.l * scale, b.t * scale, b.r * scale, b.b * scale], label=str(it.label.value if hasattr(it.label, 'value') else it.label)))
        tables = [r for t in doc.tables for r in [html_tables(t.export_to_html(doc=doc))] if r]
        md = doc.export_to_markdown(included_content_layers=LAYERS)
        return dict(text=to_text(md), markdown=md, tables=[t for ts in tables for t in ts], regions=regions)

    items = manifest(condition=['digital']) if variant == 'docling-native' else manifest()
    run(variant, fn, items)


if __name__ == '__main__':
    main()
