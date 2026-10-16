import type { ReactNode } from "react";

export type TabOption<T extends string> = { value: T; label: ReactNode };

export function Tabs<T extends string>({ value, options, onChange, label }: {
  value: T; options: Array<TabOption<T>>; onChange: (value: T) => void; label: string;
}) {
  return <div className="tabs" role="tablist" aria-label={label}>
    {options.map(option => <button key={option.value} type="button" role="tab" aria-selected={option.value === value} onClick={() => onChange(option.value)}>{option.label}</button>)}
  </div>;
}
