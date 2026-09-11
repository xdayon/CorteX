"""Host-browser regression for the exact shared headline React component."""
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from conftest import requires_remotion_e2e
from cortex.config import load_config


@requires_remotion_e2e
def test_browser_headline_preserves_unicode_and_separates_padded_lines(tmp_path):
    browser = (os.environ.get("CORTEX_REMOTION_BROWSER_EXECUTABLE")
               or load_config().render.remotion_browser or shutil.which("chromium"))
    if not browser or not shutil.which("node"):
        pytest.skip("installed Chromium and Node required")
    root = Path(__file__).resolve().parents[1]
    app = root / "apps/remotion"
    headline = {"enabled": True, "text": "Religiões, consciência, ação: gyp\nReligiões gyp",
                "fontFamily": "Montserrat", "fontSize": 110, "durationSeconds": 3,
                "burstColor": "#11B9AD", "stripColor": "#FFFFFF", "textColor": "#071012",
                "animation": {"entrance": "slide", "exit": "fade", "durationSeconds": .24}}
    script = r'''
const fs = require('node:fs');
const ts = require('typescript');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync('src/HeadlineLayer.tsx', 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX}}).outputText;
const moduleValue = {exports: {}};
new Function('module', 'exports', 'require', compiled)(moduleValue, moduleValue.exports, require);
const render = (time) => renderToStaticMarkup(React.createElement(moduleValue.exports.HeadlineLayer,
    {headline: input, time, width: 1080, height: 1920}));
process.stdout.write(JSON.stringify({settled:render(1), entrance:render(.05), exit:render(2.9), ended:render(3)}));
'''
    result = subprocess.run([shutil.which("node"), "-e", script], cwd=app,
                            input=json.dumps(headline), text=True, capture_output=True, check=True, timeout=30)
    markup = json.loads(result.stdout)
    assert markup["ended"] == ""
    assert markup["entrance"] != markup["settled"] != markup["exit"]
    assert "translateY(0px)" in markup["settled"]
    page = tmp_path / "headline.html"
    page.write_text('''<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0;background:#243038;width:1080px;height:1920px">''' + markup["settled"] + r'''
<pre id="metrics" style="display:none"></pre><script>
const span = document.querySelector('[data-cortex-headline-text]');
const rects = [...span.getClientRects()].filter(r => r.width > 1).map(r => ({top:r.top,bottom:r.bottom,left:r.left,right:r.right}));
const chars = [...span.textContent].flatMap((letter,index) => {
    if (!'gypõçêã'.includes(letter)) return [];
    const range = document.createRange(); range.setStart(span.firstChild,index); range.setEnd(span.firstChild,index+1);
    const r=range.getBoundingClientRect();
    return [{letter,top:r.top,bottom:r.bottom,left:r.left,right:r.right}];
});
document.querySelector('#metrics').textContent = JSON.stringify({rects,chars,text:span.textContent});
</script></body></html>''', encoding="utf-8")
    screenshot = tmp_path / "headline-unicode.png"
    browser_result = subprocess.run([str(browser), "--headless", "--no-sandbox", "--disable-gpu",
        f"--user-data-dir={tmp_path / 'browser-profile'}", "--no-first-run", "--disable-background-networking",
        "--window-size=1080,1920", "--virtual-time-budget=500", f"--screenshot={screenshot}", "--dump-dom", page.as_uri()],
        text=True, capture_output=True, check=True, timeout=60)
    match = re.search(r'<pre id="metrics"[^>]*>(.*?)</pre>', browser_result.stdout, flags=re.S)
    assert match, browser_result.stderr[-2000:]
    metrics = json.loads(html.unescape(match.group(1)))
    assert metrics["text"] == headline["text"]
    rects = metrics["rects"]
    assert len(rects) >= 3
    assert all(after["top"] > before["bottom"] for before, after in zip(rects, rects[1:]))
    assert len(metrics["chars"]) >= 8
    for char in metrics["chars"]:
        assert any(char["top"] >= row["top"] and char["bottom"] <= row["bottom"] for row in rects), char
    assert screenshot.is_file() and screenshot.stat().st_size > 10000
    (tmp_path / "headline-metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
