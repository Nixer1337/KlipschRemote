<#
.SYNOPSIS
    Capture clean PNG screenshots of the Klipsch Remote app for the README.

.DESCRIPTION
    For each requested screen this launches the app in offline demo mode
    (KLIPSCH_DEMO=1 + KLIPSCH_SHOT=<screen>, see klipsch_remote/_demo.py), waits
    for it to signal that the screen has rendered, captures the window (tight to
    the DWM frame bounds, so no OS drop-shadow border), saves it, and quits the
    process before moving on.

    No Bluetooth speaker is required: the UI is populated from a canned device.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\capture_screenshots.ps1
#>
[CmdletBinding()]
param(
    [string]   $OutDir  = '',
    [string[]] $Screens = @('connect', 'remote', 'equalizer', 'settings', 'settings2', 'about'),
    [string]   $Python  = 'python',
    [int]      $TimeoutSeconds = 40
)

$ErrorActionPreference = 'Stop'
# $PSScriptRoot isn't always populated in the param block (e.g. launched via -File
# from a .bat), so resolve the script dir here.
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$root = Split-Path -Parent $scriptDir
if (-not $OutDir) { $OutDir = Join-Path $root 'docs\screenshots' }

# --- Win32 helpers: find the window by title + read its tight frame bounds ----
$cs = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public class WinShot {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
    public delegate bool EnumWindowsProc(IntPtr h, IntPtr p);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, int data, UIntPtr extra);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out RECT r, int size);

    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }

    const uint MOUSEEVENTF_WHEEL = 0x0800;

    static readonly IntPtr HWND_TOPMOST    = new IntPtr(-1);
    static readonly IntPtr HWND_NOTOPMOST  = new IntPtr(-2);
    const uint SWP_NOMOVE = 0x0002, SWP_NOSIZE = 0x0001, SWP_SHOWWINDOW = 0x0040;

    public static IntPtr Find(string title) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, p) => {
            if (!IsWindowVisible(h)) return true;
            int len = GetWindowTextLength(h);
            if (len == 0) return true;
            var sb = new StringBuilder(len + 1);
            GetWindowText(h, sb, sb.Capacity);
            if (sb.ToString() == title) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static RECT Frame(IntPtr h) {
        RECT r;
        // DWMWA_EXTENDED_FRAME_BOUNDS = 9 -> bounds without the invisible border.
        DwmGetWindowAttribute(h, 9, out r, Marshal.SizeOf(typeof(RECT)));
        return r;
    }

    // Raise above every other window WITHOUT needing foreground-activation rights
    // (SetForegroundWindow is unreliable across processes). Guarantees the pixels
    // are on top for a screen-grab.
    public static void Raise(IntPtr h) {
        ShowWindow(h, 9); // SW_RESTORE
        SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
    }
    public static void Unraise(IntPtr h) {
        SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
    }

    // Scroll the window to the bottom by driving a real mouse wheel over its
    // centre (flet_desktop ignores programmatic scroll_to mid-transition). The
    // cursor is then parked at the window's top-left corner so no button hover /
    // tooltip shows up in the capture.
    public static void ScrollToBottom(IntPtr h, int notches) {
        RECT r; DwmGetWindowAttribute(h, 9, out r, Marshal.SizeOf(typeof(RECT)));
        int cx = (r.Left + r.Right) / 2;
        int cy = (r.Top + r.Bottom) / 2;
        SetCursorPos(cx, cy);
        System.Threading.Thread.Sleep(60);
        for (int i = 0; i < notches; i++) {
            mouse_event(MOUSEEVENTF_WHEEL, 0, 0, -120, UIntPtr.Zero); // negative = down
            System.Threading.Thread.Sleep(40);
        }
        SetCursorPos(r.Left + 2, r.Top + 2); // park off the controls
    }
}
'@
Add-Type -TypeDefinition $cs
Add-Type -AssemblyName System.Drawing
[void][WinShot]::SetProcessDPIAware()

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
Write-Host "Output: $OutDir"

function Capture-Window([string]$title, [string]$path, [int]$scrollNotches = 0) {
    $h = [WinShot]::Find($title)
    if ($h -eq [IntPtr]::Zero) { throw "window '$title' not found" }
    $savedCursor = New-Object 'WinShot+POINT'
    [void][WinShot]::GetCursorPos([ref]$savedCursor)
    [WinShot]::Raise($h)
    Start-Sleep -Milliseconds 600                 # let it settle on top + repaint
    try {
        if ($scrollNotches -gt 0) {
            [WinShot]::ScrollToBottom($h, $scrollNotches)
            Start-Sleep -Milliseconds 400         # let the scroll settle
        }
        $r = [WinShot]::Frame($h)
        $w = $r.Right - $r.Left
        $hgt = $r.Bottom - $r.Top
        # The real window is ~460x860 logical (>=300x500 at any DPI). A tiny rect
        # means we matched a stale/secondary window (e.g. the app was closed), so
        # fail loudly instead of saving a garbage thumbnail.
        if ($w -lt 300 -or $hgt -lt 500) { throw "matched window too small (${w} x ${hgt}); is the app still open?" }
        $bmp = New-Object System.Drawing.Bitmap($w, $hgt)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($r.Left, $r.Top, 0, 0, (New-Object System.Drawing.Size($w, $hgt)))
        $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        Write-Host ("  saved {0}  ({1} x {2})" -f (Split-Path -Leaf $path), $w, $hgt)
    }
    finally {
        [WinShot]::Unraise($h)
        [void][WinShot]::SetCursorPos($savedCursor.X, $savedCursor.Y)
    }
}

foreach ($screen in $Screens) {
    Write-Host "[$screen] launching..."
    $ready = Join-Path $env:TEMP ("klipsch_shot_{0}.ready" -f $screen)
    if (Test-Path $ready) { Remove-Item $ready -Force }

    $env:KLIPSCH_DEMO       = '1'
    $env:KLIPSCH_SHOT       = $screen
    $env:KLIPSCH_SHOT_READY = $ready

    $proc = Start-Process -FilePath $Python -ArgumentList '-m', 'klipsch_remote' `
                          -WorkingDirectory $root -PassThru

    try {
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while (-not (Test-Path $ready) -and (Get-Date) -lt $deadline) {
            if ($proc.HasExited) { throw "app exited early (code $($proc.ExitCode))" }
            Start-Sleep -Milliseconds 250
        }
        if (-not (Test-Path $ready)) { throw "timed out waiting for '$screen' to render" }

        # 'equalizer' (remote) and 'settings2' are their screens scrolled to the
        # bottom — drive the wheel to reveal the lower half.
        $notches = if ($screen -eq 'equalizer' -or $screen -eq 'settings2') { 15 } else { 0 }
        Capture-Window 'Klipsch Remote' (Join-Path $OutDir "$screen.png") $notches
    }
    finally {
        # Kill the whole tree (python + the Flet desktop client it spawns).
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$($proc.Id)" |
            ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        if (Test-Path $ready) { Remove-Item $ready -Force }
    }
}

Remove-Item Env:\KLIPSCH_DEMO, Env:\KLIPSCH_SHOT, Env:\KLIPSCH_SHOT_READY -ErrorAction SilentlyContinue
Write-Host "Done."
