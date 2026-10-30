const {chromium} = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE,headless:true,args:['--disable-dev-shm-usage']});
  const report = {at:new Date().toISOString(),scope:'PRODUCTION PUBLIC LOGIN + ANONYMOUS SAFETY, NO PASSWORD OR SESSION BYPASS',errors:[]};
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror', e => report.errors.push(e.message));
    const response = await page.goto('https://example.invalid/',{waitUntil:'networkidle'});
    assert.equal(response.status(),200);
    await page.locator('#login-form').waitFor({state:'visible'});
    assert.equal(await page.locator('#workspace').isVisible(),false);
    const result = await page.evaluate(async () => {
      const health = await (await fetch('/health/live')).json();
      const statuses = {};
      for (const path of ['/api/discovery','/api/discovery/history','/api/monitor','/health/ready']) statuses[path]=(await fetch(path)).status;
      return {health,statuses};
    });
    const deployed=JSON.parse(fs.readFileSync('evidence/expansion-deploy.json','utf8'));
    assert.equal(result.health.version,'0.3.0@'+deployed.candidate);
    assert.equal(result.health.real_trading,'NOT_IMPLEMENTED');
    assert(Object.values(result.statuses).every(v=>v===401));
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:'evidence/expansion-screenshots/production-login-mobile.png'});
    assert.deepEqual(report.errors,[]);
    report.status='PASS';report.verification=result;
  } catch(e) {report.status='FAIL';report.failure=e.message;process.exitCode=1;}
  finally {fs.writeFileSync('evidence/expansion-production-browser.json',JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify(report,null,2));}
})();
