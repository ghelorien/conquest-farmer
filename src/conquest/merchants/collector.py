"""Deterministic public-table collection; no model, game input, or challenge bypass."""
import json
from pathlib import Path
import re
import time

from conquest.discord_notify import write_json
from conquest.merchants.market import SOURCE, MarketSnapshot, browser_pages, market_integer
from conquest.merchants.price_history import PriceHistory
from conquest.merchants.public_market import collect_public


def browser_launch_options(settings='.runtime/merchants/browser.json'):
    """Allow a verified local browser installation shared with the desktop app."""
    path=Path(settings)
    if not path.exists():return {'headless':True}
    data=json.loads(path.read_text(encoding='utf-8'))
    executable=Path(data['executable_path'])
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError('Configured market browser is missing; repair the local browser setup.')
    return {'headless':True,'executable_path':str(executable)}


def matching_count(raw):
    # Intl.NumberFormat uses narrow no-break spaces in some browser locales.
    match = re.fullmatch(r'\s*([0-9]+(?:[,\u00a0\u202f ][0-9]{3})*)\s+matching items\s*', raw)
    if not match:
        raise ValueError('Market count format changed')
    return market_integer(match[1])


def stable_metadata(read, check, *, clock=time.monotonic, sleep=time.sleep):
    """The rendered counter can update after network completion; await a stable value."""
    deadline = clock()+15
    previous,unchanged_since = None,clock()
    while clock()<deadline:
        check()
        current = read()
        now = clock()
        if current!=previous:
            previous,unchanged_since = current,now
        elif now-unchanged_since>=1:
            return current
        sleep(.1)
    raise ValueError('Market count did not settle; prices unchanged')


def collect_pages(page, *, check=lambda:None, clock=time.time):
    """Read an America-filtered page through ordinary Playwright DOM controls."""
    def settled():
        check()
        page.get_by_role('progressbar',name='Updating market items').wait_for(state='hidden')
        page.get_by_role('button',name='Refresh',exact=True).and_(page.locator(':enabled')).wait_for(state='visible')

    def clean(value):
        return value.replace('\u200b','').strip()

    def metadata():
        raw = page.get_by_text(re.compile('matching items')).inner_text()
        return matching_count(raw),page.get_by_text(re.compile('Last booth change')).inner_text().strip()

    settled()
    if clean(page.get_by_role('combobox',name='Server',exact=True).inner_text())!='America':
        raise ValueError('Select America before collecting')
    for role,labels in [('textbox',('Item name','Socket','Seller')),('spinbutton',('Plus','Min price','Max price'))]:
        if any(page.get_by_role(role,name=label,exact=True).input_value() for label in labels):
            raise ValueError('Clear all market filters except America')
    if any(clean(page.get_by_role('combobox',name=label,exact=True).inner_text())
           for label in ('Category','Subcategory','Quality')):
        raise ValueError('Clear category and quality filters')
    if page.get_by_role('button',name='Go to previous page',exact=True).is_enabled():
        raise ValueError('Start from page 1')
    initial = stable_metadata(metadata,check)
    if not 0 < initial[0] <= 50000:
        raise ValueError('Market count is empty or outside supported limits')
    data = dict(source=SOURCE,server='America',observed_at=clock(),initial_total=initial[0],
                initial_change=initial[1],pages=[],last_page=False)
    for number in range(1,(initial[0]+49)//50+1):
        settled()
        # Selected pagination alone is insufficient: wait for the refresh to settle too.
        page.get_by_role('button',name=f'page {number}',exact=True).wait_for(state='visible')
        current = metadata()
        if current!=initial:
            raise ValueError(f'Market changed during collection on page {number}: {initial!r} -> {current!r}; discard this snapshot')
        rows = page.get_by_role('table',name='Live market listings').get_by_role('row').evaluate_all(
            'rows => rows.slice(1).map(r => Array.from(r.cells).map(c => c.innerText))')
        data['pages'].append({'page':number,'rows':rows})
        next_page = page.get_by_role('button',name='Go to next page',exact=True)
        if not next_page.is_enabled():
            data['last_page'] = True
            break
        check()
        next_page.click()
    settled()
    data['final_total'],data['final_change'] = metadata()
    if not data['last_page'] or metadata()!=initial:
        raise ValueError(f'Market collection was incomplete or changed: {initial!r} -> {metadata()!r}, '
                         f'{len(data["pages"])} pages, last={data["last_page"]}')
    return data


def collect_market(*, destination='reports/merchants/market.json',
                   definitions_path=r'C:\Program Files\Classic Conquer 2.0\ini\itemtype.json',
                   stop=None, timeout=600):
    """One bounded headless collection. A blocked page fails without publishing."""
    try:
        from playwright.sync_api import sync_playwright, Error
    except ImportError:
        raise ValueError('Install the market extra and Playwright Chromium before collecting') from None
    deadline = time.monotonic()+timeout

    def check():
        if stop and stop.is_set():
            raise ValueError('Market collection stopped')
        if time.monotonic()>=deadline:
            raise ValueError('Market collection timed out; prices unchanged')

    definitions = json.loads(Path(definitions_path).read_text(encoding='utf-8'))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**browser_launch_options())
            try:
                page = browser.new_page()
                page.set_default_timeout(15000)
                response = page.goto(SOURCE,wait_until='domcontentloaded',timeout=30000)
                if response is None or response.status>=400:
                    raise ValueError('Market website denied access; prices unchanged')
                server = page.get_by_role('combobox',name='Server',exact=True)
                server.wait_for(state='visible')
                if server.inner_text().strip()!='America':
                    server.click()
                    page.get_by_role('option',name='America',exact=True).click()
                page.get_by_role('button',name='Apply filters',exact=True).click()
                # The site loads its market in successive batches. A displayed
                # page/count can precede completion of the outstanding requests.
                page.wait_for_load_state('networkidle',timeout=30000)
                data = collect_public(page.request,definitions,check=check)
                check()
                PriceHistory(Path(destination).with_name('price-history.sqlite3')).remember(MarketSnapshot(data))
                write_json(destination,data)
                return data
            finally:
                browser.close()
    except Error as error:
        # Browser exception text may contain page/session data. Do not log it.
        detail=str(error)
        if "Executable doesn't exist" in detail:
            raise ValueError('Market browser is not installed for this Windows account. Install Playwright Chromium.') from None
        if 'BrowserType.launch' in detail:
            raise ValueError('Market browser could not start in the desktop app.') from None
        raise ValueError('Browser collection failed or needs website verification; prices unchanged') from None
