import type { EpisodeEntry } from "../../api";
import { IconButton, Menu, MenuItem, MenuSeparator } from "../../ui";
import type { EpisodeAction } from "./useEpisodeActions";

export function EpisodeMenu({ entry, pending, onAction, onVoice }: {
  entry: EpisodeEntry; pending: boolean; onAction: (action: EpisodeAction) => void; onVoice: () => void;
}) {
  return <Menu label={`Ações de ${entry.title}`} trigger={props => <IconButton icon="more" aria-label={`Mais ações: ${entry.title}`} {...props} />}>
    {entry.url && <MenuItem icon="refresh" disabled={pending} onSelect={() => onAction("metadata")}>
      {entry.metadata_updated_at ? "Atualizar dados do YouTube" : "Buscar dados do YouTube"}
    </MenuItem>}
    {entry.url && !entry.source && <MenuItem icon="download" disabled={pending || !!entry.archived} onSelect={() => onAction("download")}>Baixar e guardar vídeo</MenuItem>}
    {entry.source && <MenuItem icon="mic" disabled={pending || !!entry.archived} onSelect={onVoice}>Identificar voz do protagonista</MenuItem>}
    <MenuSeparator />
    <MenuItem icon="archive" disabled={pending} onSelect={() => onAction("archive")}>{entry.archived ? "Desarquivar" : "Arquivar"}</MenuItem>
  </Menu>;
}
