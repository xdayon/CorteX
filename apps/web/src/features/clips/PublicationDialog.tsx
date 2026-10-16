import type { ClipRow, Publication } from "../../api";
import { PublicationPanel } from "../../PublicationPanel";
import { Button, Dialog } from "../../ui";
import { headlineOf, newestExport } from "./clipModel";

export function PublicationDialog({ row, onSaved, onDeleted, onClose }: {
  row: ClipRow; onSaved: (item: Publication) => void; onDeleted: (artifactId: string) => void; onClose: () => void;
}) {
  const latest = newestExport(row);
  return <Dialog title="Registrar publicação" onClose={onClose} footer={<Button onClick={onClose}>Fechar</Button>}>
    <p className="field-help" style={{ marginBottom: 8 }}>{headlineOf(row)}</p>
    {latest && <PublicationPanel defaultOpen projectId={row.project_id} artifactId={latest.render_artifact_id} publication={row.publication}
      hookText={row.clip.hook?.summary?.slice(0, 200)} onSaved={onSaved} onDeleted={onDeleted} />}
  </Dialog>;
}
