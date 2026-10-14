import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test } from "vitest";
import type { RenderSettings, SuggestedClip } from "../api";
import { HeadlineTab } from "./HeadlineTab";
import { defaultRenderSettings } from "./settings";

afterEach(cleanup);

function Harness({ clip }: { clip: SuggestedClip }) {
  const [settings, setSettings] = useState<RenderSettings>(defaultRenderSettings);
  return <>
    <HeadlineTab clip={clip} settings={settings} update={change => setSettings(change)} headline="" onHeadline={() => undefined} disabled={false}/>
    <output data-testid="teaser">{String(settings.transitions.teaser)}</output>
  </>;
}

const base = { title: "Título", headline: "Manchete" } as SuggestedClip;

test("the teaser toggle shows the quoted phrase and flips the setting", () => {
  render(<Harness clip={{ ...base, teaser: { start: 5, end: 8, text: "A frase mais forte" } }}/>);
  expect(screen.getByText(/“A frase mais forte”/)).toBeTruthy();
  const toggle = screen.getByRole("button", { name: /Abrir com teaser/ });
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(toggle);
  expect(screen.getByTestId("teaser").textContent).toBe("true");
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
});

test("without a teaser in the clip the toggle is disabled with a note", () => {
  render(<Harness clip={{ ...base, teaser: null }}/>);
  const toggle = screen.getByRole("button", { name: /Abrir com teaser/ }) as HTMLButtonElement;
  expect(toggle.disabled).toBe(true);
  expect(screen.getByText(/não encontrou uma frase boa/)).toBeTruthy();
});
