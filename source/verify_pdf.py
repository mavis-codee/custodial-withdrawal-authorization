from pathlib import Path
from pypdf import PdfReader
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent
PDF = ROOT.parent / "output/pdf/Custodial_Withdrawal_Authorization_Paper/Custodial_Withdrawal_Authorization_zh.pdf"
r = PdfReader(PDF)
fonts, missing_fonts, links = {}, [], []
for page in r.pages:
    for font in page["/Resources"]["/Font"].values():
        f = font.get_object()
        name = str(f.get("/BaseFont"))
        fd = f.get("/FontDescriptor")
        embedded = bool(fd and any(fd.get_object().get(k) for k in ("/FontFile", "/FontFile2", "/FontFile3")))
        fonts[name] = embedded
        if not embedded:
            missing_fonts.append(name)
    for annot in page.get("/Annots", []):
        a = annot.get_object().get("/A")
        if a and a.get("/URI"):
            links.append(str(a["/URI"]))

parts = []
for page in r.pages:
    def visitor(text, cm, tm, font, size):
        y = cm[5] + tm[5]
        if 42 < y < 800:
            parts.append(text)
    page.extract_text(visitor_text=visitor)
raw = "".join(parts)
def norm(t):
    return re.sub(r"\s+", "", t.replace("V_U", "VU").replace("V_L", "VL"))
whole = norm(raw)
layout = json.loads((ROOT / "layout.json").read_text(encoding="utf-8"))
missing_text = []
overflow = []
checked = set()
for row in layout:
    plain = row.get("text")
    if plain and plain not in checked:
        checked.add(plain)
        if norm(plain) not in whole:
            missing_text.append(plain)
    if row["x"] < 62 or row["x"] + row["width"] > 534:
        overflow.append(row)
    if row["y"] < 48 or row["y"] + row["height"] > 795:
        overflow.append(row)
models = json.loads((ROOT / "model_results.json").read_text(encoding="utf-8"))
assert models["model_sha256"] == hashlib.sha256((ROOT / "model_check.py").read_bytes()).hexdigest()
paper = (ROOT / "paper.md").read_text(encoding="utf-8")
assert "112" in paper and "8,496" in paper and models["total_transition_attempts"] == 8496
citations = set(int(x) for x in re.findall(r"\[(\d+)\]", paper))
assert citations == set(range(1, 13))
assert "kojitamaki223@gmail.com" in raw
assert "satoshinakamoto200831" not in raw
assert "\ufffd" not in raw and "\u25a0" not in raw
report = {"pdf_sha256": hashlib.sha256(PDF.read_bytes()).hexdigest(), "pages": len(r.pages),
          "embedded_fonts": fonts, "unembedded_fonts": sorted(set(missing_fonts)),
          "unique_text_blocks_checked": len(checked), "missing_text_blocks": missing_text,
          "out_of_bounds_blocks": overflow, "unique_external_links": sorted(set(links)),
          "references": 12, "model_results_match": True,
          "source_sha256": hashlib.sha256((ROOT / "paper.md").read_bytes()).hexdigest()}
(ROOT / "quality_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if k not in {"missing_text_blocks", "out_of_bounds_blocks", "embedded_fonts", "unique_external_links"}}, ensure_ascii=False, indent=2))
print("MISSING_TEXT", missing_text)
print("OUT_OF_BOUNDS", [(x["page"], x["x"], x["y"], x["width"], x["height"], str(x["text"])[:30]) for x in overflow])
print("FONTS", fonts)
print("LINKS", len(set(links)))
assert not missing_fonts, "Unembedded font resources"
assert not missing_text, "Text content missing from PDF"
assert not overflow, "Text outside publication frame"
assert len(set(links)) >= 14, "Missing reference/contact links"
