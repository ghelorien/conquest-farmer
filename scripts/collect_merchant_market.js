// Read-only DOM collection using the Codex browser plugin's documented API.
// Call with a tab already filtered to America, no other filters, page 1.
// Pass observePage = () => cuaTab.getAXState({emit:false}) to settle each action.
// Returns data only; write the returned object to a local JSON artifact, then
// run scripts/merchant_scan.py --browser-pages <artifact>.
async function collectMerchantMarket(tab, observePage) {
  const page = tab.playwright;
  const settled = async () => {
    await page.getByRole('button',{name:'Refresh',exact:true}).and(page.locator(':enabled')).waitFor({state:'visible'});
    await page.getByRole('progressbar',{name:'Updating market items'}).waitFor({state:'hidden'});
  };
  await settled();
  if ((await page.getByRole('combobox', {name:'Server',exact:true}).innerText()).trim() !== 'America')
    throw new Error('Select America before scanning');
  for (const label of ['Item name','Socket','Seller']) {
    if (await page.getByRole('textbox',{name:label,exact:true}).evaluate(e=>e.value))
      throw new Error('Clear all filters except America before scanning');
  }
  for (const label of ['Plus','Min price','Max price']) {
    if (await page.getByRole('spinbutton',{name:label,exact:true}).evaluate(e=>e.value))
      throw new Error('Clear numeric filters before scanning');
  }
  for (const label of ['Category','Subcategory','Quality']) {
    // The site's empty select uses a zero-width-space placeholder.
    if ((await page.getByRole('combobox',{name:label,exact:true}).innerText()).replace(/\u200b/g,'').trim())
      throw new Error('Clear category and quality filters before scanning');
  }
  if (await page.getByRole('button',{name:'Go to previous page',exact:true}).isEnabled())
    throw new Error('Start from market page 1');
  const total = async () => Number((await page.getByText(/matching items/).innerText()).replace(/[^0-9]/g,''));
  const change = async () => (await page.getByText(/Last booth change/).innerText()).trim();
  const result = {source:'https://conqueronline.net/market',server:'America',
    observed_at:Date.now()/1000,initial_total:await total(),initial_change:await change(),pages:[],last_page:false};
  for (let number=1; number<=1000; number++) {
    await settled();
    await page.getByRole('button',{name:`page ${number}`,exact:true}).waitFor({state:'visible'});
    await observePage();
    if (await total()!==result.initial_total || await change()!==result.initial_change)
      throw new Error('Market changed during scan; discard and refresh');
    const rows = await page.getByRole('table',{name:'Live market listings'}).getByRole('row')
      .evaluateAll(rs=>rs.slice(1).map(r=>Array.from(r.cells).map(c=>c.innerText)));
    result.pages.push({page:number,rows});
    const next = page.getByRole('button',{name:'Go to next page',exact:true});
    if (!(await next.isEnabled())) {result.last_page=true;break;}
    await next.click();
  }
  result.final_total = await total();result.final_change = await change();
  if (!result.last_page || result.initial_total!==result.final_total || result.initial_change!==result.final_change
      || result.pages.reduce((n,p)=>n+p.rows.length,0)!==result.final_total)
    throw new Error('Market collection was incomplete or changed; discard and refresh');
  return result;
}
