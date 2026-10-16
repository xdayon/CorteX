export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (value: boolean) => void; label: string; disabled?: boolean }) {
  return <button type="button" role="switch" aria-checked={checked} disabled={disabled} className="switch-row" onClick={() => onChange(!checked)}>
    <span>{label}</span><span className="switch-track"><i /></span>
  </button>;
}
