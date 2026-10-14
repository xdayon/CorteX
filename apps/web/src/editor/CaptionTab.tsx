import { useEffect, useMemo, useRef, useState } from "react";
import type { CaptionCorrection, OverlayPreview, OverlayWord, SuggestedClip } from "../api";
import { findReplace, hideWord, restoreWord, setWordText, toggleEmphasis, type IndexedWord } from "./captions";
import { clipKey } from "./clips";

const DOUBTFUL_BELOW = 0.6;

export function formatBlockTime(seconds: number) {
  const value = Math.max(0, seconds);
  const minutes = Math.floor(value / 60);
  const rest = (value - minutes * 60).toFixed(1).padStart(4, "0");
  return `${String(minutes).padStart(2, "0")}:${rest}`;
}

function WordPopover({ word, initial, emphasized, corrected, onSave, onEmphasis, onHide, onRestore, onClose }: {
  word: OverlayWord; initial: string; emphasized: boolean; corrected: boolean;
  onSave: (text: string) => void; onEmphasis: () => void; onHide: () => void; onRestore: () => void; onClose: () => void;
}) {
  const [text, setText] = useState(initial);
  return <div className="word-popover" role="dialog" aria-label={`Editar a palavra ${word.original}`}>
    <input autoFocus value={text} maxLength={200} aria-label="Texto da palavra"
      onChange={event => setText(event.target.value)}
      onKeyDown={event => {
        if (event.key === "Enter") { event.preventDefault(); onSave(text.trim()); }
        if (event.key === "Escape") { event.preventDefault(); onClose(); }
      }}/>
    <div className="actions">
      <button type="button" className="btn primary" onClick={() => onSave(text.trim())}>Salvar</button>
      <button type="button" className="btn secondary" aria-pressed={emphasized} onClick={onEmphasis}>{emphasized ? "Remover destaque" : "★ Destacar"}</button>
      <button type="button" className="btn secondary" onClick={onHide}>Ocultar palavra</button>
      <button type="button" className="btn secondary" disabled={!corrected} onClick={onRestore}>Restaurar original</button>
      <button type="button" className="btn secondary" onClick={onClose}>Cancelar</button>
    </div>
    {word.probability !== null && word.probability < DOUBTFUL_BELOW &&
      <small className="hidden-list">A transcrição teve pouca certeza nesta palavra ({Math.round(word.probability * 100)}%).</small>}
  </div>;
}

export function CaptionTab({ clip, clips, overlay, loading, error, time, onSeek, corrections, onCorrections, emphasis, onEmphasis,
  captionEdits, onAllCaptionEdits, words, wordsError, disabled }: {
  clip: SuggestedClip; clips: SuggestedClip[]; overlay: OverlayPreview | null; loading: boolean; error: string;
  time: number; onSeek: (seconds: number) => void;
  corrections: CaptionCorrection[]; onCorrections: (list: CaptionCorrection[]) => void;
  emphasis: number[]; onEmphasis: (list: number[]) => void;
  captionEdits: Record<string, CaptionCorrection[]>; onAllCaptionEdits: (all: Record<string, CaptionCorrection[]>) => void;
  words: IndexedWord[] | null; wordsError: string; disabled: boolean;
}) {
  const [editing, setEditing] = useState<number | null>(null);
  const [cursor, setCursor] = useState(0);
  const [find, setFind] = useState("");
  const [replacement, setReplacement] = useState("");
  const [scope, setScope] = useState<"clip" | "all">("clip");
  const [message, setMessage] = useState("");
  const blockRefs = useRef<Array<HTMLDivElement | null>>([]);
  const pages = overlay?.pages ?? [];
  const activeIndex = pages.findIndex(page => time >= page.start && time < page.end);

  useEffect(() => { setEditing(null); setCursor(0); setMessage(""); }, [clipKey(clip)]);
  useEffect(() => {
    if (editing !== null || activeIndex < 0) return;
    blockRefs.current[activeIndex]?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex, editing]);

  const flat = useMemo(() => pages.flatMap(page => page.lines.flat()), [pages]);
  const doubtful = flat.filter(word => word.probability !== null && word.probability < DOUBTFUL_BELOW
    && !corrections.some(item => item.word_index === word.index));
  const hidden = corrections.filter(item => item.text === "");
  const editingWord = editing === null ? null : flat.find(word => word.index === editing) ?? null;

  function reviewNext() {
    if (!doubtful.length) return;
    const target = doubtful[cursor % doubtful.length];
    onSeek(target.start);
    setEditing(target.index);
    setCursor(cursor + 1);
  }
  function replaceAll() {
    if (!words) return;
    const result = findReplace(words, scope === "clip" ? [clip] : clips, captionEdits, find, replacement);
    if (result.count) onAllCaptionEdits(result.edits);
    setMessage(result.count === 1 ? "1 palavra substituída." : `${result.count} palavras substituídas.`);
  }
  const correctionFor = (index: number) => corrections.find(item => item.word_index === index);

  return <div className="caption-tab">
    <div className="caption-head">
      <h3>Blocos da legenda</h3>
      <button type="button" className="btn secondary" disabled={disabled || !doubtful.length} onClick={reviewNext}>
        Revisar {doubtful.length} {doubtful.length === 1 ? "palavra duvidosa" : "palavras duvidosas"}</button>
    </div>
    {error && <p className="preview-note warn" role="status">Não foi possível carregar a legenda: {error}</p>}
    {!overlay && !error && <p className="preview-note" role="status">{loading ? "Carregando a legenda…" : "Aguardando o corte ficar pronto…"}</p>}
    {overlay && pages.length === 0 && <p className="preview-note">Este corte não tem legenda visível. Confira se “Mostrar legenda” está ligado no Estilo.</p>}
    <div className="caption-blocks">
      {pages.map((page, pageIndex) => <div key={`${page.start}-${pageIndex}`} ref={node => { blockRefs.current[pageIndex] = node; }}
        className={`caption-block ${pageIndex === activeIndex ? "active" : ""}`} aria-current={pageIndex === activeIndex ? "true" : undefined}>
        <button type="button" className="caption-time" aria-label={`Ir para ${formatBlockTime(page.start)}`} onClick={() => onSeek(page.start)}>{formatBlockTime(page.start)}</button>
        <div className="caption-words">
          {page.lines.flat().map(word => {
            const correction = correctionFor(word.index);
            const isDoubtful = word.probability !== null && word.probability < DOUBTFUL_BELOW && !correction;
            const classes = ["caption-word", word.emphasis || emphasis.includes(word.index) ? "emphasis" : "", isDoubtful ? "doubtful" : "",
              correction ? "edited" : "", word.text === "" ? "hidden-word" : ""].filter(Boolean).join(" ");
            return <button key={word.index} type="button" className={classes} disabled={disabled} aria-pressed={editing === word.index}
              title={isDoubtful ? "Palavra duvidosa — vale conferir" : undefined}
              onClick={() => setEditing(editing === word.index ? null : word.index)}>{word.text || word.original}</button>;
          })}
        </div>
        {editingWord && page.lines.flat().some(word => word.index === editingWord.index) &&
          <WordPopover key={editingWord.index} word={editingWord} initial={correctionFor(editingWord.index)?.text ?? editingWord.original}
            emphasized={emphasis.includes(editingWord.index)} corrected={!!correctionFor(editingWord.index)}
            onSave={text => { onCorrections(setWordText(corrections, editingWord.index, editingWord.original, text)); setEditing(null); }}
            onEmphasis={() => onEmphasis(toggleEmphasis(emphasis, editingWord.index))}
            onHide={() => { onCorrections(hideWord(corrections, editingWord.index, editingWord.original)); setEditing(null); }}
            onRestore={() => { onCorrections(restoreWord(corrections, editingWord.index)); setEditing(null); }}
            onClose={() => setEditing(null)}/>}
      </div>)}
    </div>
    {hidden.length > 0 && <div className="hidden-list">
      <b>Palavras ocultas</b>
      {hidden.map(item => <span key={item.word_index}> {item.original}{" "}
        <button type="button" className="caption-time" onClick={() => onCorrections(restoreWord(corrections, item.word_index))}>Restaurar {item.original}</button></span>)}
    </div>}
    <div className="find-replace">
      <h4>Localizar e substituir</h4>
      <input aria-label="Localizar" placeholder="Localizar palavra" value={find} onChange={event => setFind(event.target.value)}/>
      <input aria-label="Substituir por" placeholder="Substituir por" value={replacement} onChange={event => setReplacement(event.target.value)}/>
      <select aria-label="Onde substituir" value={scope} onChange={event => setScope(event.target.value as "clip" | "all")}>
        <option value="clip">Este corte</option><option value="all">Todos os cortes selecionados</option>
      </select>
      <button type="button" className="btn secondary" disabled={disabled || !words || !find.trim()} onClick={replaceAll}>Substituir</button>
      {wordsError && <p className="preview-note warn wide" role="status">Não foi possível carregar a transcrição: {wordsError}</p>}
      {message && <p className="preview-note wide" role="status">{message}</p>}
    </div>
  </div>;
}
