#!/usr/bin/env node
// Optional documentation tooling: Playwright + Chromium and ffmpeg, not runtime dependencies.
'use strict';
const {chromium} = require('playwright');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const demo = path.join(root, 'docs', 'demos');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'duet-demo-'));
const stamp = seconds => new Date(seconds * 1000).toISOString().slice(11, 23);

async function checkLayout(page) {
  const problems = await page.evaluate(() => {
    const rect = id => document.getElementById(id).getBoundingClientRect();
    const output = rect('output'), note = rect('note'), stage = rect('stage');
    const terminal = document.querySelector('.terminal').getBoundingClientRect();
    const footer = document.querySelector('.footer').getBoundingClientRect();
    const problems = [];
    if (output.bottom > note.top + 1) problems.push('Output overlaps its note');
    if (note.bottom > terminal.bottom + 1) problems.push('Note overflows terminal');
    if (footer.bottom > stage.bottom + 1) problems.push('Footer overflows stage');
    if (document.documentElement.scrollWidth > innerWidth) problems.push('Horizontal overflow');
    return problems;
  });
  assert.deepEqual(problems, [], 'Demo layout must remain readable');
}

async function main() {
  execFileSync(process.env.PYTHON || 'python3', [path.join(root, 'scripts', 'build_review_demo.py'), '--check'], {stdio:'inherit'});
  const browser = await chromium.launch({headless:true, ...(process.env.DUET_CHROMIUM ? {executablePath:process.env.DUET_CHROMIUM} : {})});
  try {
    const context = await browser.newContext({viewport:{width:1280,height:900},deviceScaleFactor:1,reducedMotion:'reduce'});
    const remoteRequests = [];
    await context.route(/^https?:/, route=>{remoteRequests.push(route.request().url());return route.abort();});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e=>errors.push(e.message));
    await page.goto(pathToFileURL(path.join(demo, 'finding-review.html')).href);
    const info = await page.evaluate(()=>({scenes:window.demo.scenes,total:window.demo.total}));
    assert.equal(info.total,80);
    const concat = [], captions = ['WEBVTT', ''];
    let elapsed = 0;
    for (const [index,scene] of info.scenes.entries()) {
      await page.evaluate(i=>window.demo.seekScene(i), index);
      await checkLayout(page);
      const frame = path.join(temp, 'scene-'+index+'.png');
      await page.locator('#stage').screenshot({path:frame,animations:'disabled'});
      concat.push("file '"+frame.replace(/'/g,"'\\''")+"'", 'duration '+scene.duration);
      captions.push(String(index+1),stamp(elapsed)+' --> '+stamp(elapsed+scene.duration),scene.title,scene.subtitle,'');
      elapsed += scene.duration;
      if (index===0) fs.copyFileSync(frame,path.join(demo,'finding-review.png'));
    }
    concat.push("file '"+path.join(temp,'scene-6.png').replace(/'/g,"'\\''")+"'");
    fs.writeFileSync(path.join(temp,'frames.txt'),concat.join('\n')+'\n');
    fs.writeFileSync(path.join(demo,'finding-review.vtt'),captions.join('\n'));
    // Verify playback and scrubbing separately from fixed-frame rendering.
    await page.evaluate(()=>window.demo.seekScene(0));
    await page.getByRole('button',{name:'Play demo',exact:true}).click();
    await page.waitForFunction(()=>window.demo.getPosition()>0.2);
    await page.getByRole('button',{name:'Pause',exact:true}).click();
    await page.locator('#progress').fill('68');
    assert.equal(await page.locator('#title').textContent(),info.scenes[5].title);
    await page.getByRole('button',{name:'Try the fixture',exact:true}).click();
    assert.equal(await page.locator('#title').textContent(),info.scenes[6].title);
    const embedded = await page.locator('#demo-data').textContent();
    assert.deepEqual(JSON.parse(embedded),JSON.parse(fs.readFileSync(path.join(demo,'finding-review.source.json'),'utf8')));
    await page.setViewportSize({width:390,height:844});
    for (let index=0;index<info.scenes.length;index++) {
      await page.evaluate(i=>window.demo.seekScene(i),index);
      await checkLayout(page);
    }
    await page.evaluate(()=>window.demo.seekScene(0));
    await page.screenshot({path:path.join(temp,'mobile.png'),fullPage:true});
    assert.deepEqual(errors,[],'Browser errors');
    assert.deepEqual(remoteRequests,[],'Playback must work without network requests');
    execFileSync(process.env.FFMPEG || 'ffmpeg',[
      '-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',path.join(temp,'frames.txt'),
      '-vf','fps=24,format=yuv420p','-c:v','libx264','-preset','slow','-crf','24','-t',String(info.total),
      '-movflags','+faststart','-metadata','title=Duet: Verify the review. Keep the evidence.',
      '-metadata','comment=Edited replay of saved findings from a public toy fixture; not a terminal recording or performance benchmark.',
      path.join(demo,'finding-review.mp4')
    ],{stdio:'inherit'});
    console.log('Exported 80s MP4, poster, and captions. Desktop/mobile layout, playback, source identity, and offline checks passed.');
    console.log('QA frames: '+temp);
  } finally {
    await browser.close();
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
