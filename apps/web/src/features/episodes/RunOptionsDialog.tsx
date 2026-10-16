import { useState } from "react";
import { Button, Dialog, Field, Slider, Switch, TextArea, TextInput } from "../../ui";
import type { RunOptions } from "./runOptions";

export function RunOptionsDialog({ options, onSave, onClose, confirmLabel = "Concluir", title = "Opções da seleção" }: {
  options: RunOptions; onSave: (options: RunOptions) => void; onClose: () => void; confirmLabel?: string; title?: string;
}) {
  const [draft, setDraft] = useState(options);
  const patch = (change: Partial<RunOptions>) => setDraft(current => ({ ...current, ...change }));
  return <Dialog title={title} onClose={onClose} footer={<>
    <Button variant="ghost" onClick={onClose}>Cancelar</Button>
    <Button variant="primary" onClick={() => onSave(draft)}>{confirmLabel}</Button>
  </>}>
    <Slider label="Quantidade de cortes" value={draft.count} min={1} max={25} onChange={count => patch({ count })} />
    <Slider label="Duração mínima" value={draft.minimum} min={15} max={180} suffix=" s" onChange={minimum => patch({ minimum, maximum: Math.max(draft.maximum, minimum) })} />
    <Slider label="Duração máxima" value={draft.maximum} min={15} max={180} suffix=" s" onChange={maximum => patch({ maximum, minimum: Math.min(draft.minimum, maximum) })} />
    <Field label="Protagonista dos cortes" hint="Orienta a IA; não reconhece a voz pelo nome">
      <TextInput value={draft.primarySubject} onChange={event => patch({ primarySubject: event.target.value })} />
    </Field>
    <Field label="Nomes e termos do episódio" hint="Opcional · separe por vírgula">
      <TextInput value={draft.hotwordsText} placeholder="Ex.: nome do convidado, termos técnicos" onChange={event => patch({ hotwordsText: event.target.value })} />
    </Field>
    <p className="field-help" style={{ marginTop: -6, marginBottom: 14 }}>Ajuda a transcrição a escrever certo nomes próprios e termos difíceis.</p>
    <Field label="Direção editorial" hint="Enviada à IA para escolher os trechos">
      <TextArea rows={7} value={draft.instructions} onChange={event => patch({ instructions: event.target.value })}
        placeholder="Ex.: priorize minhas explicações sobre consciência; preserve o contexto e evite frases cortadas." />
    </Field>
    <Switch label="Evitar trechos já sugeridos" checked={draft.newClipsOnly} onChange={newClipsOnly => patch({ newClipsOnly })} />
  </Dialog>;
}
