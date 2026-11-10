import { Button, Icon } from "../../ui";

/** "Marcar como postado" while off; "Postado ✓" once on. */
export function PostedToggle({ posted, onClick }: { posted: boolean; onClick: () => void }) {
  return <Button size="sm" aria-pressed={posted} onClick={onClick}>{posted && <Icon name="check" small />}{posted ? "Postado ✓" : "Marcar como postado"}</Button>;
}
