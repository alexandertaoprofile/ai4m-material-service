#!/usr/bin/env python3
"""Extract explicit composition strings from local manufacturer PDFs to review.

This is deliberately conservative: it preserves raw value strings and page
locators, does not infer a balance element, and emits review-only candidates.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from pypdf import PdfReader


ELEMENT_NAMES = {
    "nickel": "Ni", "chromium": "Cr", "cobalt": "Co", "iron": "Fe",
    "molybdenum": "Mo", "titanium": "Ti", "aluminum": "Al", "carbon": "C",
    "boron": "B", "zirconium": "Zr", "manganese": "Mn", "silicon": "Si",
    "niobium": "Nb", "tantalum": "Ta", "tungsten": "W", "copper": "Cu",
    "phosphorus": "P", "sulfur": "S", "hafnium": "Hf", "rhenium": "Re",
}


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def parse_value(raw: str):
    raw = re.sub(r"\s+", " ", raw).strip(" .")
    normalized = raw.replace("–", "-")
    if re.fullmatch(r"balance\*?", normalized, re.I):
        return "", "", "", "balance"
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", normalized)
    if match: return match.group(1), match.group(2), "", "range"
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:max\.?|maximum)", normalized, re.I)
    if match: return "", match.group(1), "", "maximum"
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:min\.?|minimum)", normalized, re.I)
    if match: return match.group(1), "", "", "minimum"
    match = re.match(r"(\d+(?:\.\d+)?)\s*(?:balance|bal\.?)", normalized, re.I)
    if match: return "", "", match.group(1), "balance_after_nominal"
    match = re.fullmatch(r"(\d+(?:\.\d+)?)", normalized)
    if match: return "", "", match.group(1), "nominal"
    # Some PDF text layers concatenate a table heading or footnote after an
    # otherwise unambiguous leading value.  Retain the full raw string, but
    # classify only the leading, explicitly qualified number.
    match = re.match(r"(\d+(?:\.\d+)?)\s*(?:max\.?|maximum)\b", normalized, re.I)
    if match: return "", match.group(1), "", "maximum_with_trailing_source_text"
    match = re.match(r"(\d+(?:\.\d+)?)\s*(?:min\.?|minimum)\b", normalized, re.I)
    if match: return match.group(1), "", "", "minimum_with_trailing_source_text"
    match = re.match(r"(\d+(?:\.\d+)?)\s*(?:\(\s*balance\s*\)|balance)", normalized, re.I)
    if match: return "", "", match.group(1), "balance_after_nominal_with_trailing_source_text"
    match = re.match(r"(\d+(?:\.\d+)?)\b", normalized)
    if match: return "", "", match.group(1), "nominal_with_trailing_source_text"
    return "", "", "", "unparsed_keep_raw"


def composition_pages(pdf: PdfReader):
    pages=[]
    for index,page in enumerate(pdf.pages, start=1):
        text=page.extract_text() or ""
        if re.search(r"nominal composition|chemical composition|limiting chemical composition|composition is shown", text, re.I):
            pages.append((index,text))
    return pages


def extract_pairs(text: str):
    # Manufacturer PDFs use either ``Element: value`` or a dot-leader layout.
    result=[]
    compact=re.sub(r"\s+", " ", text)
    for name, symbol in ELEMENT_NAMES.items():
        name_pattern = rf"{name}(?:\s*\([^)]*\))?"
        next_name_pattern = rf"(?:{'|'.join(ELEMENT_NAMES)})(?:\s*\([^)]*\))?(?:\s*\d+)?"
        patterns=[
            rf"\b{name_pattern}\s*:\s*([^:\n]{{1,48}}?)(?=\s+(?:{'|'.join(ELEMENT_NAMES)})\s*:|$)",
            rf"\b{name_pattern}\s*(?:\.\s*){{3,}}\s*(.+?)(?=\s+{next_name_pattern}\s*(?:\.\s*){{3,}}|$)",
        ]
        for pattern in patterns:
            match=re.search(pattern, compact, re.I)
            if not match: continue
            value=match.group(1)
            # Do not let explanatory prose become a fake numeric composition.
            if not re.search(r"\d|balance|bal\.", value, re.I): continue
            value=re.split(r"\s+(?:TTaabbllee|Table|Weight %|Publication No\.|Publication Number|Copyright|Density|Physical Constants)", value, flags=re.I)[0].strip()
            result.append((symbol,value)); break
    return result


def write(path, fields, rows):
    with path.open("w",encoding="utf-8",newline="") as f:
        out=csv.DictWriter(f,fieldnames=fields);out.writeheader();out.writerows(rows)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--coverage',type=Path,required=True)
    ap.add_argument('--registry',type=Path,required=True)
    ap.add_argument('--output-dir',type=Path,required=True)
    ap.add_argument('--all-registry-nickel-documents', action='store_true',
                    help='Extract every local registry document whose family is nickel based; useful for the research-only expansion layer.')
    a=ap.parse_args()
    if a.output_dir.exists(): raise SystemExit(f"refusing to overwrite existing output: {a.output_dir}")
    registry={x.get('material_id'):x for x in json.loads(a.registry.read_text())['documents']}
    output=[]; pages_report=[]
    if a.all_registry_nickel_documents:
        records=[]
        for material_id, doc in registry.items():
            family=doc.get('family','').casefold()
            if 'nickel' in family and 'titanium' not in family:
                records.append({'material_id':material_id,'display_name':doc.get('display_name','')})
    else:
        records=[r for r in read_csv(a.coverage) if r['composition_status'] == 'missing_structured_composition']
    for record in records:
        doc=registry.get(record['material_id'])
        if not doc: continue
        path=Path(doc['source_pdf'])
        pdf=PdfReader(str(path)); pages=composition_pages(pdf)
        page_numbers=[]; emitted=0
        for page_number,text in pages:
            pairs=extract_pairs(text)
            if not pairs: continue
            page_numbers.append(str(page_number))
            for element, raw_value in pairs:
                lo,hi,nom,kind=parse_value(raw_value)
                output.append({
                    'material_id':record['material_id'],'display_name':record['display_name'],
                    'element':element,'minimum_wt_percent':lo,'maximum_wt_percent':hi,'nominal_wt_percent':nom,
                    'raw_value_text':raw_value,'value_kind':kind,'basis':'wt.% as stated by source',
                    'source_pdf':str(path),'source_document_id':doc['lineage_document_id'],
                    'source_page':page_number,'source_section':'manufacturer composition page',
                    'admission':'review_only_pdf_extraction','blocker':'Verify all elements, balance convention, and exact grade/state against rendered source page before training admission.',
                }); emitted+=1
        pages_report.append({'material_id':record['material_id'],'display_name':record['display_name'],'source_pdf':str(path),'composition_pages_found':';'.join(page_numbers),'component_candidates':emitted,'status':'review_ready' if emitted else 'manual_pdf_review_required'})
    a.output_dir.mkdir(parents=True)
    write(a.output_dir/'pdf_composition_review_candidates.csv',list(output[0]) if output else [],output)
    write(a.output_dir/'pdf_composition_extraction_audit.csv',list(pages_report[0]) if pages_report else [],pages_report)
    summary={'target_grades':len(pages_report),'component_candidates':len(output),'grades_with_candidates':sum(1 for r in pages_report if int(r['component_candidates'])>0),'policy':'PDF-derived data is review-only until a reviewer compares it to the rendered manufacturer page and approves exact grade/state linkage.'}
    (a.output_dir/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
