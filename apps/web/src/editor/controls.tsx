import { Field } from "../ui";

export function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const pickerValue = /^#[0-9a-fA-F]{6}/.test(value) ? value.slice(0, 7) : "#FFFFFF";
  function pick(rgb: string) { onChange(value.length === 9 ? `${rgb}${value.slice(7)}` : rgb); }
  return <Field label={label}><div className="color-control"><input type="color" value={pickerValue} onChange={(event) => pick(event.target.value.toUpperCase())}/><input type="text" value={value} maxLength={9} spellCheck={false} onChange={(event) => onChange(event.target.value.toUpperCase())}/></div></Field>;
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (value: boolean) => void; label: string; disabled?: boolean }) {
  return <button type="button" aria-pressed={checked} disabled={disabled} className="switch-row" onClick={() => onChange(!checked)}><span>{label}</span><span className="switch-track"><i /></span></button>;
}

export { Field, Slider as Range } from "../ui";
