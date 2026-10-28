# Documents

Read the text of a PDF, Word (DOCX), Excel (XLSX), PowerPoint (PPTX), HTML, CSV, TSV, plain-text
or Markdown document an agent received, for example an email attachment saved with `gmail.get_attachment`. Parsing and inspection are the Runtime's
[untrusted document inspection](https://github.com/AlgenAI/algen-agent-runtime/blob/main/docs/document-security.md)
(`algen_agent_runtime.security.documents`); this connector reads the artifact for the calling
tenant, bounds the call and governs it as a tool.

| | |
| --- | --- |
| Plugin | none yet: the toolkit needs the deployment's artifact loader, so it is built in code |
| Extra | `pip install "algen-agent-tools[documents]"` for PDFs (installs `algen-agent-runtime[documents]`, which needs PyMuPDF, licensed AGPL-3.0); every other format uses only the standard library and needs no extra |
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
  sheet longer than 5,000 rows is read up to there and reported as a `document.truncated` finding
  (medium, so the disposition is at least `review`).
- `disposition`: `pass`, `review` or `quarantine`, and `findings` (kind, severity, message,
  page) for hidden text, instructions aimed at the model and risky Office packaging (macros,
  remote templates and other content loaded on open). The findings carry no excerpt of the
  suspicious text. For XLSX `page` is the sheet number, for PPTX the slide number.
- `page_count` (pages, sheets or slides), `image_count`, and `text_withheld`.

The format comes from the file name's suffix: `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.html`,
`.htm`, `.csv`, `.tsv`, `.txt` or `.md`. Any other suffix is refused. XLSX cells are joined with
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
