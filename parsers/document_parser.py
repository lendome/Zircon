"""Document text extraction for non-plaintext file types.

The ReadFileTool routes binary/documents through this module so the agent can
read PDFs, EPUBs, XPS, CBZ and Word (.docx) files as ordinary text instead of
treating them as unreadable blobs.

Backend is PyMuPDF (``fitz`` / MuPDF), which natively parses PDF, EPUB, XPS,
CBZ and MOBI. Rich-text Word files are handled by extracting the underlying
``word/document.xml`` so no extra dependency is required.

All functions are safe to call on plaintext too: ``classify`` returns
``"text"`` for anything that does not look like one of the supported binary
formats, and ``extract_text`` is a no-op pass-through in that case.
"""

import zipfile
import xml.etree.ElementTree as ET

# Extensions PyMuPDF can open directly.
_FITZ_EXTS = {"pdf", "epub", "xps", "cbz", "mobi"}

# Word documents are OOXML zip containers; map them to their inner text part.
_DOCX_EXTS = {"docx"}

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A conservative set of plaintext-ish extensions we should never route to the
# extractor, even if a cheap magic-byte sniff is ambiguous.
_TEXT_EXTS = {
    "txt", "md", "rst", "py", "js", "ts", "tsx", "jsx", "c", "h", "cpp",
    "hpp", "cc", "java", "go", "rs", "rb", "sh", "bash", "zsh", "ps1", "bat",
    "cmd", "sql", "json", "yaml", "yml", "toml", "ini", "cfg", "conf",
    "xml", "html", "htm", "css", "scss", "less", "csv", "tsv", "log", "diff",
    "patch", "env", "gitignore", "dockerfile", "lock", "toml", "properties",
}


def classify(path, content=None):
    """Return the extraction strategy for ``path``.

    Returns one of:
      - ``"text"``      : treat as plaintext, no extraction needed
      - ``"fitz"``      : extract with PyMuPDF (pdf/epub/xps/cbz/mobi)
      - ``"docx"``      : extract from the OOXML ``word/document.xml``
      - ``""`` (empty)  : unsupported binary/unknown, read as-is (hex/error)
    """
    ext = _extension(path)
    if ext in _TEXT_EXTS:
        return "text"
    if ext in _FITZ_EXTS:
        return "fitz"
    if ext in _DOCX_EXTS:
        return "docx"
    # Unknown extension: sniff the leading bytes of the payload so a PDF with a
    # non-standard extension (or an extensionless file) is still handled.
    if content:
        head = content[:512]
        if head.lstrip().startswith(b"%PDF"):
            return "fitz"
        # ZIP magic: could be .docx, .epub, .cbz, .xps (all zip containers).
        if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
            inner = _peek_zip_type(content)
            if inner in _FITZ_EXTS:
                return "fitz"
            if inner == "docx":
                return "docx"
    return ""


def extract_text(kind, path, content):
    """Return ``(text, meta)`` for the file identified by ``kind``.

    ``kind`` is the value produced by :func:`classify`. For ``"text"`` the
    content is returned untouched; for binary kinds the extracted text and a
    small metadata dict (page count, format, source) are returned.
    """
    if kind == "text":
        return _decode_text(content), _plain_meta(path)
    if kind == "fitz":
        return _extract_fitz(path, content)
    if kind == "docx":
        return _extract_docx(content)
    raise ValueError(f"unknown document kind: {kind!r}")


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------

def _extension(path):
    name = str(path).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _peek_zip_type(content):
    """Inspect a zip container's members to guess whether it's docx/epub/cbz."""
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(content)) as zf:
            names = zf.namelist()
    except Exception:
        return ""
    low = [n.lower() for n in names]
    if "word/document.xml" in low:
        return "docx"
    if any(n.startswith("epub/") or n.startswith("mimetype") for n in low):
        return "epub"
    if any(n.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")) for n in low):
        return "cbz"
    return ""


def _decode_text(content):
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _plain_meta(path):
    return {"format": "text", "source": str(path)}


def _extract_fitz(path, content):
    import fitz  # imported lazily so plaintext reads never pay the import cost

    stream = content or str(path)
    doc = fitz.open(stream=stream, filetype=None) if content else fitz.open(path)
    try:
        fmt = doc.metadata.get("format", "document")
        pages = []
        for page in doc:
            pages.append(page.get_text("text"))
        text = "\n\n".join(pages).strip()
    finally:
        doc.close()
    meta = {
        "format": fmt,
        "pages": len(pages),
        "source": str(path),
    }
    return text, meta


def _extract_docx(content):
    """Pull the visible text and tables out of a .docx's document.xml."""
    buf = __import__("io").BytesIO(content)
    try:
        with zipfile.ZipFile(buf) as zf:
            xml_bytes = zf.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile):
        return "", {"format": "docx", "error": "missing word/document.xml", "source": ""}

    root = ET.fromstring(xml_bytes)
    lines = []
    for para in root.iter(_WORD_NS + "p"):
        runs = [node.text for node in para.iter(_WORD_NS + "t") if node.text]
        # A tab or break creates a column/line break; drop empty paragraphs.
        line = "".join(runs).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines).strip()
    meta = {"format": "docx", "paragraphs": len(lines), "source": ""}
    return text, meta
