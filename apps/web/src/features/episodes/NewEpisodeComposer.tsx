import { useRef, useState, type DragEvent } from "react";
import { Button, Icon, IconButton, ProgressBar } from "../../ui";
import { RunOptionsDialog } from "./RunOptionsDialog";
import { DEFAULT_INSTRUCTIONS, hotwordCount, useRunOptions } from "./runOptions";
import { useCreateEpisodeRun } from "./useCreateEpisodeRun";

const MEDIA_TYPE = /^(video|audio)\//;

export function NewEpisodeComposer() {
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [options, setOptions] = useRunOptions();
  const picker = useRef<HTMLInputElement>(null);
  const create = useCreateEpisodeRun();

  function takeFile(candidate: File | undefined) {
    if (!candidate) return;
    if (!MEDIA_TYPE.test(candidate.type) && !/\.(mp4|mov|mkv|webm|mp3|wav|m4a)$/i.test(candidate.name)) {
      setError("Escolha um arquivo de vídeo ou áudio."); return;
    }
    setError(""); setFile(candidate);
  }
  function onDrop(event: DragEvent) {
    event.preventDefault(); setDragging(false);
    takeFile(event.dataTransfer.files[0]);
  }
  async function submit() {
    if (create.busy) return;
    setError("");
    const failure = await create.start(file ? { kind: "file", file } : { kind: "youtube", url }, options);
    if (failure) setError(failure);
  }

  const terms = hotwordCount(options.hotwordsText);
  const chips: Array<[string, string]> = [
    ["Cortes", String(options.count)],
    ["Duração", `${options.minimum}–${options.maximum} s`],
    ["Protagonista", options.primarySubject || "não informado"],
    ["Direção editorial", options.instructions === DEFAULT_INSTRUCTIONS ? "padrão do perfil" : "personalizada"],
    ["Nomes e termos", terms ? String(terms) : "adicionar"],
  ];
  const ready = !!file || url.trim().length > 0;

  return <section className={`composer card${dragging ? " dragging" : ""}`} aria-label="Novo episódio"
    onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
    <div className="cmp-row">
      <div className="cmp-in">
        <Icon name={file ? "film" : "link"} />
        {file
          ? <><span className="cmp-file">{file.name}</span><IconButton icon="x" size="sm" aria-label="Remover arquivo" disabled={create.busy} onClick={() => setFile(null)} /></>
          : <input type="text" value={url} disabled={create.busy} aria-label="Link do YouTube ou arquivo do episódio"
              placeholder="Cole o link do YouTube ou solte aqui o arquivo do episódio"
              onChange={event => setUrl(event.target.value)} onKeyDown={event => { if (event.key === "Enter") void submit(); }} />}
        <input ref={picker} type="file" hidden accept="video/*,audio/*" data-testid="episode-file"
          onChange={event => { takeFile(event.target.files?.[0]); event.target.value = ""; }} />
        <Button size="sm" icon="upload" disabled={create.busy} onClick={() => picker.current?.click()}>Escolher arquivo</Button>
      </div>
      <Button variant="primary" size="lg" icon="spark" disabled={create.busy || !ready} onClick={() => void submit()}>
        {create.busy ? "Processando…" : "Processar episódio"}
      </Button>
    </div>
    <div className="cmp-opts">
      {chips.map(([key, value]) => <button key={key} type="button" className="ochip" disabled={create.busy} onClick={() => setOptionsOpen(true)}>
        <span className="k">{key}</span>{value}
      </button>)}
      {options.newClipsOnly && <span className="cmp-note">Trechos já sugeridos são evitados.</span>}
    </div>
    {create.busy && <div className="cmp-progress" role="status">
      <span>{create.message}</span>
      {create.progress > 0 && <ProgressBar value={create.progress} label="Andamento do envio" />}
    </div>}
    {error && <div className="notice bad" role="alert"><p>{error}</p></div>}
    {optionsOpen && <RunOptionsDialog options={options} onClose={() => setOptionsOpen(false)} onSave={next => { setOptions(next); setOptionsOpen(false); }} />}
  </section>;
}
