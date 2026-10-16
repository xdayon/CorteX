import { useEffect } from "react";

/** Sets the document title; a null title leaves it to the page that owns it. */
export function usePageTitle(title: string | null) {
  useEffect(() => { if (title) document.title = title; }, [title]);
}
