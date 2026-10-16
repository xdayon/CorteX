import { forwardRef, useImperativeHandle, useRef, type ReactNode } from "react";
import type { DiscardReason } from "../../api";
import { Menu, MenuItem, MenuLabel } from "../../ui";
import { REASONS, REASON_LABELS } from "./clipModel";

type TriggerProps = { onClick: () => void; "aria-expanded": boolean; "aria-haspopup": "menu"; "aria-controls": string };

/** Menu of discard reasons; `open()` lets a keyboard shortcut pop it open by clicking the trigger. */
export const DiscardMenu = forwardRef<{ open: () => void }, { onPick: (reason: DiscardReason) => void; up?: boolean; trigger: (props: TriggerProps & { ref: React.Ref<HTMLButtonElement> }) => ReactNode }>(function DiscardMenu({ onPick, trigger, up }, handle) {
  const button = useRef<HTMLButtonElement>(null);
  useImperativeHandle(handle, () => ({ open: () => button.current?.click() }), []);
  return <Menu label="Motivo do descarte" align="left" up={up} trigger={props => trigger({ ...props, ref: button })}>
    <MenuLabel>Por que descartar?</MenuLabel>
    {REASONS.map(reason => <MenuItem key={reason} onSelect={() => onPick(reason)}>{REASON_LABELS[reason]}</MenuItem>)}
  </Menu>;
});
