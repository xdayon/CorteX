import { useEffect, useMemo, useRef, useState } from "react";
import type { CaptionCorrection, OverlayPreview, OverlayWord, SuggestedClip } from "../../api";
import { findReplace, hideWord, isDoubtful, restoreWord, setWordText, toggleEmphasis, toggleLineBreak, type IndexedWord } from "../../editor/captions";
import { Button, Icon, Kbd, Segmented, Select, TextInput } from "../../ui";
import { formatBlockTime } from "./timecode";

type Filter = "all" | "doubtful" | "emphasis";
export type CaptionTarget = { key: string; clip: SuggestedClip };

function WordPopover({ word, initial, emphasized, corrected, lineBreak, onSave, onEmphasis, onLineBreak, onHide, onRestore, onClose }: {
  word: OverlayWord; initial: string; emphasized: boolean; corrected: boolean; lineBreak: boolean;
  onSave: (text: string) => void; onEmphasis: () => void; onLineBreak: () => void; onHide: () => void; onRestore: () => void; onClose: () => void;
}) {
  const [text, setText] = useState(initial);
  return <div className="word-popover" role="dialog" aria-label={`Editar a palavra ${word.original}`}>
    <TextInput autoFocus value={text} maxLength={200} aria-label="Texto da palavra"
      onChange={event => setText(event.target.value)}
      onKeyDown={event => {
        if (event.key === "Enter") { event.preventDefault(); onSave(text.trim()); }
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
      }} />
    <div className="actions">
      <Button variant="primary" size="sm" onClick={() => onSave(text.trim())}>Salvar</Button>
      <Button size="sm" aria-pressed={emphasized} onClick={onEmphasis}>{emphasized ? "Remover destaque" : "★ Destacar"}</Button>
      <Button size="sm" aria-pressed={lineBreak} onClick={onLineBreak}>{lineBreak ? "Tirar quebra de linha" : "↵ Quebrar linha aqui"}</Button>
      <Button size="sm" onClick={onHide}>Ocultar palavra</Button>
      <Button size="sm" disabled={!corrected} onClick={onRestore}>Restaurar original</Button>
      <Button size="sm" variant="ghost" onClick={onClose}>Cancelar</Button>
    </div>
    {word.probability !== null && word.probability < 0.6 &&
      <small className="hint">A transcrição teve pouca certeza nesta palavra ({Math.round(word.probability * 100)}%).</small>}
  </div>;
}

export function CaptionTab({ current, targets, overlay, loading, error, time, onSeek, corrections, onCorrections, emphasis, onEmphasis,
  lineBreaks, onLineBreaks, captionEdits, onAllCaptionEdits, words, wordsError, disabled }: {
  current: CaptionTarget; targets: CaptionTarget[]; overlay: OverlayPreview | null; loading: boolean; error: string;
  time: number; onSeek: (seconds: number) => void;
  corrections: CaptionCorrection[]; onCorrections: (list: CaptionCorrection[]) => void;
  emphasis: number[]; onEmphasis: (list: number[]) => void;
  lineBreaks: number[]; onLineBreaks: (list: number[]) => void;
  captionEdits: Record<string, CaptionCorrection[]>; onAllCaptionEdits: (all: Record<string, CaptionCorrection[]>) => void;
  words: IndexedWord[] | null; wordsError: string; disabled: boolean;
}) {
  const [editing, setEditing] = useState<number | null>(null);
  const [cursor, setCursor] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const [find, setFind] = useState("");
  const [replacement, setReplacement] = useState("");
  const [scope, setScope] = useState<"clip" | "all">("clip");
  const [message, setMessage] = useState("");
  const blockRefs = useRef<Array<HTMLDivElement | null>>([]);
  const pages = overlay?.pages ?? [];
  const activeIndex = pages.findIndex(page => time >= page.start && time < page.end);

  useEffect(() => { setEditing(null); setCursor(0); setMessage(""); }, [current.key]);
  useEffect(() => {
    if (editing !== null || activeIndex < 0) return;
    blockRefs.current[activeIndex]?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex, editing]);

  const flat = useMemo(() => pages.flatMap(page => page.lines.flat()), [pages]);
  const isEmphasized = (word: OverlayWord) => word.emphasis || emphasis.includes(word.index);
  const doubtful = flat.filter(word => isDoubtful(word, corrections));
  const highlighted = flat.filter(isEmphasized);
  const hidden = corrections.filter(item => item.text === "");
  const editingWord = editing === null ? null : flat.find(word => word.index === editing) ?? null;
  const correctionFor = (index: number) => corrections.find(item => item.word_index === index);
  const shown = pages.map((page, index) => ({ page, index })).filter(({ page }) => {
    const list = page.lines.flat();
    return filter === "all" || (filter === "doubtful" ? list.some(word => isDoubtful(word, corrections)) : list.some(isEmphasized));
  });

  function reviewNext() {
    if (!doubtful.length) return;
    const target = doubtful[cursor % doubtful.length];
    setFilter("all");
    onSeek(target.start);
    setEditing(target.index);
    setCursor(cursor + 1);
  }
  function replaceAll() {
    if (!words) return;
    const result = findReplace(words, scope === "clip" ? [current] : targets, captionEdits, find, replacement);
    if (result.count) onAllCaptionEdits(result.edits);
    setMessage(result.count === 1 ? "1 palavra substituída." : `${result.count} palavras substituídas.`);
  }
  function clickWord(word: OverlayWord, shift: boolean) {
    if (shift) { onEmphasis(toggleEmphasis(emphasis, word.index)); return; }
    setEditing(editing === word.index ? null : word.index);
  }

  return <div className="pane caption-tab">
    <label className="ed-search"><Icon name="search" small />
      <input type="text" aria-label="Localizar" placeholder="Localizar palavra para substituir" value={find} onChange={event => setFind(event.target.value)} />
    </label>
    {find.trim() && <div className="find-replace">
      <TextInput aria-label="Substituir por" placeholder="Substituir por" value={replacement} onChange={event => setReplacement(event.target.value)} />
      <Select aria-label="Onde substituir" value={scope} onChange={event => setScope(event.target.value as "clip" | "all")}>
        <option value="clip">Este corte</option>
        <option value="all">{targets.length > 1 ? `Todos os ${targets.length} cortes` : "Todos os cortes"}</option>
      </Select>
      <Button disabled={disabled || !words} onClick={replaceAll}>Substituir</Button>
      {wordsError && <p className="hint warn wide" role="status">Não foi possível carregar a transcrição: {wordsError}</p>}
      {message && <p className="hint wide" role="status">{message}</p>}
    </div>}
    <Segmented<Filter> label="Filtrar blocos da legenda" value={filter} onChange={setFilter} options={[
      { value: "all", label: "Todos" },
      { value: "doubtful", label: <>Duvidosas <span className="n warn">{doubtful.length}</span></> },
      { value: "emphasis", label: <>Destaques <span className="n hl">{highlighted.length}</span></> },
    ]} />
    {doubtful.length > 0 && <Button size="sm" className="review" disabled={disabled} onClick={reviewNext}>
      Revisar {doubtful.length} {doubtful.length === 1 ? "palavra duvidosa" : "palavras duvidosas"}</Button>}
    {error && <p className="hint warn" role="status">Não foi possível carregar a legenda: {error}</p>}
    {!overlay && !error && <p className="hint" role="status">{loading ? "Carregando a legenda…" : "Aguardando o corte ficar pronto…"}</p>}
    {overlay && pages.length === 0 && <p className="hint">Este corte não tem legenda visível. Confira se “Mostrar legenda” está ligado no Estilo.</p>}
    {overlay && pages.length > 0 && !shown.length && <p className="hint">Nenhum bloco neste filtro.</p>}
    <div className="blocks">
      {shown.map(({ page, index }) => <div key={`${page.start}-${index}`} ref={node => { blockRefs.current[index] = node; }}
        className={`blk${index === activeIndex ? " cur" : ""}`} aria-current={index === activeIndex ? "true" : undefined}>
        <button type="button" className="tcode mono" aria-label={`Ir para ${formatBlockTime(page.start)}`} onClick={() => onSeek(page.start)}>{formatBlockTime(page.start)}</button>
        <div className="blk-main">
          <div className="words">
            {page.lines.map((line, lineIndex) => [
              lineIndex > 0 && <span key={`line-${lineIndex}`} className="line-end" aria-hidden="true" />,
              ...line.map(word => {
                const correction = correctionFor(word.index);
                const classes = ["w", isEmphasized(word) ? "em" : "", isDoubtful(word, corrections) ? "doubt" : "", correction ? "edited" : "", word.text === "" ? "gone" : "", lineBreaks.includes(word.index) ? "br" : ""].filter(Boolean).join(" ");
                return <button key={word.index} type="button" className={classes} disabled={disabled} aria-pressed={editing === word.index}
                  title={isDoubtful(word, corrections) ? "Palavra duvidosa — vale conferir" : undefined}
                  onClick={event => clickWord(word, event.shiftKey)}>{correction && correction.text ? correction.text : word.original}</button>;
              }),
            ])}
          </div>
          {editingWord && page.lines.flat().some(word => word.index === editingWord.index) &&
            <WordPopover key={editingWord.index} word={editingWord} initial={correctionFor(editingWord.index)?.text ?? editingWord.original}
              emphasized={emphasis.includes(editingWord.index)} corrected={!!correctionFor(editingWord.index)}
              lineBreak={lineBreaks.includes(editingWord.index)}
              onLineBreak={() => onLineBreaks(toggleLineBreak(lineBreaks, editingWord.index))}
              onSave={text => { onCorrections(setWordText(corrections, editingWord.index, editingWord.original, text)); setEditing(null); }}
              onEmphasis={() => onEmphasis(toggleEmphasis(emphasis, editingWord.index))}
              onHide={() => { onCorrections(hideWord(corrections, editingWord.index, editingWord.original)); setEditing(null); }}
              onRestore={() => { onCorrections(restoreWord(corrections, editingWord.index)); setEditing(null); }}
              onClose={() => setEditing(null)} />}
        </div>
      </div>)}
    </div>
    {hidden.length > 0 && <div className="hint hidden-list">
      <b>Palavras ocultas</b>
      {hidden.map(item => <button type="button" key={item.word_index} className="link" onClick={() => onCorrections(restoreWord(corrections, item.word_index))}>Restaurar {item.original}</button>)}
    </div>}
    <p className="hint">Clique numa palavra para corrigir ou quebrar a linha antes dela. <Kbd>Shift</Kbd> + clique marca ou tira o destaque amarelo.</p>
  </div>;
}
