import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { editorPath, episodePath, Link, navigate, parseRoute, useRoute } from "./router";

function Probe() {
  const route = useRoute();
  return <output>{JSON.stringify(route)}</output>;
}

beforeEach(() => window.history.replaceState(null, "", "/"));
afterEach(cleanup);

test.each([
  ["/", "", { name: "episodes" }],
  ["/cortes", "", { name: "cuts" }],
  ["/episodios/abc", "", { name: "episode", id: "abc" }],
  ["/episodios/local-p%201", "", { name: "episode", id: "local-p 1" }],
  ["/episodios/abc/editor", "?run=r1&clips=1%3A0%3A40%3Abalanced,2%3A60%3A100%3Abalanced", { name: "editor", id: "abc", run: "r1", clips: ["r1~1:0:40:balanced", "r1~2:60:100:balanced"] }],
  ["/episodios/abc/editor", "?clips=r1~1%3A0%3A40%3Abalanced,r2~1%3A5%3A50%3Adynamic", { name: "editor", id: "abc", run: "", clips: ["r1~1:0:40:balanced", "r2~1:5:50:dynamic"] }],
  ["/episodios/abc/editor", "", { name: "editor", id: "abc", run: "", clips: [] }],
  ["/episodios", "", { name: "unknown" }],
  ["/qualquer/coisa", "", { name: "unknown" }],
])("parseRoute %s%s", (path, search, expected) => {
  expect(parseRoute(path, search)).toEqual(expected);
});

test("editorPath round-trips through parseRoute", () => {
  const keys = ["run-9~1:0:40:balanced", "run-8~2:60:100:dynamic"];
  const [path, search] = editorPath("ep 1", keys).split("?");
  expect(episodePath("ep 1")).toBe("/episodios/ep%201");
  expect(parseRoute(path, `?${search}`)).toEqual({ name: "editor", id: "ep 1", run: "", clips: keys });
});

test("navigate pushes or replaces the URL and useRoute follows, including back navigation", () => {
  render(<Probe />);
  expect(screen.getByRole("status").textContent).toContain('"episodes"');
  act(() => navigate("/episodios/x"));
  expect(window.location.pathname).toBe("/episodios/x");
  expect(screen.getByRole("status").textContent).toContain('"id":"x"');
  const length = window.history.length;
  act(() => navigate("/cortes", { replace: true }));
  expect(window.history.length).toBe(length);
  expect(screen.getByRole("status").textContent).toContain('"cuts"');
  act(() => { window.history.replaceState(null, "", "/episodios/y"); window.dispatchEvent(new PopStateEvent("popstate")); });
  expect(screen.getByRole("status").textContent).toContain('"id":"y"');
});

test("Link navigates client-side on a plain click but leaves modified clicks to the browser", () => {
  render(<><Link to="/cortes">Cortes</Link><Probe /></>);
  const link = screen.getByRole("link", { name: "Cortes" });
  expect(link.getAttribute("href")).toBe("/cortes");
  fireEvent.click(link, { ctrlKey: true });
  expect(window.location.pathname).toBe("/");
  fireEvent.click(link);
  expect(window.location.pathname).toBe("/cortes");
  expect(screen.getByRole("status").textContent).toContain('"cuts"');
});
