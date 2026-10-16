import { Button, Icon } from "../../ui";

/** "Marcar salvo" while off; "Salvo" with a check once on. */
export function SavedToggle({ saved, onClick }: { saved: boolean; onClick: () => void }) {
  return <Button size="sm" aria-pressed={saved} onClick={onClick}>{saved && <Icon name="check" small />}{saved ? "Salvo" : "Marcar salvo"}</Button>;
}
