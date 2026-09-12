// Real Chromium glyph/geometry regression for the shared preview/export layer.
import {bundle} from '@remotion/bundler';
import {openBrowser} from '@remotion/renderer';
import {mkdtemp, mkdir, writeFile, readFile, rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const here=resolve(dirname(fileURLToPath(import.meta.url)), '..');
const [browserExecutable,output]=process.argv.slice(2);
await mkdir(resolve(here,'.cache'),{recursive:true});
await mkdir(output,{recursive:true});
const temp=await mkdtemp(resolve(here,'.cache/caption-test-'));
let server, browser;
try {
  const entry=resolve(temp,'entry.tsx');
  await writeFile(entry, `import React from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {CaptionLayer} from '../../src/CaptionLayer';
const div=document.createElement('div');document.body.appendChild(div);
const root=createRoot(div);
const words='GENTE TÁ LITERALMENTE NUMA ÉPOCA EXTREMAMENTE EXTRAORDINÁRIA INCONSTITUCIONALÍSSIMAMENTE CONSCIENTE'.split(' ').map((text,i)=>({text,start:i,end:i+1}));
const cues=[{start:0,end:words.length,words}];
const caption={enabled:true,fontFamily:'Montserrat',fontSize:56,fontWeight:900,uppercase:true,positionY:.61,wordsPerCue:12,outline:true,shadow:true,karaoke:true,textColor:'#ffffff',karaokeColor:'#20dfdf',outlineColor:'#000000',shadowColor:'#000000',animation:{style:'pop',durationSeconds:.18}};
window.showCaption=(time,scale=1)=>{flushSync(()=>root.render(<div style={{position:'relative',width:1080*scale,height:1920*scale,background:'#111'}}><CaptionLayer caption={caption} cues={cues} time={time} width={1080*scale} height={1920*scale}/></div>));};
window.captionWords=words;
window.showCaption(.5);
`);
  const dir=await bundle({entryPoint:entry,outDir:resolve(output,'bundle'),enableCaching:true,ignoreRegisterRootWarning:true});
  server=createServer(async(req,res)=>{try {const path=resolve(dir,'.'+(req.url==='/'?'/index.html':req.url));if(!path.startsWith(dir+'/')) throw Error('path');res.end(await readFile(path));}catch {res.statusCode=404;res.end();}});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await openBrowser('chrome',{browserExecutable,logLevel:'error'});
  const page=await browser.newPage({context:null,logLevel:'error',indent:false,pageIndex:0,onBrowserLog:null,onLog:()=>{}});
  await page.setViewport({width:1100,height:1940,deviceScaleFactor:1});
  await page.goto({url:`http://127.0.0.1:${server.address().port}/`,timeout:15000,options:{waitUntil:'load'}});
  const measurements=await page.evaluate(async()=>{
    await document.fonts.ready;
    const results=[];
    for(const scale of [1,.25]) for(const word of window.captionWords){
      window.showCaption(word.start+.5,scale);
      const box=document.querySelector('[data-cortex-caption]'),rect=box.getBoundingClientRect();
      const lines=[...document.querySelectorAll('[data-caption-line]')];
      const spans=lines.flatMap(line=>[...line.querySelectorAll('span')]);
      results.push({scale,word:word.text,start:Number(box.dataset.captionStart),top:(rect.top-box.parentElement.getBoundingClientRect().top)/scale,
        count:lines.length,visible:spans.map(s=>s.textContent),active:spans.filter(s=>getComputedStyle(s).color==='rgb(32, 223, 223)').map(s=>s.textContent),
        fits:spans.every(s=>{const r=s.getBoundingClientRect();return r.left>=rect.left&&r.right<=rect.right&&r.bottom<=rect.bottom;})});
    }
    window.showCaption(4.5);
    return results;
  });
  assert.equal(measurements.length,18);
  assert.ok(new Set(measurements.map(m=>m.start)).size>=2,'Long cue must paginate');
  for(const m of measurements){
    assert.ok(m.count<=2&&m.count>0,JSON.stringify(m));assert.ok(m.fits,JSON.stringify(m));
    assert.deepEqual(m.active,[m.word]);assert.ok(Math.abs(m.top-measurements[0].top)<.1,JSON.stringify(m));
  }
  for(let i=0;i<9;i++) assert.deepEqual(measurements[i].visible,measurements[i+9].visible,'Scaled preview/export pagination differs');
  const {value}=await page._client().send('Page.captureScreenshot',{format:'png'});
  await writeFile(resolve(output,'caption-layout.png'),Buffer.from(value.data,'base64'));
  await writeFile(resolve(output,'caption-layout.json'),JSON.stringify(measurements,null,2));
  console.log(JSON.stringify({samples:measurements.length,pages:new Set(measurements.map(m=>m.start)).size,maxLines:Math.max(...measurements.map(m=>m.count))}));
} finally {if(browser) await browser.close({silent:true});if(server) await new Promise(r=>server.close(r));await rm(temp,{recursive:true,force:true});}
