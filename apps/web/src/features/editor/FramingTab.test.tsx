import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test } from "vitest";
import type { RenderSettings } from "../../api";
import { FramingTab } from "./FramingTab";
import { defaultRenderSettings } from "../../editor/settings";

afterEach(cleanup);

function Harness({ initial }: { initial: RenderSettings }) {
  const [settings, setSettings] = useState(initial);
  return <>
    <FramingTab settings={settings} update={change => setSettings(change)} disabled={false}/>
    <output data-testid="mode">{settings.framing.focus_mode}</output>
  </>;
}

test("the faces toggle switches the focus slider to the no-face fallback", () => {
  render(<Harness initial={defaultRenderSettings}/>);
  const toggle = screen.getByRole("switch", { name: /Seguir rostos automaticamente/ });
  expect(toggle.getAttribute("aria-checked")).toBe("true");
  expect(screen.getByText("Foco quando não houver rosto")).toBeTruthy();
  expect(screen.queryByText("Foco horizontal")).toBeNull();
  fireEvent.click(toggle);
  expect(screen.getByTestId("mode").textContent).toBe("manual");
  expect(toggle.getAttribute("aria-checked")).toBe("false");
  expect(screen.getByText("Foco horizontal")).toBeTruthy();
});

test("the zoom slider names both ends and the format tiles switch the mode", () => {
  render(<Harness initial={defaultRenderSettings}/>);
  expect(screen.getByText("Estúdio inteiro")).toBeTruthy();
  expect(screen.getByText("Rosto")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Tela cheia/ }));
  expect(screen.getByRole("button", { name: /Tela cheia/ }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.queryByText("Estúdio inteiro")).toBeNull();
});
