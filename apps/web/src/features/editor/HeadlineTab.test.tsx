import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test } from "vitest";
import type { RenderSettings, SuggestedClip } from "../../api";
import { HeadlineTab } from "./HeadlineTab";
import { defaultRenderSettings } from "../../editor/settings";

afterEach(cleanup);

function Harness({ clip }: { clip: SuggestedClip }) {
  const [settings, setSettings] = useState<RenderSettings>(defaultRenderSettings);
  const [headline, setHeadline] = useState<string | undefined>(undefined);
  return <>
    <HeadlineTab clip={clip} settings={settings} update={change => setSettings(change)} headline={headline} onHeadline={text => setHeadline(text ?? undefined)}
      onTeaser={teaser => setSettings(current => ({ ...current, transitions: { ...current.transitions, teaser } }))} disabled={false}/>
    <output data-testid="teaser">{String(settings.transitions.teaser)}</output>
    <output data-testid="duration">{settings.headline.persistent ? "all" : settings.headline.duration_seconds}</output>
  </>;
}

const base = { title: "Título", headline: "Manchete" } as SuggestedClip;

test("the teaser toggle shows the quoted phrase and flips the setting", () => {
  render(<Harness clip={{ ...base, teaser: { start: 5, end: 8, text: "A frase mais forte" } }}/>);
  expect(screen.getByText(/“A frase mais forte”/)).toBeTruthy();
  const toggle = screen.getByRole("switch", { name: /Abrir com teaser/ });
  expect(toggle.getAttribute("aria-checked")).toBe("false");
  fireEvent.click(toggle);
  expect(screen.getByTestId("teaser").textContent).toBe("true");
  expect(toggle.getAttribute("aria-checked")).toBe("true");
});

test("without a teaser in the clip the toggle is disabled with a note", () => {
  render(<Harness clip={{ ...base, teaser: null }}/>);
  const toggle = screen.getByRole("switch", { name: /Abrir com teaser/ }) as HTMLButtonElement;
  expect(toggle.disabled).toBe(true);
  expect(screen.getByText(/não encontrou uma frase boa/)).toBeTruthy();
});

test("the text field is prefilled with the effective headline and edits fall back to the AI suggestion", () => {
  render(<Harness clip={{ ...base, headline_options: ["Manchete", "Outra opção"] }}/>);
  const field = screen.getByRole("textbox", { name: "Texto da manchete" }) as HTMLInputElement;
  expect(field.value).toBe("Manchete");
  expect(screen.getByRole("radio", { name: /Manchete/ }).getAttribute("aria-checked")).toBe("true");
  fireEvent.change(field, { target: { value: "Minha manchete" } });
  expect(field.value).toBe("Minha manchete");
  expect(screen.getByRole("radio", { name: /Manchete/ }).getAttribute("aria-checked")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Usar a sugestão da IA" }));
  expect(field.value).toBe("Manchete");
  fireEvent.click(screen.getByRole("radio", { name: /Outra opção/ }));
  expect(field.value).toBe("Outra opção");
});

test("the on-screen duration is a segmented choice", () => {
  render(<Harness clip={base}/>);
  fireEvent.click(screen.getByRole("radio", { name: "8 s" }));
  expect(screen.getByTestId("duration").textContent).toBe("8");
  fireEvent.click(screen.getByRole("radio", { name: "O corte todo" }));
  expect(screen.getByTestId("duration").textContent).toBe("all");
});
