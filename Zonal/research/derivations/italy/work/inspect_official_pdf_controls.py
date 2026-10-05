from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent / "raw" / "official_reports"
NEEDLES = (
    "60.330,4",
    "7.252,3",
    "23.294,0",
    "12,9 GWh",
    "5,6 GW",
    "7.244,0",
    "7.252,0",
    "7.220,9",
    "7.262,1",
    "accumulo",
    "Accumulo",
    "12,9",
    "5,6",
)

for file in sorted(ROOT.glob("*.pdf")):
    reader = PdfReader(file)
    hits = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        found = [needle for needle in NEEDLES if needle in text]
        if found:
            hits.append({"page": page_number, "found": found})
    print({"file": file.name, "pages": len(reader.pages), "hits": hits})
