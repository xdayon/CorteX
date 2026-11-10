const TYPING = "input, textarea, select, [contenteditable='true']";

/** True when a key press comes from a text field or a dialog/menu is open, so page shortcuts must stand down. */
export function isTyping(event: Event) {
  const target = event.target instanceof Element ? event.target : null;
  return !!target?.closest(TYPING) || !!document.querySelector('[role="dialog"], [role="menu"]');
}
