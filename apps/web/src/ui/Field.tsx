import type { CSSProperties, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="field"><span className="field-head"><b>{label}</b>{hint && <small>{hint}</small>}</span>{children}</label>;
}

export function TextInput({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={["input", className].filter(Boolean).join(" ")} {...rest} />;
}

export function TextArea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={["textarea", className].filter(Boolean).join(" ")} {...rest} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={["select", className].filter(Boolean).join(" ")} {...rest}>{children}</select>;
}

export function Slider({ label, value, min, max, step = 1, suffix = "", onChange, startLabel, endLabel }: {
  label: string; value: number; min: number; max: number; step?: number; suffix?: string;
  onChange: (value: number) => void; startLabel?: string; endLabel?: string;
}) {
  const fill = `${((value - min) / (max - min)) * 100}%`;
  return <Field label={label}>
    <div className="range-wrap">
      <input type="range" min={min} max={max} step={step} value={value} style={{ "--fill": fill } as CSSProperties} onChange={event => onChange(Number(event.target.value))} />
      <output>{value}{suffix}</output>
    </div>
    {(startLabel || endLabel) && <span className="range-ends"><small>{startLabel}</small><small>{endLabel}</small></span>}
  </Field>;
}
