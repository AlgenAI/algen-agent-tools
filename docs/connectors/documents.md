# Documents

Read the text of a PDF, Word (DOCX), Excel (XLSX), PowerPoint (PPTX), HTML, CSV, TSV, plain-text,
Markdown or email (EML, or Outlook MSG) document an agent received, for example an email attachment saved with `gmail.get_attachment`, or a file attached to the run. Parsing and inspection are this package's
[document inspection](#document-inspection) (`algen_agent_tools.documents.inspection`, moved from
the Runtime's `algen_agent_runtime.security` in 0.2.0a7); this connector reads the artifact for
the calling tenant, bounds the call and governs it as a tool.

| | |
| --- | --- |
| Plugin | none yet: the toolkit needs the deployment's artifact loader, so it is built in code |
| Extra | `pip install "algen-agent-tools[documents]"` for PDFs (installs PyMuPDF, licensed AGPL-3.0); every other format uses only the standard library and needs no extra. Optional `ocr` for Tesseract OCR (see [OCR](#ocr)) and `msg` for Outlook `.msg` files (olefile, BSD) |
| Environment | none |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `documents.extract_text` | read | `documents.read` | 120 s | Runtime default | agent policy |

`documents.extract_text(document, max_chars=50000)` takes an artifact reference
(`artifact_id`, `filename`, `media_type`) and returns:

- `text`: only what a reader would see, cut to `max_chars` (at most 100,000). Hidden text is
  never returned: white, tiny, transparent or off-page text in PDFs; hidden or invisible runs in
  DOCX; hidden sheets, rows and columns in XLSX; hidden slides and shapes in PPTX; and HTML hidden
  by the `hidden` attribute or an inline style (`display: none`, `visibility: hidden`, zero font
  size or opacity). HTML scripts, styles and the head, PPTX speaker notes and Office comments are
  not extracted.
- `truncated`: the text is incomplete, either cut to `max_chars` or cut by the inspection. An XLSX
  sheet longer than 5,000 rows is read up to there and reported as a `document.truncated` finding.
- `disposition`: `pass`, `review` or `quarantine`, and `findings` (kind, severity, message,
  page, category) for hidden text, instructions aimed at the model and risky Office packaging
  (macros, remote templates and other content loaded on open). The findings carry no excerpt of
  the suspicious text. For XLSX `page` is the sheet number, for PPTX the slide number.
- `coverage` and `needs_ocr`: how much was read. See [Unread content](#unread-content).
- `attachments`: for an email, the attachments that were read (file name, media type,
  disposition, coverage). A finding in an attachment names it in `part`. See [Email](#email).
- `page_count` (pages, sheets or slides), `image_count`, and `text_withheld`.

The format comes from the file name's suffix: `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.html`,
`.htm`, `.csv`, `.tsv`, `.txt`, `.md`, `.eml` or `.msg`. Any other suffix is refused. When the
content's own signature names another type, the content wins: a `scan.pdf` that is really a
JPEG is read as an image (with OCR), a `.docx` renamed `.doc` as Word, a PDF named `.txt` as a
PDF. Text formats have no signature, so they go by their name. Text, CSV, TSV, Markdown and HTML
are read as UTF-8 (a byte-order mark is dropped), as UTF-16 when they start with its byte-order
mark, and otherwise as Windows-1252, the encoding Excel uses for CSV exports.

A PDF longer than 50 pages is read up to page 50 and reports `document.pages_skipped` (coverage),
so the result is `partial` and `truncated` rather than refused. XLSX cells are joined with
` | `, one line per row, under a `--- Sheet: <name> ---` heading; a formula gives its last
calculated value and date or time cells come out in ISO form. CSV and TSV are read as plain text.

A quarantined document's text is withheld by default (`quarantine="withhold"`), so the model
sees the findings and can ask a person to review the file. Treat `quarantine` as a reason for
review, not a verdict: the instruction heuristic can match ordinary prose about guardrails.

## In code

```python
from algen_agent_tools.attachments import AttachmentRef
from algen_agent_tools.documents import documents_toolkit

async def load(reference: AttachmentRef, context: ToolContext) -> bytes:
    # Return the bytes only if the artifact belongs to context.tenant_id.
    return await artifacts.read(context.tenant_id, reference.artifact_id)

documents = documents_toolkit(artifact_loader=load, max_bytes=10_000_000)
```

Register it from your project's plugin with `documents.register(context)`, and enable
`documents.extract_text` with the `documents.read` permission.

## Guarantees

- The loader receives the call's `ToolContext`, so it can refuse another tenant's artifact.
- Documents larger than `max_bytes` are refused before parsing. The Runtime's extractors also
  bound pages (PDF 50), characters (250,000), XLSX rows per sheet (5,000), Office (DOCX, XLSX,
  PPTX) archive members, uncompressed size and compression ratio (zip bombs), and refuse DTDs.
- A file whose content does not match its name (a renamed executable as `.pdf`) is refused.
- Errors name the rule that refused the document, never its content.
- Parsing runs in a worker thread, so a large document does not block other calls.

## Email

An `.eml` file is read as a header block (From, To, Cc, Date, Subject) and its body, followed by
each attachment's text under an `[Attachment: <name>]` line. The body is the plain-text part, or
the HTML part (inspected like an HTML document) when there is none. Each attachment is read by the
extractor for its file name; an attached email is read the same way, at most two levels deep, and
at most 20 attachments are read in the whole message. An attachment's findings are the message's,
so hidden text or an instruction in an attachment quarantines the message.

An attachment that is past a limit, of an unsupported type or unreadable is a coverage finding
(`document.attachment_skipped` or `document.attachment_unread`, with the attachment in `part`),
so the message is `pass` with `coverage="partial"`, not refused. In code,
`EmailDocumentExtractor(max_depth=2, max_attachments=20, max_characters=250_000)` changes the
limits.

An Outlook `.msg` file (with the `msg` extra, `pip install "algen-agent-tools[msg]"`) is read the
same way: Tools reads its sender, recipients, date, subject, body and attachments from the file's
OLE streams with olefile, rebuilds the equivalent email and inspects that, so the limits and
findings are the same. Attached Outlook messages are read as attached emails. A message whose
only body is RTF reports `document.body_unread` (coverage), since RTF bodies are not read.

## Known limitations

These come from the Runtime's inspection; its
[document security](https://github.com/AlgenAI/algen-agent-runtime/blob/main/docs/document-security.md)
page lists them all. For example, an XLSX cell hidden only by the `;;;` number format or a font
colour that matches its fill, and HTML hidden by a stylesheet or by off-screen positioning, are
returned as visible text.

## Testing

Pass any async `artifact_loader`, for example one that serves bytes from a dict. PDF tests need
the `documents` extra; tests for every other format need nothing, and can build small DOCX, XLSX
and PPTX packages with `zipfile`.

To read the files attached to the run (`RunRequest.attachments`, Runtime 0.1.0a19), use the
Runtime-backed loader. It reads only the run's own attachments, for the run's tenant, and refuses
any other artifact id the model names:

```python
from algen_agent_tools.attachments import runtime_attachment_loader

documents = documents_toolkit(artifact_loader=runtime_attachment_loader)
```

## Document inspection

`algen_agent_tools.documents` re-exports the inspection's public names, so code that used the
Runtime's modules switches by changing the import:

```python
# Before (deprecated in algen-agent-runtime 0.1.0a19):
from algen_agent_runtime.security.documents import extractor_for
# Now:
from algen_agent_tools.documents import extractor_for, ExtractedDocument, DocumentDisposition

extracted = extractor_for("cv.docx").extract(data, filename="cv.docx")
```

`extractor_for(filename)` returns the extractor for the suffix; each returns an
`ExtractedDocument` with `text`, `sanitized_text` (what a reader would see), `spans`,
`findings` and a `disposition` (`pass`, `review` or `quarantine`). The extractor classes
(`PdfDocumentExtractor`, `DocxDocumentExtractor`, `XlsxDocumentExtractor`,
`PptxDocumentExtractor`, `HtmlDocumentExtractor`, `PlainTextDocumentExtractor`) and
`classify_findings`, `sanitize_untrusted_text`, `prompt_injection_findings` and `redact_pii` are
in `algen_agent_tools.documents.inspection`.

PDF, DOCX and PPTX documents with text and embedded images report `document.images_excluded`:
the images were not read. A document with images and no text reports `document.ocr_required`.

## Unread content

Content the inspection did not read is reported separately from security findings, so a caller
can tell "dangerous" from "not read":

| Finding kind | Means |
| --- | --- |
| `document.ocr_required` | Images or scanned pages hold text only OCR can read (`needs_ocr`) |
| `document.images_excluded` | Images next to text were not read |
| `document.ocr_no_text` | OCR ran and found no text |
| `document.truncated` | Rows past the sheet's row limit were not read |
| `document.attachment_skipped` | An email attachment past the attachment, depth or text limit was not read |
| `document.attachment_unread` | An email attachment of an unsupported type, or one that could not be parsed, was not read |
| `document.body_unread` | An Outlook message's body is RTF only and was not read |
| `document.pages_skipped` | Pages past the PDF page limit were not read |

These findings have `category="coverage"`; all others have `category="security"`. Only security
findings set the disposition, so a scanned ID card without OCR is `pass` with `coverage="none"`
and `needs_ocr=true`, and a cover note over a scan is `pass` with `coverage="partial"`. Hidden
text or an instruction aimed at the model still makes it `review` or `quarantine`.

`coverage` is `full` (everything was read), `partial` (some text was read, some content was not)
or `none` (content was found but no text could be read). `ExtractedDocument.unread` lists what
was not read: `image`, `page`, `rows`, `attachment` or `body`, with the page, slide or sheet and the
email attachment (`part`) when known.

`pass` with `coverage="none"` means nothing dangerous was found because nothing was read. The
application decides what that warrants: run OCR, ask a person, or file the document unread.

## OCR

OCR is off unless the host passes a recognizer. The `ocr` extra ships one for
[Tesseract](https://github.com/tesseract-ocr/tesseract):

```bash
pip install "algen-agent-tools[documents,ocr]"   # pytesseract and Pillow
apt install tesseract-ocr tesseract-ocr-deu       # or: brew install tesseract tesseract-lang
```

```python
from algen_agent_tools.documents import TesseractRecognizer, documents_toolkit

documents = documents_toolkit(
    artifact_loader=load, ocr=TesseractRecognizer(languages=("eng", "deu"), timeout=30)
)
```

`TesseractRecognizer` fails when it is built, naming the fix, if the extra, the `tesseract`
program or one of its languages is missing. It reads every frame of a multi-page TIFF or an
animated GIF (up to `max_frames`, default 64), each within `timeout` seconds, over a white
background for transparent images, and reports the mean word confidence. Installing the program
and its language data is the host's job; this package never runs one by default.

Any other engine plugs in the same way: a recognizer is any object with a `recognize` method.

```python
from algen_agent_tools.documents import RecognizedText, documents_toolkit, extractor_for

class MyRecognizer:
    def recognize(self, image: bytes, *, media_type: str) -> RecognizedText:
        text, confidence = my_engine.read(image)  # your OCR engine
        return RecognizedText(text=text, confidence=confidence)

documents = documents_toolkit(artifact_loader=load, ocr=MyRecognizer(), ocr_max_pages=3)
extracted = extractor_for("scan.pdf", ocr=MyRecognizer()).extract(data, filename="scan.pdf")
```

With a recognizer:

- Images (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tif`, `.tiff`) are accepted. Before
  the recognizer sees one, its content must match its suffix and the dimensions in its header
  must be within 40 megapixels (`ImageDocumentExtractor(max_pixels=...)`). Every frame of a GIF
  or TIFF counts toward that limit, and one with more than 64 frames is refused. The recognizer
  gets the original bytes and the image's media type.
- A PDF page that has images and fewer than 32 characters of text is rendered as a greyscale PNG
  at 200 DPI, scaled down to at most 12 megapixels, and recognized, for at most `ocr_max_pages`
  pages (default 3). Scanned pages past the limit report `document.ocr_required` with their page
  number. Images on pages with enough text are not read and report `document.images_excluded`.
  `PdfDocumentExtractor` takes `ocr_min_page_chars`, `ocr_dpi` and `ocr_max_pixels` to change
  these. A recognized page keeps the little text it had (a stamp or a page number), so that text
  can appear twice.
- Recognized text is a span with `source="ocr"` and the recognizer's `confidence`. It passes the
  same prompt-injection checks and sanitizing as any other text, so an instruction in a scanned
  page quarantines the document. An image or page with no recognized text reports
  `document.ocr_no_text` (low).
- The tool's description tells the model that images are read.
- The recognizer runs in the tool's worker thread, inside the tool's 120 s timeout. An exception
  it raises fails the call, unchanged (a missing OCR package is not reported as the `documents`
  extra).

Without a recognizer (the default) nothing changes: images are refused and nothing is rendered.
DOCX and PPTX images are not recognized yet.
