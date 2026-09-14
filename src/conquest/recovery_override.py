"""Confirmation-bound closure for durable recovery holds.

The helper is intentionally input-free.  Callers supply a fresh, memory-read
observation and then replan from it; an override never invents a payment,
trade, warehouse receipt, or successful recovery result.
"""
import copy
import hashlib
import json
import time
import os
from contextlib import contextmanager
from pathlib import Path
from conquest.discord_notify import read_json, write_json


@contextmanager
def _lock(path):
    lock=Path(str(path)+'.override.lock');lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('a+b') as stream:
        stream.write(b'0');stream.flush();stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def _complete(path, intent):
    state=read_json(path)
    record=intent['record'];digest=record['original_evidence_digest']
    if state.get('operator_override')==record:return state
    if evidence_digest(state)!=digest:
        raise ValueError('Incident changed during override recovery; recheck before overriding')
    audit=Path(intent['audit']);audit.parent.mkdir(parents=True,exist_ok=True)
    existing=[]
    if audit.exists():
        for line in audit.read_text(encoding='utf-8').splitlines():
            try:existing.append(json.loads(line))
            except ValueError:continue
    if not any(row.get('original_evidence_digest')==digest and
               row.get('confirmation_reference')==record['confirmation_reference'] for row in existing):
        with audit.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(record,sort_keys=True)+'\n');stream.flush();os.fsync(stream.fileno())
    # The caller owns the route/controller exclusion; recheck after audit I/O.
    if evidence_digest(read_json(path))!=digest:
        raise ValueError('Incident changed during override; recheck before overriding')
    state.update(phase=intent['terminal_phase'],operator_override=record,replan_required=True)
    write_json(path,state)
    return state


def read_recovered(path):
    """Complete a previously confirmed journal write; never issue game input."""
    path=Path(path);intent_path=Path(str(path)+'.override-intent.json')
    if not intent_path.exists():return read_json(path)
    with _lock(path):
        intent=read_json(intent_path)
        if not intent:return read_json(path)
        state=read_json(path)
        record=intent['record']
        if state.get('operator_override')!=record and evidence_digest(state)!=record['original_evidence_digest']:
            # A different incident now occupies the journal. Preserve the old
            # decision for audit, but allow a new explicit confirmation.
            superseded=Path(str(intent_path)+'.'+evidence_digest(intent)+'.superseded')
            write_json(superseded,intent)
            intent_path.unlink(missing_ok=True)
            return state
        state=_complete(path,intent)
        intent_path.unlink(missing_ok=True)
        return state


def operator_override(path, *, pending_phases, operator_confirmed=False,
                      confirmation_reference=None, operator=None,
                      fresh_evidence=None, audit_path=None, incident=None,
                      terminal_phase='operator_overridden', incident_digest=None):
    if operator_confirmed is not True:
        raise ValueError('Operator confirmation is required for this incident')
    if not isinstance(confirmation_reference, str) or not confirmation_reference.strip():
        raise ValueError('A non-empty incident confirmation reference is required')
    if operator is not None and (not isinstance(operator, str) or not operator.strip()):
        raise ValueError('Operator must be a non-empty string when supplied')
    path=Path(path)
    read_recovered(path)
    with _lock(path):
        state=read_json(path)
        if state.get('phase')==terminal_phase:
            prior=state.get('operator_override') or {}
            if prior.get('confirmation_reference')!=confirmation_reference.strip():
                raise ValueError('Incident was already overridden with a different confirmation')
            return state
        if state.get('phase') not in set(pending_phases):
            raise ValueError('No unresolved recovery hold is active')
        original=copy.deepcopy(state);digest=evidence_digest(original)
        if incident_digest is not None and incident_digest != digest:
            raise ValueError('Incident evidence changed; recheck before overriding')
        record={'incident':incident or path.stem,'original_phase':state.get('phase'),
                'original_evidence_digest':digest,'original_state':original,
                'confirmation_reference':confirmation_reference.strip(),
                'operator':operator.strip() if isinstance(operator,str) else None,
                'confirmed_at':time.time(),'fresh_evidence':fresh_evidence or {}}
        intent={'record':record,'terminal_phase':terminal_phase,
                'audit':str(Path(audit_path or str(path)+'.audit.jsonl').resolve())}
        intent_path=Path(str(path)+'.override-intent.json')
        write_json(intent_path,intent)
        with intent_path.open('r+b') as stream:os.fsync(stream.fileno())
        result=_complete(path,intent)
        intent_path.unlink(missing_ok=True)
        return result


def evidence_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def preview_digest(value):
    return evidence_digest(value)
