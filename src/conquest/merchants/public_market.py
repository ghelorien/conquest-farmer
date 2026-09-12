"""Validate the public JSON responses used by the market website."""
import re
import time

from conquest.merchants.market import SOURCE, browser_pages

ENDPOINT = 'https://api.conqueronline.net/api/public/market/items'


def socket_label(value):
    if value == 'None':return 'No socket'
    if value == 'Empty':return value
    if not isinstance(value,str) or not re.fullmatch(
            r'(Normal|Refined|Super)(Phoenix|Dragon|Fury|Rainbow|Kylin|Violet|Moon|Tortoise)Gem',value):
        raise ValueError('Unknown public market socket')
    return re.sub(r'^(Normal|Refined|Super)',r'\1 ',value)


def normalize_pages(pages, definitions, *, observed_at):
    if not pages:raise ValueError('Empty public market response')
    first=pages[0]
    total=first.get('totalCount')
    if type(total) is not int or not 0<total<=50000:
        raise ValueError('Invalid public market count')
    count=(total+99)//100
    versions=first.get('servers')
    if (not isinstance(versions,list) or len(versions)!=1 or versions[0].get('server')!=0
            or versions[0].get('isAvailable') is not True or not versions[0].get('updatedAtUtc')):
        raise ValueError('America market is unavailable')
    if len(pages)!=count:raise ValueError('Public market pages missing')
    rows=[];seen=set()
    for number,page in enumerate(pages,1):
        if (page.get('page')!=number or page.get('pageSize')!=100 or page.get('totalCount')!=total
                or page.get('totalPages')!=count or page.get('servers')!=versions):
            raise ValueError('Public market changed during collection')
        items=page.get('items')
        if not isinstance(items,list) or len(items)!=min(100,total-(number-1)*100):
            raise ValueError('Incomplete public market page')
        for item in items:
            uid=item.get('itemId')
            if type(uid) is not int or uid<=0 or uid in seen or item.get('server')!=0:
                raise ValueError('Duplicate or invalid public market identity')
            seen.add(uid)
            for field in ('attributeName','itemMinorClass','sellerName'):
                if not isinstance(item.get(field),str) or not item[field].strip() or '\n' in item[field]:
                    raise ValueError('Incomplete public market attributes')
            if type(item.get('price')) is not int or item['price']<=0 or type(item.get('additionLevel')) is not int:
                raise ValueError('Invalid public market price or plus')
            rows.append([item['attributeName']+'\n'+item['itemMinorClass'],item.get('qualityName') or '—',
                         str(item['additionLevel']),socket_label(item.get('gem1'))+'\n'+socket_label(item.get('gem2')),
                         item['sellerName'],'America',str(item['price'])])
    # Reuse the same attribute/quantity normalizer after validating native API
    # pagination. These 50-row blocks are an internal normalization format.
    data=browser_pages(dict(source=SOURCE,server='America',observed_at=observed_at,
        initial_total=total,final_total=total,initial_change=versions,final_change=versions,last_page=True,
        pages=[{'page':i//50+1,'rows':rows[i:i+50]} for i in range(0,total,50)]),definitions)
    data['collection_method']='public-market-api'
    data['server_updated_at']=versions[0]['updatedAtUtc']
    return data


def collect_public(request, definitions, *, check=lambda:None):
    started=time.time()
    def fetch(number):
        check()
        response=request.get(ENDPOINT,params={'server':0,'sort':4,'direction':0,'page':number,'pageSize':100},timeout=15000)
        if not response.ok or 'json' not in response.headers.get('content-type',''):
            raise ValueError('Public market request denied or unavailable')
        return response.json()
    first=fetch(1)
    count=first.get('totalPages')
    if type(count) is not int or not 1<=count<=500:
        raise ValueError('Invalid public market pagination')
    pages=[first]
    for number in range(2,count+1):
        current=fetch(number)
        if current.get('servers')!=first.get('servers') or current.get('totalCount')!=first.get('totalCount'):
            raise ValueError('Public market changed during collection')
        pages.append(current)
    result=normalize_pages(pages,definitions,observed_at=started)
    final=fetch(1)
    if final!=first:
        raise ValueError('Public market changed before verification completed')
    check()
    return result
