import { Alert, Button, Card } from "./ui";
import { useAction } from "../hooks/useApi";
import { downloadFile } from "../services/api";

/**
 * Downloads of the bulk question import templates (Excel and Word). Both files carry their own column instructions and filled-in
 * examples; the server serves them only to staff who may add questions (GET /api/v1/bulk-import-templates/{xlsx|docx}).
 */
export function ImportTemplateDownloads() {
  const dl = useAction();
  return (
    <Card>
      <h3>Bulk question import templates</h3>
      <p className="muted small">
        Excel is best for large sets (up to 100,000 questions); Word suits smaller, formatted sets (up to 5,000). Each template explains
        every column and shows examples: text only, text with a picture, picture only, and options as pictures. Choose the subject when you
        upload; marks are set later, in the test.
      </p>
      <div className="row">
        <Button variant="secondary" busy={dl.busy}
          onClick={() => void dl.run(() => downloadFile("/api/v1/bulk-import-templates/xlsx", "question-import-template.xlsx"))}>
          Download Excel template (.xlsx)
        </Button>
        <Button variant="secondary" busy={dl.busy}
          onClick={() => void dl.run(() => downloadFile("/api/v1/bulk-import-templates/docx", "question-import-template.docx"))}>
          Download Word template (.docx)
        </Button>
      </div>
      {dl.error && <Alert kind="error">{dl.error}</Alert>}
    </Card>
  );
}
