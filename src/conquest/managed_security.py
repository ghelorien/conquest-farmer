"""Same-account Windows access for newly created managed state only.

An elevated token's default owner can be Administrators. OWNER RIGHTS therefore
does not give the same account's filtered token access to its own state. Pin the
TokenUser SID instead; never repair or replace an existing tree's ACL implicitly.
"""
from pathlib import Path
import os
import stat

MODIFY = 0x001301FF  # Read/write/execute/delete children; no WRITE_DAC/WRITE_OWNER.
FULL = 0x001F01FF
INHERIT = 3  # OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE


def _real(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if os.path.lexists(part):
            info = os.lstat(part)
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Managed state cannot use reparse points')
    return path


def _windows():
    import win32api
    import win32con
    import win32security
    return win32api, win32con, win32security


def _user_sid(api, constants, security):
    token = security.OpenProcessToken(api.GetCurrentProcess(), constants.TOKEN_QUERY)
    try:
        sid = security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()
    value = security.ConvertSidToStringSid(sid)
    if not value.startswith('S-1-5-21-'):
        raise ValueError('Managed state requires a local interactive account SID')
    return sid


def provision_new(path, *, directory):
    """Protect an object the caller just created, before any payload is written."""
    path = _real(path)
    if os.name != 'nt':
        path.chmod(0o700 if directory else 0o600)
        return
    api, constants, security = _windows()
    try:
        sid = _user_sid(api, constants, security)
        flags = INHERIT if directory else 0
        expected = [(security.ConvertSidToStringSid(sid), MODIFY),
                    ('S-1-5-18', FULL), ('S-1-5-32-544', FULL)]
        acl = security.ACL()
        for identity, access in expected:
            acl.AddAccessAllowedAceEx(security.ACL_REVISION, flags, access,
                                     security.ConvertStringSidToSid(identity))
        security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
            security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, acl, None)
        descriptor = security.GetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
                                                   security.DACL_SECURITY_INFORMATION)
        actual = descriptor.GetSecurityDescriptorDacl()
        rows = [] if actual is None else [actual.GetAce(i) for i in range(actual.GetAceCount())]
        control, _revision = descriptor.GetSecurityDescriptorControl()
        if (not control & security.SE_DACL_PROTECTED or len(rows) != len(expected)
                or any(kind != (security.ACCESS_ALLOWED_ACE_TYPE, flags)
                       or access != wanted_access
                       or security.ConvertSidToStringSid(identity) != wanted_identity
                       for (kind, access, identity), (wanted_identity, wanted_access)
                       in zip(rows, expected))):
            raise ValueError('Managed state ACL verification failed')
    except Exception as error:
        raise ValueError('Could not provision same-account managed state access') from error


def verify_existing(path, *, directory):
    """Require effective access through the exact user SID; never repair here."""
    path = _real(path)
    if os.name != 'nt':
        if path.stat().st_uid != os.getuid():
            raise ValueError('Managed state belongs to another account')
        return
    api, constants, security = _windows()
    try:
        sid = _user_sid(api, constants, security)
        wanted = security.ConvertSidToStringSid(sid)
        descriptor = security.GetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
            security.DACL_SECURITY_INFORMATION | security.OWNER_SECURITY_INFORMATION)
        owner = security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
        if owner not in (wanted, 'S-1-5-32-544'):
            raise ValueError('Managed state belongs to another account')
        acl = descriptor.GetSecurityDescriptorDacl()
        granted = 0
        required = MODIFY if directory else MODIFY & ~0x40
        if acl is not None:
            for index in range(acl.GetAceCount()):
                kind, access, identity = acl.GetAce(index)
                if kind[1] & 8:  # INHERIT_ONLY_ACE is not effective on this object.
                    continue
                # Conservatively fail on any deny affecting needed rights;
                # do not infer elevated group membership grants normal access.
                if kind[0] == security.ACCESS_DENIED_ACE_TYPE and access & required:
                    raise ValueError('Managed state has restrictive deny rules')
                if (kind[0] == security.ACCESS_ALLOWED_ACE_TYPE
                        and security.ConvertSidToStringSid(identity) == wanted):
                    granted |= access
        if granted & required != required:
            raise ValueError('Same-account Modify access is missing; explicit repair is required')
    except Exception as error:
        raise ValueError('Existing managed state access could not be verified; explicit repair is required') from error


def ensure_managed_directory(path):
    """Provision only a fresh root; existing roots are validated, never repaired."""
    path = _real(path)
    if path.exists():
        if not path.is_dir():
            raise ValueError('Managed state root is not a directory')
        verify_existing(path, directory=True)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    try:
        provision_new(path, directory=True)
    except BaseException:
        # No child/write is permitted before provisioning succeeds.
        path.rmdir()
        raise
    return True


def open_managed_lock(path):
    """Create a private lock before initializing it, or open an existing lock."""
    path = _real(path)
    try:
        handle = path.open('x+b')
    except FileExistsError:
        verify_existing(path, directory=False)
        return path.open('r+b')
    try:
        provision_new(path, directory=False)
    except BaseException:
        handle.close()
        path.unlink()
        raise
    return handle
