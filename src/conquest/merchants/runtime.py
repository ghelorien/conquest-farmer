"""Concurrent memory observers; serialized merchant input and durable work."""
from conquest.merchants.capacity import available_slots
from conquest.character_context import installation_path, state_path
import json
from pathlib import Path
import threading
import time
from conquest.capture import CaptureUnavailable
from conquest.merchants.journal import CHARACTERS, Journal, character_name
from conquest.merchants.controller import MerchantController
from conquest.merchants.driver import MerchantDriver
from conquest.merchants.market import MarketSnapshot
from conquest.merchants.price_history import PriceHistory
from conquest.merchants.recovery import Recovery, credential_path


def make_observer(client, character):
    import yaml
    from conquest.embedded_observer import EmbeddedObserver
    from conquest.memory_health import HealthLayout
    from conquest.memory_entities import EntityLayout
    health = HealthLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-health-candidate.yaml').read_text()))
    entities = EntityLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-entities-candidate.yaml').read_text()))
    from conquest.character_context import merchant_context
    context=merchant_context(character)
    kwargs={'context':context} if context else {}
    observer = EmbeddedObserver(client.identity['pid'],client.hwnd,health,entities,character,**kwargs)
    if observer.adapter.identity != client.identity:
        observer.close()
        raise ValueError('Client identity changed during attachment')
    return observer


class MerchantRuntime:
    def __init__(self, catalog, coordinator, *, journal=None, observer_factory=make_observer,
                 market_path=state_path('reports/merchants/market.json'), qualification_dir=state_path('.runtime/merchants')):
        self.catalog,self.coordinator = catalog,coordinator
        self.journal = journal or Journal()
        from conquest.character_context import registry
        if registry():
            for character in CHARACTERS:
                if not self.journal.get(character,'profile_initialized',False):
                    self.journal.set(character,'enabled',False)
                    self.journal.set(character,'refill_enabled',False)
                    self.journal.set(character,'profile_initialized',True)
        self.observer_factory,self.market_path = observer_factory,Path(market_path)
        self.qualification_dir = Path(qualification_dir)
        self.lock,self.discovery_lock = threading.RLock(),threading.Lock()
        self.stop_event = threading.Event()
        from conquest.merchants.sales_report import SalesReportWorker
        self.sales_worker = SalesReportWorker(self.journal,self.stop_event)
        from conquest.merchants.market_refresh import MarketRefreshWorker
        self.market_worker=MarketRefreshWorker(self.journal,self.stop_event,self.market_path)
        self.observers,self.controllers,self.latest,self.errors = {},{},{},{}
        from conquest.client_attachment import AttachmentStatus
        self.attachments={c:AttachmentStatus() for c in CHARACTERS}
        self.launches,self.launch_owner = {},None
        self.return_drivers = {}
        self.recoveries = {c:Recovery(c,self.journal) for c in CHARACTERS}
        from conquest.merchants.shop_return import ShopReturn
        self.returns = {c:ShopReturn(c,self.journal) for c in CHARACTERS}
        from conquest.merchants.refill import RefillSchedule
        self.refills = {c:RefillSchedule(c,self.journal) for c in CHARACTERS}
        self.refilling,self.refill_revisions = {},{}
        self.refill_threads = {}
        self.connecting,self.connect_checks,self.connect_cancel={},{},{}
        for refill in self.refills.values():refill.state()
        self.threads = []
        self.handoff = None
        self.work_deadline = None

    def can_start_work(self,minimum_seconds=0):
        return self.work_deadline is None or time.time()+minimum_seconds < self.work_deadline

    def finish_handoff(self):
        # Submitted actions reconcile independently before any later input.
        # Expiry is not a completed capacity check. Keep its durable queue.
        for refill in self.refills.values():
            if refill.state().get('pending'):
                refill.pause_budget()
        self.work_deadline = None

    def start(self):
        if self.threads:
            return
        from conquest.merchants.market_guard import MarketGuard
        self.market_guard = MarketGuard(self)
        guard = threading.Thread(target=self.market_guard.run, daemon=True, name='merchant-market-safety')
        self.threads.append(guard); guard.start()
        for character in CHARACTERS:
            thread = threading.Thread(target=self.run,args=(character,),daemon=True,name=f'merchant-{character}')
            self.threads.append(thread);thread.start()
        thread = threading.Thread(target=self.sales_worker.run,daemon=True,name='merchant-sales-reports')
        self.threads.append(thread);thread.start()

        thread=threading.Thread(target=self.market_worker.run,daemon=True,name='merchant-market-refresh')
        self.threads.append(thread);thread.start()

    def enabled(self, character):
        from conquest.merchants.recovery_safety import active
        recovering = active(self,character) and not self.journal.get(character,'connect_hold',False)
        return (self.journal.get(character,'enabled',False) or recovering) and not self.coordinator.stopped

    def refill_enabled(self, character):
        return self.journal.get(character,'refill_enabled',True) and not self.coordinator.stopped

    def set_refill_enabled(self, character, enabled):
        character=character_name(character)
        if type(enabled) is not bool:raise ValueError('enabled must be boolean')
        self.journal.set(character,'refill_enabled',enabled)
        self.journal.event(character,'refill_resumed' if enabled else 'refill_paused')

    def input_allowed(self, character):
        delivery_window=getattr(self,'delivery_window',None)
        purpose=getattr(self.coordinator,'purpose',None)
        if character in self.connecting:
            return (not delivery_window and not getattr(self,'refill_window',None)
                    and purpose in ('connect','connect_launch')
                    and self.connecting[character]==threading.get_ident()
                    and self.connect_checks.get(character,lambda:False)())
        if delivery_window:
            from conquest.merchants.delivery_reservation import active
            reserved=active(self.journal,character)
            return (purpose=='trade' and character not in self.refilling and self.enabled(character)
                    and bool(reserved and reserved.get('request_id')==delivery_window))
        if getattr(self,'refill_window',None) and (purpose!='refill' or character not in self.refilling):
            return False
        if character in self.refilling:
            return (self.refill_enabled(character)
                    and purpose=='refill'
                    and self.refill_threads.get(character)==threading.get_ident()
                    and self.refilling[character]==self.refill_revisions.get(character,0))
        return self.enabled(character)

    def invalidate_refill(self, character):
        # Revoke input aimed at an old surface without pausing future checks.
        self.refill_revisions[character]=self.refill_revisions.get(character,0)+1

    def apply_refill(self, character, controller, plan):
        from conquest.merchants.refill import RefillController
        runner=RefillController(character,self.journal,controller.driver,self.coordinator,clock=controller.clock)
        self.refilling[character]=self.refill_revisions.get(character,0)
        self.refill_threads[character]=threading.get_ident()
        try:return runner.apply_price(plan)
        finally:
            self.refilling.pop(character,None)
            self.refill_threads.pop(character,None)

    def enable(self, character, enabled):
        character = character_name(character)
        if type(enabled) is not bool:
            raise ValueError('enabled must be boolean')
        self.journal.set(character,'enabled',enabled)
        if enabled:
            self.journal.set(character,'connect_hold',False)
            self.journal.set(character,'new_stock',True)
            if (self.journal.get(character,'attention') or {}).get('kind')=='unexpected':
                self.journal.set(character,'attention',None)
        self.journal.event(character,'resumed' if enabled else 'paused')

    def global_stop(self):
        self.coordinator.stop()
        for cancel in self.connect_cancel.values():cancel.set()
        self.handoff = None
        for character in CHARACTERS:
            self.enable(character,False)
            self.set_refill_enabled(character,False)

    def list_once(self, character, request_id):
        state,created = self.journal.request_once(character,request_id)
        if created:
            self.market_worker.request(character,'batch:'+state['request_id'])
            self.coordinator.resume()
        return state

    def attach(self, character):
        from conquest.memory_life import read_life
        status=self.attachments[character];status.enter('discovery')
        # Never identify an account by title, list order, PID alone, or a stale
        # name from a disconnected process. Check the memory identity first.
        with self.discovery_lock:
            matches = []
            access_failed = False
            for client in self.catalog.windows():
                if any(o.adapter.identity == client.identity for o in self.observers.values()):
                    continue
                observer = None
                try:
                    from conquest.reconnect import login_screen
                    at_login=login_screen(client.hwnd)
                    if at_login and client.identity!=self.journal.get(character,'last_identity'):
                        continue
                    status.enter('access',pid=client.identity['pid'],hwnd=client.hwnd,
                                 process_created=client.identity.get('creation_time_100ns'))
                    observer = self.observer_factory(client,character)
                    status.enter('identity')
                    if not at_login:read_life(observer.adapter,observer.health_layout,character)
                    matches.append(observer)
                except Exception as error:
                    status.fail(error)
                    access_failed |= isinstance(error,OSError)
                    if observer:
                        observer.close()
            if len(matches) != 1:
                for observer in matches:
                    observer.close()
                raise ValueError('Run the app as administrator to read elevated clients' if access_failed else
                    f'{character}: expected one verified logged-in client, found {len(matches)}')
            try:self.bind(character,matches[0])
            except Exception as error:
                matches[0].close()
                status.fail(error)
                self.journal.set(character,'attachment',status.snapshot())
                raise
            if (not login_screen(matches[0].operations.target.hwnd)
                    and read_life(matches[0].adapter,matches[0].health_layout,character).map_id==1002):
                self.returns[character].begin()

    def bind(self, character, observer):
        from conquest.character_context import merchant_context, merchant_directory
        context=merchant_context(character)
        status=getattr(self,'attachments',{}).get(character)
        if context:
            from conquest.client_attachment import verify_observer, remember_installation
            # Login candidates may not yet expose a character. They remain
            # unbound until the identity can be verified; no saved UID is reused.
            from conquest.reconnect import login_screen
            if not login_screen(observer.operations.target.hwnd):verify_observer(context,observer)
            try:remember_installation(context,observer.adapter.identity['path'])
            except ValueError:pass  # Hosting/reading do not require terrain files.
        previous=self.journal.get(character,'last_identity')
        if previous and previous!=observer.adapter.identity:
            self.returns[character].begin()
        directory=merchant_directory(character) if context else self.qualification_dir/character.lower()
        driver = MerchantDriver(observer,directory/'qualification.json',self.coordinator)
        with self.lock:
            self.observers[character] = observer
            self.controllers[character] = MerchantController(character,self.journal,driver,self.coordinator)
            from conquest.merchants.return_driver import ReturnDriver
            self.return_drivers[character] = ReturnDriver(driver)
        self.journal.set(character,'last_identity',observer.adapter.identity)
        if status:
            status.enter('memory',pid=observer.adapter.identity['pid'])
            self.journal.set(character,'attachment',status.snapshot())

    def disconnected(self, character):
        from conquest.reconnect import login_screen
        observer = self.observers.get(character)
        if not observer:
            return False
        observer.adapter.assert_identity()
        return login_screen(observer.operations.target.hwnd)

    def recover(self, character, *, crashed=False):
        from conquest.merchants.recovery_safety import arm, submitted
        arm(self,character)
        if not self.enabled(character):
            raise CaptureUnavailable('Paused; recovery will not change manual intent')
        if not credential_path(character).exists():
            raise ValueError('Configure this merchant’s encrypted credentials locally')
        if not self.coordinator.safe_to_yield():
            with self.lock:
                if self.handoff is None:
                    self.handoff=f'merchant-recovery:{character}:{int(time.time()*1000)}'
            raise CaptureUnavailable('Waiting for a safe farmer handoff')
        if crashed:
            from conquest.client_wrapper import LaunchWatch
            with self.discovery_lock:
                if self.launch_owner not in (None,character):
                    raise CaptureUnavailable('Waiting for the other merchant launcher')
                watch = self.launches.get(character)
                if watch and watch.pending:
                    candidate = watch.poll()
                    if candidate:
                        self.bind(character,self.observer_factory(candidate,character))
                        self.launch_owner = None
                    elif not watch.pending:
                        self.launch_owner = None
                        raise ValueError('Launcher did not produce one verified candidate; retry reconnect')
                    return
                from conquest.character_context import merchant_installation
                from conquest.merchants.client_launch import installed_client
                command,cwd=installed_client(merchant_installation(character))
                watch = LaunchWatch(self.catalog,command,cwd=cwd)
                with self.coordinator.lease(character,purpose='connect_launch'):
                    submitted(self,character)
                    if self.recoveries[character].attempt(watch.start):
                        self.launches[character],self.launch_owner = watch,character
            return
        driver = self.controllers[character].driver
        driver.require_qualified('login')
        from conquest.reconnect import submit_login
        with self.coordinator.lease(character,purpose='connect'):
            submitted(self,character)
            self.recoveries[character].attempt(lambda:submit_login(driver.target,
                credential_path(character),session=driver.observer.adapter))

    def step(self, character):
        if character in self.connecting:return
        refill_only=bool(getattr(self,'refill_window',None))
        observer = self.observers.get(character)
        if observer:
            try:
                observer.adapter.assert_identity()
            except (OSError,ValueError):
                self.returns[character].begin()
                observer.close()
                with self.lock:
                    self.observers.pop(character,None);self.controllers.pop(character,None)
                    self.return_drivers.pop(character,None)
                    self.latest.pop(character,None)
                self.journal.set(character,'accepted_request',None)
                self.journal.set(character,'crashed',True)
                observer = None
        if observer is None:
            if refill_only:return
            try:
                self.attach(character)
            except ValueError:
                if self.journal.get(character,'crashed',False):
                    self.recover(character,crashed=True)
                    return
                raise
        if self.disconnected(character):
            self.returns[character].begin()
            with self.lock:
                self.latest.pop(character,None)
            if refill_only:return
            self.recover(character)
            return
        controller = self.controllers[character]
        returning=self.returns[character].state()
        returning=bool(returning and returning['phase']!='complete')
        with self.observers[character].lock:
            snapshot = controller.driver.memory.read(recovery=True) if returning else controller.driver.read()
        with self.lock:
            self.latest[character] = snapshot
        from conquest.merchants.sales import observe
        observe(self.journal,snapshot)
        controller.reconcile(snapshot)
        if self.journal.get(character,'connect_hold',False):return
        if not self.can_start_work():
            return
        from conquest.merchants.delivery_reservation import active as reserved_delivery
        reservation=reserved_delivery(self.journal,character)
        if reservation:
            # Never list new arrivals before the farmer has verified its own
            # inventory. Partial offers must not be accepted as full batches.
            if refill_only or not self.enabled(character):
                return
            if snapshot.get('request'):
                controller.accept_request(snapshot)
            elif snapshot.get('trade'):
                controller.accept_delivery()
            return
        if getattr(self,'delivery_window',None):
            # The farmer reserves its exact batch after the safe grant. Do
            # not race that reservation by listing or moving merchant stock.
            return
        # Preserve the operator's explicit merchant permission before an old
        # shop-return incident can narrow the rest of this cycle to held-stock
        # refill. That internal mode may still clear an unrelated prompt, but
        # a real refill-only window, pause or Global Stop never may.
        decline_enabled=(not refill_only and not self.coordinator.stopped
                         and self.journal.get(character,'enabled',False) is True)
        pending_scan=self.journal.get(character,'scan',{})
        if (decline_enabled and pending_scan.get('pending') and pending_scan.get('one_time')
                and (snapshot.get('request') or snapshot.get('trade'))):
            raise CaptureUnavailable('One-time listing waits for the trade window to close; no trade will be accepted')
        decline_state=self.journal.get(character,'unrelated_request_decline') or {}
        if (decline_enabled and (snapshot.get('request') or decline_state.get('phase')=='submitted')
                and not self.coordinator.safe_to_yield()):
            with self.lock:
                if self.handoff is None:self.handoff=f'merchants:{int(time.time()*1000)}'
        from conquest.merchants.unrelated_request import decline_unrelated_request
        if decline_unrelated_request(controller,snapshot,operations_enabled=decline_enabled):return
        from conquest.merchants.held_stock_refill import allowed as held_refill_allowed
        held_refill = returning and held_refill_allowed(self,character,snapshot)
        if refill_only and ((returning and not held_refill) or snapshot.get('trade') or snapshot.get('request')
                            or (self.recoveries[character].state()['state']!='connected' and not held_refill)):
            return
        self.returns[character].remember(snapshot)
        if held_refill: refill_only = True
        if returning and not held_refill:
            if (self.returns[character].state() or {}).get('phase')=='needs_attention':
                return  # Historical incidents cannot request repeated recovery handoffs.
            if not self.coordinator.safe_to_yield() and self.enabled(character):
                with self.lock:
                    if self.handoff is None:self.handoff=f'merchant-return:{character}:{int(time.time()*1000)}'
            if not self.returns[character].step(snapshot,controller,self.return_drivers[character]):
                return
        if not held_refill and self.recoveries[character].state()['state'] != 'connected':
            if not snapshot['booth_open']:
                raise ValueError('Open and verify the merchant booth before resuming recovery')
            self.recoveries[character].verified()
            self.journal.set(character,'crashed',False)
        operations_enabled = self.enabled(character) and not refill_only
        refill = self.refills[character]
        refill_due = self.refill_enabled(character) and refill.due()
        if not operations_enabled and not refill_due:
            return
        if (snapshot.get('request') or snapshot.get('trade') or self.journal.get(character,'scan',{}).get('pending')
                or self.journal.get(character,'new_stock',False)) and not self.coordinator.safe_to_yield():
            with self.lock:
                if self.handoff is None:
                    self.handoff = f'merchants:{int(time.time()*1000)}'
        scan = self.journal.get(character,'scan',{}) if operations_enabled else {}
        one_time = scan.get('pending') and scan.get('one_time')
        if snapshot.get('request'):
            if operations_enabled:controller.accept_request(snapshot)
            elif refill_due:raise CaptureUnavailable('Inventory refill waits for the trade request to close')
            return
        if snapshot.get('trade'):
            if operations_enabled:controller.accept_delivery()
            elif refill_due:raise CaptureUnavailable('Inventory refill waits for the trade window to close')
            return
        if scan.get('pending') and self.market_worker.state(character).get('pending'):
            raise CaptureUnavailable('Fetching fresh America market prices; listing starts after the download')
        self.journal.set(character,'accepted_request',None)
        new_stock = operations_enabled and self.journal.get(character,'new_stock',False)
        marker = {'inventory':[i['uid'] for i in snapshot['inventory'] if not i['bound']],
                  'booth_count':len(snapshot['booth'])}
        if operations_enabled and marker != self.journal.get(character,'stock_marker') and marker['inventory']:
            new_stock = True
        refill_due = refill_due and not one_time
        if refill_due and (not marker['inventory'] or marker['booth_count']>=32):
            saved=refill.state()
            refill.complete('no_stock' if not marker['inventory'] else 'booth_full',
                            listed=saved.get('listed',0) if saved.get('pending') else 0,
                            deferred=len(marker['inventory']))
            refill_due = False
        if not scan.get('pending') and not new_stock and not refill_due:
            return
        history = PriceHistory(self.market_path.with_name('price-history.sqlite3'))
        if refill_due:
            if not snapshot['booth_open']:
                raise ValueError('Fifteen-minute refill needs the verified own booth open')
            refill.start()
            from conquest.merchants.refill import HistoricalComparisons
            market = HistoricalComparisons(history.catalog())
        else:
            # Explicit scans still require fresh, complete public market data.
            # Ordinary new-stock work may reuse history if the market has expired.
            try:
                market = MarketSnapshot(json.loads(self.market_path.read_text(encoding='utf-8')))
            except (OSError,ValueError):
                if scan.get('pending'):
                    raise ValueError('Waiting for a fresh complete America market scan') from None
                from conquest.merchants.refill import HistoricalComparisons
                market = HistoricalComparisons(history.catalog())
            else:
                history.remember(market)
        market.history_catalog = history.catalog()
        with self.lock:
            owned_snapshots = [s for c,s in self.latest.items()
                if c in self.observers and s['identity'] == self.observers[c].adapter.identity]
        plans = controller.plan(snapshot,market,inventory_only=refill_due or not scan.get('pending'),
                                owned_snapshots=owned_snapshots,history=history.quotes())
        self.journal.set(character,'comparisons',plans)
        changed = 0
        prior_listed=refill.state().get('listed',0) if refill_due else 0
        booth_count = len(snapshot['booth'])
        progress=None
        if scan.get('pending') and not refill_due:
            old=self.journal.get(character,'batch_progress',{})
            progress={'request_id':scan['request_id'],'changed':old.get('changed',0) if old.get('request_id')==scan['request_id'] else 0,
                      'remaining':sum(p['price'] is not None and p['price']!=p.get('old_price') for p in plans),
                      'phase':'applying','item':None}
            self.journal.set(character,'batch_progress',progress)
        if refill_due and any(p['price'] is not None for p in plans) and not self.coordinator.safe_to_yield():
            with self.lock:
                if self.handoff is None:self.handoff=f'merchant-refill:{character}:{int(time.time()*1000)}'
        budget_exhausted=False
        if refill_due:
            refill.checkpoint([p['uid'] for p in plans])
        for index,plan in enumerate(plans):
            if not self.can_start_work(3):
                budget_exhausted=True
                break
            if refill_due:
                refill.checkpoint([p['uid'] for p in plans[index:]],listed=prior_listed+changed)
            if plan['price'] is not None and plan['price'] != plan.get('old_price'):
                if plan.get('old_price') is None and booth_count>=32:
                    continue
                if progress is not None:
                    progress['item']=plan['name'];self.journal.set(character,'batch_progress',progress)
                after = self.apply_refill(character,controller,plan) if refill_due else controller.apply_price(plan)
                if after:
                    changed += 1
                    if refill_due:
                        refill.checkpoint([p['uid'] for p in plans[index+1:]],listed=prior_listed+changed)
                    if progress is not None:
                        progress.update(changed=progress['changed']+1,remaining=max(0,progress['remaining']-1))
                        self.journal.set(character,'batch_progress',progress)
                    booth_count = len(after['booth'])
                    with self.lock:
                        self.latest[character] = after
        # Unlistable excess stays queued, but do not repeatedly input against
        # an unchanged full booth. A later capacity/market change retries it.
        current = controller.driver.read()
        deferred = [p if p['price'] is None else
            {**p,'reason':'Waiting for available booth space or qualified input'} for p in plans
            if p['price'] is None or any(i['uid']==p['uid'] for i in current['inventory'])]
        self.journal.set(character,'deferred',deferred)
        # Every carried item stays visible in the durable queue, including
        # priced items that could not fit in the booth.
        carried = [i['uid'] for i in current['inventory'] if not i['bound']]
        queued = [p['uid'] for p in plans if p['uid'] in carried]
        queued.extend(uid for uid in carried if uid not in queued)
        self.journal.set(character,'inventory_queue',queued)
        self.journal.set(character,'stock_marker',{'inventory':carried,'booth_count':len(current['booth'])})
        self.journal.set(character,'new_stock',False)
        if refill_due:
            if budget_exhausted:
                refill.checkpoint(queued,listed=prior_listed+changed,deferred=len(deferred))
                refill.pause_budget()
            else:
                refill.complete('completed',listed=prior_listed+changed,deferred=len(deferred))
        elif scan.get('pending'):
            self.journal.complete_scan(character,scan['request_id'],changed=progress['changed'] if progress else changed,deferred=len(deferred))
            if progress is not None:
                self.journal.set(character,'batch_progress',{**progress,'phase':'completed','item':None})

    def run(self, character):
        while not self.stop_event.is_set():
            try:
                from contextlib import nullcontext
                fence=getattr(self.coordinator,'fence',None)
                with fence.bind_worker(fence.capture()) if fence else nullcontext():
                    self.step(character)
                with self.lock:
                    self.errors.pop(character,None)
            except (ValueError,OSError,CaptureUnavailable) as error:
                note = str(error)
                with self.lock:
                    previous = self.errors.get(character)
                    entry = previous if previous and previous['note']==note else {'note':note,'since':time.time(),'notified':False}
                    if not isinstance(error,CaptureUnavailable) and time.time()-entry['since']>=60 and not entry['notified']:
                        self.journal.event(character,'persistent_failure',note=note)
                        entry['notified'] = True
                    self.errors[character] = entry
            except Exception:
                # Keep the other character and UI alive, with no exception
                # text that could disclose an account or notifier secret.
                self.enable(character,False)
                self.set_refill_enabled(character,False)
                self.journal.set(character,'attention',{'kind':'unexpected',
                    'note':'Unexpected merchant failure; automatically paused. Check diagnostics and resume when ready.'})
                with self.lock:
                    self.errors[character] = {'note':'Unexpected merchant failure; paused. Check qualification and diagnostics.','since':time.time()}
            self.stop_event.wait(1)
        observer = self.observers.pop(character,None)
        if observer:
            observer.close()

    def status(self):
        with self.lock:
            result = {}
            for character in CHARACTERS:
                snapshot = self.latest.get(character)
                fresh = bool(snapshot and 0 <= time.time()-snapshot['timestamp'] <= 5)
                error = self.errors.get(character)
                scan = self.journal.get(character,'scan',{})
                qualification = {}
                controller = self.controllers.get(character)
                return_state=self.returns[character].state()
                returning=bool(return_state and return_state['phase']!='complete')
                from conquest.merchants.held_stock_refill import market_ready
                current_market_ready=market_ready(self,character,snapshot)
                historical_error=bool(error and return_state and error.get('note')==return_state.get('note'))
                for capability in ('trade_request','trade','booth_input','login','market_return','booth_setup',
                                   'booth_panel','inventory_panel'):
                    try:
                        if controller is None:
                            raise ValueError('Not attached')
                        controller.driver.require_qualified(capability)
                        qualification[capability] = True
                    except (ValueError,OSError):
                        qualification[capability] = False
                result[character] = {'enabled':self.enabled(character),'connected':fresh,
                    'input_active':self.coordinator.owner==character,
                    'activity':error['note'] if error else ('Returning to shop: '+return_state['phase'].replace('_',' ')
                        if returning and self.enabled(character) else 'Ready' if self.enabled(character) else 'Paused'),
                    'snapshot':snapshot if fresh else None,'error':error,'scan':scan,
                    'capacity':available_slots(snapshot) if fresh else None,
                    'ready':fresh and self.enabled(character) and (not returning or current_market_ready) and (not error or error.get('note')=='Waiting for a safe farmer handoff' or current_market_ready and historical_error) and available_slots(snapshot)>0
                        and qualification['trade_request'] and qualification['trade'] and not self.journal.pending(character),
                    'qualification':qualification,
                    'credentials_saved':credential_path(character).exists(),
                    'needs_attention':self.journal.get(character,'attention'),
                    'pending':self.journal.pending(character),'recovery':self.recoveries[character].state(),
                    'recovery_safety':self.journal.get(character,'recovery_safety'),
                    'shop_return':self.returns[character].state()}
                result[character]['connect_market']=self.journal.get(character,'connect_market')
                result[character]['profile_id']=getattr(character,'profile_id',None)
                result[character]['attachment']=self.attachments[character].snapshot()
                result[character]['refill'] = {**self.refills[character].state(),'enabled':self.refill_enabled(character)}
                if current_market_ready and (not error or historical_error):
                    doing='Refilling current inventory' if character in self.refilling else 'Current inventory ready'
                    result[character]['activity']=doing+'; earlier recovery incident remains unresolved'
                result[character]['market_refresh']=self.market_worker.state(character)
                result[character]['batch_progress']=self.journal.get(character,'batch_progress',{})
            projection=getattr(self,'status_projection',None)
            return projection(result) if projection else result

    def close(self):
        # Stop on close, preserve pause/resume intent for the next app launch.
        self.coordinator.stop()
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=2)
