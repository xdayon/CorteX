import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Boom({ fail }: { fail: boolean }) {
  if (fail) throw new Error("quebrou de verdade");
  return <p>tela ok</p>;
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("a render error shows the message and the ways out, and a route change clears it", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  window.history.replaceState(null, "", "/episodios/ep");
  const { rerender } = render(<ErrorBoundary resetKey="/episodios/ep"><Boom fail /></ErrorBoundary>);
  expect(screen.getByText("Algo quebrou nesta tela")).toBeTruthy();
  expect(screen.getByText("quebrou de verdade")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Recarregar" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Voltar aos episódios" }));
  expect(window.location.pathname).toBe("/");
  rerender(<ErrorBoundary resetKey="/"><Boom fail={false} /></ErrorBoundary>);
  expect(screen.getByText("tela ok")).toBeTruthy();
  expect(screen.queryByText("Algo quebrou nesta tela")).toBeNull();
});

test("Recarregar reloads the page", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const reload = vi.fn();
  vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload } as unknown as Location);
  act(() => { render(<ErrorBoundary resetKey="/"><Boom fail /></ErrorBoundary>); });
  fireEvent.click(screen.getByRole("button", { name: "Recarregar" }));
  expect(reload).toHaveBeenCalled();
});
