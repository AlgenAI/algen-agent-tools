# Documents

Read the text of a PDF, DOCX, plain-text or Markdown document an agent received, for example an
email attachment saved with `gmail.get_attachment`. Parsing and inspection are the Runtime's
[untrusted document inspection](https://github.com/AlgenAI/algen-agent-runtime/blob/main/docs/document-security.md)
(`algen_agent_runtime.security.documents`); this connector reads the artifact for the calling
tenant, bounds the call and governs it as a tool.

| | |
| --- | --- |
| Plugin | none yet: the toolkit needs the deployment's artifact loader, so it is built in code |
| Extra | `pip install "algen-agent-tools[documents]"` for PDFs (installs `algen-agent-runtime[documents]`, which needs PyMuPDF, licensed AGPL-3.0); DOCX, text and Markdown need no extra |
| Environment | none |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `documents.extract_text` | read | `documents.read` | 120 s | Runtime default | agent policy |

`documents.extract_text(document, max_chars=50000)` takes an artifact reference
(`artifact_id`, `filename`, `media_type`) and returns:

- `text`: only what a reader would see, cut to `max_chars` (at most 100,000; `truncated` says
  so). Hidden text (white, tiny, transparent or off-page in PDFs; hidden or invisible runs in
  DOCX) is never returned.
- `disposition`: `pass`, `review` or `quarantine`, and `findings` (kind, severity, message,
  page) for hidden text, instructions aimed at the model and risky DOCX packaging (macros,
  remote templates). The findings carry no excerpt of the suspicious text.
- `page_count`, `image_count`, and `text_withheld`.

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
  bound pages (PDF 50), characters (250,000), DOCX archive members, uncompressed size and
  compression ratio (zip bombs), and refuse DTDs.
- A file whose content does not match its name (a renamed executable as `.pdf`) is refused.
- Errors name the rule that refused the document, never its content.
- Parsing runs in a worker thread, so a large document does not block other calls.

## Testing

Pass any async `artifact_loader`, for example one that serves bytes from a dict. PDF tests need
the `documents` extra; DOCX and text tests need nothing.
