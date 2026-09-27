import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import { useSpan } from "../api/hooks";
import { authHeaders } from "../lib/auth";
import { useUi } from "../store/ui";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

export function EvidenceChip({ spanId, stance = "supporting", label }: { spanId: string; stance?: string; label?: string }) {
  const open = useUi((s) => s.openSource);
  const { data } = useSpan(spanId);
  return (
    <button
      className={`evidence-chip ${stance === "contradicting" ? "contradicting" : ""}`}
      onClick={() => open(spanId)}
      title={data ? `“${data.quote}” — ${data.document_title}, p. ${data.page_number}` : "Open source"}
    >
      {stance === "contradicting" ? "⚠ contradicting" : "📄"} {label ?? (data ? `p.${data.page_number}` : "source")}
    </button>
  );
}

export function FieldEvidence({ spans }: { spans?: { span_id: string; stance: string }[] }) {
  if (!spans?.length) return <span className="no-evidence" title="No source span recorded for this field">no source</span>;
  return (
    <span className="row wrap" style={{ gap: 4, display: "inline-flex" }}>
      {spans.map((s) => <EvidenceChip key={s.span_id} spanId={s.span_id} stance={s.stance} />)}
    </span>
  );
}

export function SourceViewer() {
  const spanId = useUi((s) => s.sourceSpan);
  const close = useUi((s) => s.openSource);
  const { data: span, error } = useSpan(spanId);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [width, setWidth] = useState(800);
  const [pageSize, setPageSize] = useState<{ w: number; h: number } | null>(null);
  const [text, setText] = useState<string | null>(null);
  const isPdf = true;

  useEffect(() => {
    if (span) setPage(span.page_number);
  }, [span]);

  useEffect(() => {
    // Non-PDF sources (DOCX/text) show extracted page text with the span highlighted by offsets.
    if (!span) return;
    fetch(`/api/v1/document-versions/${span.document_version_id}/pages?page=${page}`)
      .then((r) => r.json())
      .then((rows) => setText(rows?.[0]?.text ?? null))
      .catch(() => setText(null));
  }, [span, page]);

  const fileUrl = span ? `/api/v1/document-versions/${span.document_version_id}/file` : null;
  const fileMemo = useMemo(() => (fileUrl ? { url: fileUrl, httpHeaders: authHeaders() } : null), [fileUrl]);
  const scale = pageSize ? width / pageSize.w : 1;
  const boxes = useMemo(() => (span && page === span.page_number ? ((span.bbox as number[][] | null) ?? []) : []), [span, page]);

  return (
    <Dialog.Root open={!!spanId} onOpenChange={(o) => !o && close(null)}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog" aria-describedby={undefined}>
          <header>
            <Dialog.Title asChild>
              <h2 className="grow">{span ? `${span.document_title} — page ${page}` : "Source"}</h2>
            </Dialog.Title>
            {span?.is_synthetic && <span className="badge synthetic">SYNTHETIC document</span>}
            {span && (
              <span className="muted small">
                {span.document_type} · published {span.publication_date ?? "unknown"} · academic year {span.academic_year ?? "not recorded"}
              </span>
            )}
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>◀</button>
            <button onClick={() => setPage((p) => Math.min(numPages || p, p + 1))} disabled={!!numPages && page >= numPages}>▶</button>
            <Dialog.Close asChild><button aria-label="Close source viewer">Close</button></Dialog.Close>
          </header>
          <div className="body" ref={(el) => { if (el) setWidth(Math.min(900, el.clientWidth - 30)); }}>
            {error && <p className="error">Could not load evidence: {String(error)}</p>}
            {span && (
              <>
                <blockquote className="quote">“{span.quote}”</blockquote>
                {!boxes.length && page === span.page_number && (
                  <p className="muted small">Exact coordinates are not available for this passage; the quoted text above is the evidence.</p>
                )}
              </>
            )}
            {fileUrl && isPdf && (
              <Document
                file={fileMemo}
                onLoadSuccess={(d) => setNumPages(d.numPages)}
                error={
                  <div>
                    <p className="muted">This source is not a PDF. Extracted text for this page:</p>
                    <pre style={{ whiteSpace: "pre-wrap" }}>{renderTextWithSpan(text, span?.page_number === page ? span : null)}</pre>
                  </div>
                }
              >
                <div className="pdf-page">
                  <Page
                    pageNumber={page}
                    width={width}
                    renderAnnotationLayer={false}
                    onLoadSuccess={(p) => setPageSize({ w: p.originalWidth, h: p.originalHeight })}
                  />
                  {boxes.map((b, i) => (
                    <div
                      key={i}
                      className="pdf-highlight"
                      style={{ left: b[0] * scale - 2, top: b[1] * scale - 2, width: (b[2] - b[0]) * scale + 4, height: (b[3] - b[1]) * scale + 4 }}
                    />
                  ))}
                </div>
              </Document>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function renderTextWithSpan(text: string | null, span: { char_start: number; char_end: number } | null) {
  if (!text) return "(no text)";
  if (!span) return text;
  return (
    <>
      {text.slice(0, span.char_start)}
      <mark>{text.slice(span.char_start, span.char_end)}</mark>
      {text.slice(span.char_end)}
    </>
  );
}
