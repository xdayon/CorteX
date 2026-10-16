import type { ReactNode } from "react";

export type SegmentOption<T extends string> = { value: T; label: ReactNode; count?: number };

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: Array<SegmentOption<T>>; onChange: (value: T) => void; label: string;
}) {
  return <div className="seg" role="radiogroup" aria-label={label}>
    {options.map(option => <button key={option.value} type="button" role="radio" aria-checked={option.value === value} onClick={() => onChange(option.value)}>
      {option.label}{option.count !== undefined && <span className="num">{option.count}</span>}
    </button>)}
  </div>;
}
