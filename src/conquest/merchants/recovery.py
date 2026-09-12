"""Durable, bounded recovery independent of merchant pause intent."""
import time
from conquest.capture import CaptureUnavailable


class Recovery:
    def __init__(self, character, journal, *, clock=time.time, limit=3):
        self.character,self.journal,self.clock,self.limit = character,journal,clock,limit

    def state(self):
        return self.journal.get(self.character,'recovery',{'attempts':0,'next_attempt':0,'state':'connected'})

    def retry(self):
        self.journal.set(self.character,'recovery',{'attempts':0,'next_attempt':0,'state':'retry_requested'})

    def attempt(self, action):
        state = self.state()
        if self.clock() < state['next_attempt']:
            return False
        if state['attempts'] >= self.limit:
            raise ValueError('Reconnect retries exhausted; use Retry reconnect')
        # Acquire foreground ownership before invoking this method. A crash
        # after dispatch must still consume an attempt after app restart.
        state.update(attempts=state['attempts']+1,state='submitted',
            next_attempt=self.clock()+(15,30,60)[min(state['attempts'],2)])
        self.journal.set(self.character,'recovery',state)
        try:
            action()
        except CaptureUnavailable:
            state.update(state='waiting_for_input')
            self.journal.set(self.character,'recovery',state)
            raise
        except Exception:
            state.update(state='needs_attention')
            self.journal.set(self.character,'recovery',state)
            # Login routines can hold secrets; never persist their exceptions.
            raise ValueError('Recovery failed; check local credentials and qualified login state') from None
        return True

    def verified(self):
        state = self.state()
        if state['state'] != 'connected':
            self.journal.event(self.character,'recovery_verified',attempts=state['attempts'])
        self.journal.set(self.character,'recovery',{'attempts':0,'next_attempt':0,'state':'connected'})


def credential_path(character):
    from pathlib import Path
    from conquest.merchants.journal import character_name
    return Path('.runtime/merchants') / character_name(character).lower() / 'account.dpapi'


def save_credentials(character, username, password):
    import json
    import win32crypt
    if any(not isinstance(v,str) or not 1 <= len(v) <= 256 for v in (username,password)):
        raise ValueError('Both login fields are required (maximum 256 characters)')
    path = credential_path(character)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix('.tmp.dpapi')
    temporary.write_bytes(win32crypt.CryptProtectData(
        json.dumps({'username':username,'password':password}).encode(),
        f'Conquest {character}',None,None,None,0))
    temporary.replace(path)
