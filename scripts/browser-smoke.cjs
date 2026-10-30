const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});
 const base=process.env.ROBO_URL||'https://example.invalid';
 const result={url:base,at:new Date().toISOString(),checks:[],consoleErrors:[],pageErrors:[]};
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100},ignoreHTTPSErrors:false});
  const page=await context.newPage();
  page.on('pageerror',e=>result.pageErrors.push(e.message));
  page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('401'))result.consoleErrors.push(m.text());});
  const r=await page.goto(base,{waitUntil:'networkidle'});assert.equal(r.status(),200);
  await page.locator('#login-form').waitFor({state:'visible'});
  fs.mkdirSync('evidence/screenshots',{recursive:true});
  await page.screenshot({path:'evidence/screenshots/login.png',fullPage:true});
  await page.getByLabel('Senha',{exact:true}).fill(fs.readFileSync(process.env.ROBO_TEST_PASSWORD_FILE || path.join(__dirname,'../secrets/initial-password.txt'),'utf8').trim());
  await page.getByRole('button',{name:'Entrar no laboratório'}).click();
  await page.locator('#workspace').waitFor({state:'visible'});
  await page.locator('#opportunities tr').first().waitFor();
  assert.equal(await page.locator('#opportunities tr').count(),6);result.checks.push('login + 6 real market observations');
  const data=await page.evaluate(async()=>{const r=await fetch('/api/overview');return {status:r.status,value:await r.json()};});
  assert.equal(data.status,200);assert.equal(data.value.live_trading,false);result.checks.push('authenticated API and paper-only invariant');
  await page.locator('#opportunities tr button').first().click();
  await page.getByRole('dialog').waitFor({state:'visible'});await page.getByRole('button',{name:'Fechar',exact:true}).click();result.checks.push('cost detail dialog');
  await page.screenshot({path:'evidence/screenshots/dashboard-desktop.png',fullPage:true});
  await page.locator('#run-research').click();
  await page.locator('.experiment').first().waitFor();
  assert.match(await page.locator('#experiments').textContent(),/INSUFFICIENT_DATA|REPLAY_COMPLETED/);result.checks.push('real dataset experiment, no fabricated PnL');
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await page.screenshot({path:'evidence/screenshots/dashboard-mobile.png',fullPage:true});result.checks.push('mobile 390px without page overflow');
  await page.locator('#logout').click();await page.locator('#login-form').waitFor({state:'visible'});
  const privateStatus=await page.evaluate(async()=>(await fetch('/api/overview')).status);assert.equal(privateStatus,401);result.checks.push('logout revokes session');
  assert.deepEqual(result.pageErrors,[]);assert.deepEqual(result.consoleErrors,[]);
  result.status='PASS';
 }catch(e){result.status='FAIL';result.error=e.message;throw e;}
 finally{fs.writeFileSync('evidence/browser-smoke.json',JSON.stringify(result,null,2));await browser.close();console.log(JSON.stringify(result));}
})().catch(()=>process.exit(1));
