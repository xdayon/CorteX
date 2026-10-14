import type { CSSProperties, ReactNode } from "react";

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="field"><span className="field-head"><b>{label}</b>{hint && <small>{hint}</small>}</span>{children}</label>;
}

export function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const pickerValue = /^#[0-9a-fA-F]{6}/.test(value) ? value.slice(0, 7) : "#FFFFFF";
  function pick(rgb: string) { onChange(value.length === 9 ? `${rgb}${value.slice(7)}` : rgb); }
  return <Field label={label}><div className="color-control"><input type="color" value={pickerValue} onChange={(event) => pick(event.target.value.toUpperCase())}/><input type="text" value={value} maxLength={9} spellCheck={false} onChange={(event) => onChange(event.target.value.toUpperCase())}/></div></Field>;
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (value: boolean) => void; label: string; disabled?: boolean }) {
  return <button type="button" aria-pressed={checked} disabled={disabled} className={`toggle-row ${checked ? "on" : ""}`} onClick={() => onChange(!checked)}><span>{label}</span><i><em /></i></button>;
}

export function Range({ label, value, min, max, step = 1, suffix = "", onChange, startLabel, endLabel }: {
  label: string; value: number; min: number; max: number; step?: number; suffix?: string;
  onChange: (v: number) => void; startLabel?: string; endLabel?: string;
}) {
  const fill = `${((value - min) / (max - min)) * 100}%`;
  return <Field label={label}><div className="range-wrap"><input type="range" min={min} max={max} step={step} value={value} style={{ "--fill": fill } as CSSProperties} onChange={(e) => onChange(Number(e.target.value))}/><output>{value}{suffix}</output></div>
    {(startLabel || endLabel) && <span className="range-ends"><small>{startLabel}</small><small>{endLabel}</small></span>}</Field>;
}
