from __future__ import annotations

import csv
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from wardlens.models import PatientBundle, PatientSummary
from wardlens.services.summary import extract_top_todos, recent_reports

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
ET.register_namespace("w", W_NS)


def _w(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def _clean_xml_text(value: object) -> str:
    text = str(value or "")
    return "".join(
        character
        for character in text
        if character in "\t\n\r"
        or 0x20 <= ord(character) <= 0xD7FF
        or 0xE000 <= ord(character) <= 0xFFFD
        or 0x10000 <= ord(character) <= 0x10FFFF
    )


def _append_paragraph(
    parent: ET.Element,
    text: object,
    *,
    bold: bool = False,
    italic: bool = False,
    size: int = 16,
) -> ET.Element:
    paragraph = ET.SubElement(parent, _w("p"))
    paragraph_properties = ET.SubElement(paragraph, _w("pPr"))
    ET.SubElement(paragraph_properties, _w("spacing"), {_w("after"): "0"})
    run = ET.SubElement(paragraph, _w("r"))
    run_properties = ET.SubElement(run, _w("rPr"))
    if bold:
        ET.SubElement(run_properties, _w("b"))
    if italic:
        ET.SubElement(run_properties, _w("i"))
    ET.SubElement(run_properties, _w("sz"), {_w("val"): str(size)})
    ET.SubElement(run_properties, _w("szCs"), {_w("val"): str(size)})
    node = ET.SubElement(run, _w("t"), {f"{{{XML_NS}}}space": "preserve"})
    node.text = _clean_xml_text(text)
    return paragraph


def _append_cell(row: ET.Element, value: object, *, bold: bool = False) -> None:
    cell = ET.SubElement(row, _w("tc"))
    cell_properties = ET.SubElement(cell, _w("tcPr"))
    ET.SubElement(cell_properties, _w("tcW"), {_w("w"): "2500", _w("type"): "pct"})
    lines = _clean_xml_text(value).splitlines() or [""]
    for line in lines:
        _append_paragraph(cell, line, bold=bold)


def _document_xml(
    patients: list[PatientSummary],
    bundles: dict[str, PatientBundle],
    doctor_id: str,
) -> bytes:
    document = ET.Element(_w("document"))
    body = ET.SubElement(document, _w("body"))
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    title = f"WardLens 查房清單｜機密醫療資料｜{timestamp}"
    if doctor_id:
        title += f"｜醫師燈號 {_clean_xml_text(doctor_id)}"
    _append_paragraph(body, title, bold=True, size=22)
    _append_paragraph(
        body,
        "內容為唯讀擷取／AI 草稿輔助；請逐項核對院內原始病歷。",
        italic=True,
    )

    table = ET.SubElement(body, _w("tbl"))
    table_properties = ET.SubElement(table, _w("tblPr"))
    ET.SubElement(table_properties, _w("tblStyle"), {_w("val"): "TableGrid"})
    ET.SubElement(table_properties, _w("tblW"), {_w("w"): "5000", _w("type"): "pct"})
    grid = ET.SubElement(table, _w("tblGrid"))
    for width in (2200, 2900, 2900, 1200):
        ET.SubElement(grid, _w("gridCol"), {_w("w"): str(width)})

    header = ET.SubElement(table, _w("tr"))
    for value in ("病人資料", "Admission TODO", "近期／Pending 報告", "擷取狀態"):
        _append_cell(header, value, bold=True)

    for patient in patients:
        row = ET.SubElement(table, _w("tr"))
        identity = "\n".join(
            value
            for value in (
                patient.location,
                patient.name,
                f"MRN {patient.histno}",
                " ".join(part for part in (patient.age, patient.sex) if part),
            )
            if value
        )
        bundle = bundles.get(patient.local_key)
        if bundle is None:
            todo = "尚未載入；不可解讀為無待辦。"
            reports = "尚未載入；不可解讀為無新報告。"
            status = "只有基本清單"
        else:
            todo = "\n".join(extract_top_todos(bundle)) or "未辨識，請核對原文。"
            reports = "\n".join(recent_reports(bundle)) or "未取得報告索引。"
            status = (
                f"截止 {bundle.fetched_at.astimezone().strftime('%H:%M')}\n"
                f"來源 {len(bundle.records)}｜警告 {len(bundle.warnings)}"
            )
        for value in (identity, todo, reports, status):
            _append_cell(row, value)

    section = ET.SubElement(body, _w("sectPr"))
    ET.SubElement(
        section,
        _w("pgSz"),
        {_w("w"): "15840", _w("h"): "12240", _w("orient"): "landscape"},
    )
    ET.SubElement(
        section,
        _w("pgMar"),
        {
            _w("top"): "648",
            _w("right"): "504",
            _w("bottom"): "648",
            _w("left"): "504",
            _w("header"): "360",
            _w("footer"): "360",
            _w("gutter"): "0",
        },
    )
    return ET.tostring(document, encoding="utf-8", xml_declaration=True)


def _core_xml() -> bytes:
    namespaces = {
        "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
        "dc": "http://purl.org/dc/elements/1.1/",
        "dcterms": "http://purl.org/dc/terms/",
        "dcmitype": "http://purl.org/dc/dcmitype/",
        "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    }
    for prefix, uri in namespaces.items():
        ET.register_namespace(prefix, uri)
    root = ET.Element(f"{{{namespaces['cp']}}}coreProperties")
    ET.SubElement(root, f"{{{namespaces['dc']}}}title").text = "WardLens patient list"
    ET.SubElement(root, f"{{{namespaces['dc']}}}creator").text = "WardLens"
    created = ET.SubElement(
        root,
        f"{{{namespaces['dcterms']}}}created",
        {f"{{{namespaces['xsi']}}}type": "dcterms:W3CDTF"},
    )
    created.text = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""

ROOT_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""

DOCUMENT_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
"""

STYLES_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Aptos" w:eastAsia="Microsoft JhengHei" w:hAnsi="Aptos"/><w:sz w:val="16"/><w:szCs w:val="16"/></w:rPr></w:rPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
  <w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:color="auto"/><w:left w:val="single" w:sz="4" w:color="auto"/><w:bottom w:val="single" w:sz="4" w:color="auto"/><w:right w:val="single" w:sz="4" w:color="auto"/><w:insideH w:val="single" w:sz="4" w:color="auto"/><w:insideV w:val="single" w:sz="4" w:color="auto"/></w:tblBorders></w:tblPr></w:style>
</w:styles>
"""

APP_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>WardLens</Application><AppVersion>0.3</AppVersion>
</Properties>
"""


def export_patient_list_csv(patients: list[PatientSummary], path: Path) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Ward", "Bed", "Name", "HistNo", "Age", "Sex", "CaseNo"])
        for patient in patients:
            writer.writerow(
                [
                    patient.ward,
                    patient.bed,
                    patient.name,
                    patient.histno,
                    patient.age,
                    patient.sex,
                    patient.case_no,
                ]
            )
    return path


def export_patient_list_docx(
    patients: list[PatientSummary],
    bundles: dict[str, PatientBundle],
    path: Path,
    *,
    doctor_id: str = "",
) -> Path:
    parts = {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": ROOT_RELS,
        "docProps/core.xml": _core_xml(),
        "docProps/app.xml": APP_XML,
        "word/document.xml": _document_xml(patients, bundles, doctor_id),
        "word/styles.xml": STYLES_XML,
        "word/_rels/document.xml.rels": DOCUMENT_RELS,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as document:
        for name, content in parts.items():
            document.writestr(name, content)
    return path
