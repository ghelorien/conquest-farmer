"""Deterministic, seller-balanced pricing; no game input or network effects."""
from dataclasses import dataclass, replace
from fractions import Fraction
from statistics import median
import math
import re
import time

OWNED = frozenset(('spiritual', 'dutch'))
SERVER = 'America'
# The fingerprint-pinned booth dialog enables Confirm only for 1..999,999,999
# (renderer RVA 0x75c0c: unsigned (amount - 1) <= 0x3b9ac9fe).
MAX_BOOTH_PRICE = 999_999_999
COMPOSITION_QUALITIES = frozenset(('Fixed','Normal','Refined','Unique','Elite'))


def comparison_key(key):
    # Only the qualities the user explicitly grouped, only on + equipment.
    # Socket contents, type, currency and plus remain part of the key.
    return replace(key,quality='Composition') if key.plus>0 and key.quality in COMPOSITION_QUALITIES else key


def historical_quote(history,key):
    matches = [v for k,v in history.items() if comparison_key(k)==comparison_key(key)]
    if not matches:
        return None
    latest = max(v['observed_at'] for v in matches)
    return min((v for v in matches if v['observed_at']==latest),key=lambda v:Fraction(v['unit_price']))


def validate_booth_price(price):
    if type(price) is not int or not 1 <= price <= MAX_BOOTH_PRICE:
        raise ValueError('Target is outside the supported booth silver range')


def parse_booth_price(raw):
    """Parse the live client's decimal or comma-grouped price field strictly."""
    if not isinstance(raw,bytes) or not re.fullmatch(rb'(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)',raw):
        raise ValueError('Unrecognized booth price field')
    value=int(raw.replace(b',',b''))
    validate_booth_price(value)
    return value


def wait_booth_price(read, expected, check, *, clock=time.monotonic, sleep=time.sleep, timeout=2):
    """Wait for queued native keystrokes to render; never repeat the input."""
    validate_booth_price(expected)
    deadline=clock()+timeout
    while True:
        check()
        try:
            entered=parse_booth_price(read())
        except ValueError:
            entered=None
        if entered==expected:
            return entered
        if clock()>=deadline:
            raise ValueError('Entered booth price was not verified before timeout; no listing submitted')
        sleep(.02)


@dataclass(frozen=True)
class ItemKey:
    category: str
    quality: str
    plus: int
    sockets: tuple[str, str]
    currency: str = 'silver'

    def __post_init__(self):
        if not self.category or not self.quality or type(self.plus) is not int or not 0 <= self.plus <= 12:
            raise ValueError('Incomplete comparison attributes')
        if len(self.sockets) != 2 or any(not isinstance(s, str) or not s for s in self.sockets):
            raise ValueError('Both socket attributes must be known')
        if self.currency != 'silver':
            raise ValueError('Only verified silver listings are supported')


@dataclass(frozen=True)
class Listing:
    seller: str
    key: ItemKey
    price: int
    quantity: int = 1
    server: str = SERVER

    def __post_init__(self):
        if not self.seller or type(self.price) is not int or type(self.quantity) is not int or min(self.price, self.quantity) <= 0:
            raise ValueError('Invalid listing')


@dataclass(frozen=True)
class Decision:
    price: int | None
    reason: str
    sellers: int
    excluded: tuple[str, ...] = ()
    reference: str | None = None
    source_observed_at: float | None = None


def price_item(key, listings, *, quantity=1):
    if type(quantity) is not int or quantity <= 0:
        raise ValueError('Quantity must be a positive integer')
    sellers, owned = {}, {}
    for row in listings:
        seller = row.seller.casefold()
        if row.server != SERVER or comparison_key(row.key) != comparison_key(key):
            continue
        unit = Fraction(row.price, row.quantity)
        group = owned if seller in OWNED else sellers
        group[seller] = min(group.get(seller, unit), unit)
    # A single seller can price an item. The outlier test still needs three
    # OTHER independent sellers; a sparse category must not be discarded.
    rejected = tuple(sorted(name for name, price in sellers.items()
        if len(sellers) >= 4 and price < median([p for other, p in sellers.items() if other != name]) / 2))
    valid = {name: price for name, price in sellers.items() if name not in rejected}
    if not valid and not owned:
        return Decision(None, 'No comparable live listing', len(sellers), rejected)
    reference = min(valid.values()) if valid else None
    reason = '1% below lowest valid competitor'
    if owned and (reference is None or min(owned.values()) <= reference):
        reference = min(owned.values())
        # Never apply the discount to our own floor. Unequal stack sizes may
        # require rounding UP one silver to avoid undercutting our unit price.
        price = math.ceil(reference * quantity)
        reason = 'Match lowest Spiritual/Dutch price; already lowest valid offer'
    else:
        price = math.floor(reference * quantity * Fraction(99, 100))
    if price < 1 or price > MAX_BOOTH_PRICE:
        return Decision(None, 'Target is outside the supported silver range', len(sellers), rejected)
    return Decision(price, reason, len(sellers), rejected, str(reference))


def quote_item(key, listings, *, quantity=1, history=None, allow_plus_conversion=True):
    """Live exact match, last exact observation, then +2 = three equivalent +1s."""
    rows = list(listings)
    direct = price_item(key,rows,quantity=quantity)
    if any(comparison_key(r.key) == comparison_key(key) and r.server == SERVER for r in rows):
        return direct  # An invalid live target must not silently use a fallback.
    history = history or {}
    saved = historical_quote(history,key)
    if saved:
        reference = Fraction(saved['unit_price'])
        reason = 'Last observed comparable price (no additional discount)'
        observed_at = saved['observed_at']
    elif key.plus == 2 and allow_plus_conversion:
        base_key = replace(key,plus=1)
        base = price_item(base_key,rows)
        saved = historical_quote(history,base_key)
        if base.reference is not None:
            reference = Fraction(base.reference)*3
            reason = '+2 valued at 3 × the live equivalent +1 price'
            observed_at = None
        elif saved and not any(comparison_key(r.key) == comparison_key(base_key) and r.server == SERVER for r in rows):
            reference = Fraction(saved['unit_price'])*3
            reason = '+2 valued at 3 × the last observed equivalent +1 price'
            observed_at = saved['observed_at']
        else:
            return direct
    else:
        return direct
    price = math.ceil(reference*quantity)
    if not 1 <= price <= MAX_BOOTH_PRICE:
        return Decision(None,'Target is outside the supported silver range',0)
    return Decision(price,reason,0,reference=str(reference),source_observed_at=observed_at)


def socket_name(value):
    if value == 0:
        return 'No socket'
    if value == 255:
        return 'Empty'
    gems = {1: 'Phoenix', 2: 'Dragon', 3: 'Fury', 4: 'Rainbow', 5: 'Kylin', 6: 'Violet', 7: 'Moon', 8: 'Tortoise'}
    family, rank = divmod(value, 10)
    # Game gem values are 1/2/3, 11/12/13, ... .
    family += 1
    if family not in gems or rank not in (1, 2, 3):
        raise ValueError('Unknown socket gem')
    return f'{("Normal", "Refined", "Super")[rank-1]} {gems[family]}Gem'


def quality(kind):
    return {0: 'Fixed', 6: 'Refined', 7: 'Unique', 8: 'Elite', 9: 'Super'}.get(kind % 10, 'Normal')
