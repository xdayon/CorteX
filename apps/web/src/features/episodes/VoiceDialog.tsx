import type { EpisodeEntry } from "../../api";
import { Button, Dialog } from "../../ui";
import { VoiceSelector } from "../../VoiceSelector";

export function VoiceDialog({ entry, onClose }: { entry: EpisodeEntry; onClose: () => void }) {
  return <Dialog title="Identificar voz do protagonista" onClose={onClose} footer={<Button onClick={onClose}>Fechar</Button>}>
    <VoiceSelector entry={entry} disabled={!!entry.archived} autoOpen />
  </Dialog>;
}
