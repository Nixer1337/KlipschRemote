"""Cross-platform "launch on system startup" registration.

A small, dependency-free helper that registers / unregisters the app to start
when the user logs in, using each platform's *standard* per-user mechanism (no
admin rights, nothing system-wide):

  * **Windows** — a per-user **Scheduled Task** (``schtasks``) triggered at
    logon with a short delay. A plain ``HKCU\\...\\Run`` value (used by older
    builds) fires *before* the shell/notification area exists at login, so a
    silent "start into the tray" launch raced the shell and died; the task's
    logon delay lets the desktop settle first. Legacy ``Run`` values are
    migrated away on launch (see :func:`migrate_legacy`).
  * **macOS**   — a LaunchAgent ``.plist`` in ``~/Library/LaunchAgents``.
  * **Linux**   — an XDG autostart ``.desktop`` file in ``~/.config/autostart``.

The OS registration itself is the single source of truth — :func:`is_enabled`
reads it back directly, so the UI never drifts from the real state. All entry
points relaunch the *same* executable the OS used to start this process (the
native ``flet build`` launcher, a PyInstaller bundle exe, or — in a source
checkout — ``python -m klipsch_remote``), found via :func:`_launch_args`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

_APP_NAME = "Klipsch Remote"
_APP_ID = "com.unofficial.klipsch-remote"   # mirrors the flet org/product id
# Windows Scheduled Task (current mechanism).
_WIN_TASK_NAME = "KlipschRemote"
# Logon delay: give the shell + notification area time to come up before we
# launch, so the silent "start into the tray" path doesn't race a half-built
# desktop (the root cause of the old Run-key autostart dying at boot).
_WIN_LOGON_DELAY = "PT20S"
# Legacy Run-key autostart (older builds) — migrated away / cleaned up below.
_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_WIN_RUN_VALUE = "KlipschRemote"
# Marker appended to the autostart launch command on macOS/Linux so the app knows
# it was started at login (read back from sys.argv via launched_at_startup()).
# NOT used on Windows: the native flet/serious_python launcher CRASHES if given
# any extra argv (the embedded Python never boots), so the Windows autostart
# launch is argument-free and the login launch is detected structurally instead —
# by the parent process being the Task Scheduler service (svchost.exe).
_AUTOSTART_FLAG = "--autostart"


def is_supported() -> bool:
    """True on the platforms that have a backend below."""
    return sys.platform == "win32" or sys.platform == "darwin" \
        or sys.platform.startswith("linux")


# --------------------------------------------------------------- launch target
def _host_executable() -> str:
    """Full path of the executable the OS actually launched this process from.

    For the native ``flet build`` app this is the bundle's launcher (e.g.
    ``KlipschRemote.exe``); for a PyInstaller bundle it's the frozen app exe;
    in a plain ``python -m klipsch_remote`` dev run it's the interpreter. We ask
    the OS for the *process image* (not ``sys.executable``, which on an embedded
    interpreter like serious_python's would point at the wrong thing).
    """
    if sys.platform == "win32":
        import ctypes
        buf = ctypes.create_unicode_buffer(4096)
        if ctypes.windll.kernel32.GetModuleFileNameW(None, buf, len(buf)):
            return buf.value
    elif sys.platform == "darwin":
        import ctypes
        try:
            libc = ctypes.CDLL(None)
            size = ctypes.c_uint32(4096)
            buf = ctypes.create_string_buffer(size.value)
            if libc._NSGetExecutablePath(buf, ctypes.byref(size)) == 0:
                return os.path.realpath(buf.value.decode())
        except Exception:  # fall back to sys.executable below
            pass
    else:  # linux / other posix
        # A Linux AppImage exposes its own path here — relaunch the .AppImage,
        # not the extracted inner binary, which won't exist next boot.
        appimage = os.environ.get("APPIMAGE")
        if appimage:
            return appimage
        try:
            return os.readlink("/proc/self/exe")
        except OSError:
            pass
    return sys.executable


def _launch_args() -> list[str]:
    """The argv used to relaunch the app at login.

    On macOS/Linux the silent-start flag is appended so the app can tell it was
    started at login. On Windows NO extra args are added — the native launcher
    crashes on any argv — so the command is just the executable (or the module
    invocation in a dev checkout); the login launch is detected via the parent
    process instead (see :func:`launched_at_startup`).
    """
    host = _host_executable()
    base = os.path.basename(host).lower()
    # A bare interpreter means we're running from source (dev / `python -m`),
    # so relaunch the module rather than the naked interpreter.
    if base.startswith("python") or base in ("py", "py.exe", "pythonw.exe"):
        args = [host, "-m", "klipsch_remote"]
    else:
        args = [host]
    if sys.platform != "win32":
        args.append(_AUTOSTART_FLAG)
    return args


def _win_parent_image_name() -> str:
    """Lower-cased image name of this process's parent (e.g. ``svchost.exe``).

    Walks a Toolhelp process snapshot to find our parent PID, then that PID's
    executable name. Returns ``""`` on any failure.
    """
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = wintypes.HANDLE(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    k32 = ctypes.windll.kernel32
    # Declare signatures: HANDLE is pointer-sized, so without an explicit restype
    # ctypes' default c_int would truncate the snapshot handle on 64-bit Windows.
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                    ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE,
                                   ctypes.POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID:
        return ""
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        me = os.getpid()
        parent_pid = None
        if k32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                if entry.th32ProcessID == me:
                    parent_pid = entry.th32ParentProcessID
                    break
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        if parent_pid is None:
            return ""
        if k32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                if entry.th32ProcessID == parent_pid:
                    return (entry.szExeFile or "").lower()
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        k32.CloseHandle(snap)
    return ""


def launched_at_startup() -> bool:
    """True if this process was started by the autostart entry (silent launch).

    On Windows the launch is argument-free (the native launcher can't take
    argv), so we detect it structurally: a logon Scheduled Task is started by
    the Task Scheduler *service*, making our parent process ``svchost.exe`` — a
    manual launch's parent is ``explorer.exe`` / the installer / a shell, never
    svchost. Elsewhere the silent-start flag in ``sys.argv`` is the signal.
    """
    if sys.platform == "win32":
        try:
            return _win_parent_image_name() == "svchost.exe"
        except Exception:  # treat detection failure as "manual"
            return False
    return _AUTOSTART_FLAG in sys.argv


def _quote(args: list[str]) -> str:
    """Join argv into a command line, quoting any element with spaces."""
    return " ".join(f'"{a}"' if " " in a else a for a in args)


# ------------------------------------------------------------------- Windows
def _win_schtasks(args: list[str]) -> subprocess.CompletedProcess:
    """Run ``schtasks.exe`` with no console flash, capturing its output."""
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _win_current_user() -> str:
    """``DOMAIN\\user`` (or just ``user``) for the task's logon trigger/principal."""
    domain = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or ""
    user = os.environ.get("USERNAME") or ""
    return f"{domain}\\{user}" if domain else user


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _win_task_xml() -> str:
    """Task Scheduler definition: run at this user's logon, after a short delay.

    ``LeastPrivilege`` + ``InteractiveToken`` means it runs in the normal desktop
    session with no admin rights and no UAC prompt; ``ExecutionTimeLimit`` is
    unbounded (PT0S) so the long-running app is never auto-killed; battery checks
    are off so it still starts on laptops.
    """
    args = _launch_args()                 # Windows: just [exe] (no argv!)
    command = args[0]
    arguments = " ".join(args[1:])
    # The native exe takes no args; emit <Arguments> only when there are some
    # (a dev `python -m klipsch_remote` task), never an empty element.
    arguments_xml = (f'      <Arguments>{_xml_escape(arguments)}</Arguments>\n'
                     if arguments else "")
    user = _xml_escape(_win_current_user())
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <RegistrationInfo>\n'
        '    <Description>Klipsch Remote - launch at logon (silent, into tray)'
        '.</Description>\n'
        '  </RegistrationInfo>\n'
        '  <Triggers>\n'
        '    <LogonTrigger>\n'
        '      <Enabled>true</Enabled>\n'
        f'      <UserId>{user}</UserId>\n'
        f'      <Delay>{_WIN_LOGON_DELAY}</Delay>\n'
        '    </LogonTrigger>\n'
        '  </Triggers>\n'
        '  <Principals>\n'
        '    <Principal id="Author">\n'
        f'      <UserId>{user}</UserId>\n'
        '      <LogonType>InteractiveToken</LogonType>\n'
        '      <RunLevel>LeastPrivilege</RunLevel>\n'
        '    </Principal>\n'
        '  </Principals>\n'
        '  <Settings>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <AllowHardTerminate>true</AllowHardTerminate>\n'
        '    <StartWhenAvailable>true</StartWhenAvailable>\n'
        '    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n'
        '    <AllowStartOnDemand>true</AllowStartOnDemand>\n'
        '    <Enabled>true</Enabled>\n'
        '    <Hidden>false</Hidden>\n'
        '    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n'
        '    <WakeToRun>false</WakeToRun>\n'
        '    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n'
        '    <Priority>7</Priority>\n'
        '  </Settings>\n'
        '  <Actions Context="Author">\n'
        '    <Exec>\n'
        f'      <Command>{_xml_escape(command)}</Command>\n'
        f'{arguments_xml}'
        '    </Exec>\n'
        '  </Actions>\n'
        '</Task>\n'
    )


def _win_is_enabled() -> bool:
    return _win_schtasks(["/Query", "/TN", _WIN_TASK_NAME]).returncode == 0


def _win_set(enabled: bool) -> None:
    if enabled:
        fd, path = tempfile.mkstemp(suffix=".xml")
        try:
            with os.fdopen(fd, "w", encoding="utf-16") as f:
                f.write(_win_task_xml())
            r = _win_schtasks(["/Create", "/TN", _WIN_TASK_NAME,
                               "/XML", path, "/F"])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if r.returncode != 0:
            raise OSError("schtasks /Create failed "
                          f"({r.returncode}): {(r.stderr or r.stdout).strip()}")
    else:
        _win_schtasks(["/Delete", "/TN", _WIN_TASK_NAME, "/F"])
    _win_clear_legacy_run()


def _win_has_legacy_run() -> bool:
    """True if an old ``HKCU\\...\\Run`` autostart value is still present."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as key:
            winreg.QueryValueEx(key, _WIN_RUN_VALUE)
        return True
    except OSError:
        return False


def _win_clear_legacy_run() -> None:
    """Drop the old Run-key autostart value (superseded by the scheduled task)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _WIN_RUN_VALUE)
    except OSError:  # FileNotFoundError (already gone) included
        pass


# --------------------------------------------------------------------- macOS
def _mac_plist_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{_APP_ID}.plist")


def _mac_is_enabled() -> bool:
    return os.path.exists(_mac_plist_path())


def _mac_set(enabled: bool) -> None:
    import plistlib
    path = _mac_plist_path()
    if enabled:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            plistlib.dump({
                "Label": _APP_ID,
                "ProgramArguments": _launch_args(),
                "RunAtLoad": True,
            }, f)
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


# --------------------------------------------------------------------- Linux
def _linux_desktop_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart", "klipsch-remote.desktop")


def _linux_is_enabled() -> bool:
    return os.path.exists(_linux_desktop_path())


def _linux_set(enabled: bool) -> None:
    path = _linux_desktop_path()
    if enabled:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={_APP_NAME}\n"
            f"Exec={_quote(_launch_args())}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(entry)
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


# ------------------------------------------------------------------- dispatch
def is_enabled() -> bool:
    """True if the app is currently registered to launch at login."""
    if sys.platform == "win32":
        return _win_is_enabled()
    if sys.platform == "darwin":
        return _mac_is_enabled()
    if sys.platform.startswith("linux"):
        return _linux_is_enabled()
    return False


def set_enabled(enabled: bool) -> None:
    """Register (``True``) or unregister (``False``) launch-at-login.

    Raises on failure (e.g. a locked registry / read-only home) so callers can
    surface the error and keep the toggle in sync with reality.
    """
    if sys.platform == "win32":
        _win_set(enabled)
    elif sys.platform == "darwin":
        _mac_set(enabled)
    elif sys.platform.startswith("linux"):
        _linux_set(enabled)


def migrate_legacy() -> None:
    """Upgrade an older autostart registration to the current mechanism.

    Earlier Windows builds registered launch-at-login via ``HKCU\\...\\Run``,
    which fired too early at boot. If we find that stale value and no scheduled
    task yet, recreate the registration through the current backend (a delayed
    logon task) and drop the old value — so the user's setting survives the
    upgrade without them having to re-toggle it. Best-effort and no-op elsewhere.
    """
    if sys.platform != "win32":
        return
    try:
        if _win_has_legacy_run() and not _win_is_enabled():
            set_enabled(True)   # creates the task + clears the legacy value
    except Exception:  # migration must never block startup
        pass
