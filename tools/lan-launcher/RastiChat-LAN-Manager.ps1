<#
.SYNOPSIS
    RastiChat LAN one-click launcher — starts/stops the full stack (Postgres,
    Redis, Django backend, Widget, Operator Dashboard, Platform Dashboard)
    bound to this machine's real Wi-Fi/LAN IP address, so other laptops and
    phones on the same router can reach it.

.DESCRIPTION
    This script is the single source of truth for the LAN launcher. The BAT
    files in this same folder are thin wrappers that call this script with a
    fixed command — nothing here is duplicated in the BAT files.

    Never edits tracked repository source files. Every generated artifact
    (LAN IP, chosen ports, .env.local files, PIDs, logs) lives under
    tools\lan-launcher\runtime\, which is gitignored.

.PARAMETER Command
    One of: start, stop, restart, status, logs, doctor, urls, seed, smoke, reset.

.PARAMETER LanIP
    Override automatic LAN IP detection (e.g. -LanIP 192.168.1.3).

.PARAMETER Service
    For the `logs` command: limit to one service (backend, widget, operator, platform).

.PARAMETER Follow
    For the `logs` command: tail/follow instead of a one-shot dump.

.PARAMETER NoBrowser
    For `start`: do not open any browser tabs automatically.

.PARAMETER OpenPlatform
    For `start`: also open the Platform Dashboard login tab (Operator + Widget
    demo open by default; Platform is opened only on request since not every
    LAN test session needs it).

.PARAMETER RemoveFirewallRules
    For `stop`: also remove the firewall rules this launcher created.

.PARAMETER Confirm
    Required alongside `reset` to actually perform the destructive volume wipe.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File RastiChat-LAN-Manager.ps1 start
    powershell -ExecutionPolicy Bypass -File RastiChat-LAN-Manager.ps1 status
    powershell -ExecutionPolicy Bypass -File RastiChat-LAN-Manager.ps1 logs -Service backend -Follow
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'logs', 'doctor', 'urls', 'seed', 'smoke', 'reset', IgnoreCase = $true)]
    [string]$Command = 'start',

    [string]$LanIP,
    [ValidateSet('backend', 'widget', 'operator', 'platform', 'all', IgnoreCase = $true)]
    [string]$Service = 'all',
    [switch]$Follow,
    [switch]$NoBrowser,
    [switch]$OpenPlatform,
    [switch]$RemoveFirewallRules,
    [switch]$Confirm,
    # Internal use only: set when the script re-launches itself elevated
    # (via Start-Process -Verb RunAs) purely to add/remove firewall rules,
    # so the elevated child process does exactly that one step and exits —
    # never the full start/stop flow — and never appears as a normal user-
    # facing command.
    [switch]$FirewallOnly,
    [switch]$FirewallRemoveOnly
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ============================================================================
# 0. PATHS AND CONSTANTS
# ============================================================================

$LauncherRoot = $PSScriptRoot
$RuntimeDir = Join-Path $LauncherRoot 'runtime'
$LogsDir = Join-Path $RuntimeDir 'logs'
$StateFile = Join-Path $RuntimeDir 'state.json'
$LockFile = Join-Path $RuntimeDir 'launcher.lock'
$LanConfigFile = Join-Path $RuntimeDir 'lan-config.json'
$UrlsTextFile = Join-Path $RuntimeDir 'LAN-URLS.txt'
$UrlsHtmlFile = Join-Path $RuntimeDir 'index.html'
$OperatorEnvFile = Join-Path $RuntimeDir 'operator.env.local'
$PlatformEnvFile = Join-Path $RuntimeDir 'platform.env.local'
$OperatorEnvTarget = Join-Path $LauncherRoot '..' '..' 'apps' 'operator-dashboard' '.env.local'
$PlatformEnvTarget = Join-Path $LauncherRoot '..' '..' 'apps' 'platform-dashboard' '.env.local'
$LanComposeFile = Join-Path $RuntimeDir 'docker-compose.lan.yml'

# Default ports — never move Vite/Next.js to another port implicitly; if a
# port is genuinely occupied by something else, the user is asked (see
# Resolve-Ports), the chosen port is never silently different from what is
# printed to the user.
$DefaultPorts = @{
    backend  = 8080
    widget   = 8081
    operator = 3100
    platform = 3101
}

$RequiredRepoPaths = @(
    'docker-compose.yml',
    'backend',
    'apps/operator-dashboard',
    'apps/platform-dashboard',
    'packages/widget',
    'examples/plain-html'
)

# Persian source files whose bytes must never be touched by this launcher —
# checked before and after every run (see Test-EncodingIntegrity).
$PersianSourceFiles = @(
    'packages/widget/src/main.ts',
    'examples/plain-html/index.html',
    'backend/seed_data.py'
)

$Script:RepoRoot = $null

# ============================================================================
# 1. OUTPUT HELPERS
# ============================================================================

function Write-Section([string]$Title) {
    Write-Host ''
    Write-Host ('=' * 60) -ForegroundColor DarkCyan
    Write-Host $Title -ForegroundColor Cyan
    Write-Host ('=' * 60) -ForegroundColor DarkCyan
}

function Write-Ok([string]$Message) { Write-Host "[OK]      $Message" -ForegroundColor Green }
function Write-WarnMsg([string]$Message) { Write-Host "[WARNING] $Message" -ForegroundColor Yellow }
function Write-ErrMsg([string]$Message) { Write-Host "[ERROR]   $Message" -ForegroundColor Red }
function Write-Info([string]$Message) { Write-Host "[INFO]    $Message" -ForegroundColor Gray }

function Fail-Fa([string]$MessageFa, [string]$MessageEn) {
    Write-Host ''
    Write-Host "خطا: $MessageFa" -ForegroundColor Red
    if ($MessageEn) { Write-Host "Error: $MessageEn" -ForegroundColor Red }
    Write-Host ''
    exit 1
}

# ============================================================================
# 2. REPO ROOT DETECTION AND VALIDATION
# ============================================================================

function Get-RepoRoot {
    if ($Script:RepoRoot) { return $Script:RepoRoot }
    # tools\lan-launcher -> tools -> repo root. Resolved relative to this
    # script's own file location — never a hardcoded drive/path, so cloning
    # the repository anywhere (including paths with spaces) works unchanged.
    $candidate = Resolve-Path (Join-Path $LauncherRoot '..' '..') -ErrorAction SilentlyContinue
    if (-not $candidate) {
        Fail-Fa 'مسیر ریشه مخزن یافت نشد. این اسکریپت باید داخل tools\lan-launcher باقی بماند.' `
            'Could not resolve the repository root relative to this script.'
    }
    $Script:RepoRoot = $candidate.Path
    return $Script:RepoRoot
}

function Test-RepoStructure {
    $root = Get-RepoRoot
    $missing = @()
    foreach ($rel in $RequiredRepoPaths) {
        $full = Join-Path $root $rel
        if (-not (Test-Path $full)) { $missing += $rel }
    }
    if ($missing.Count -gt 0) {
        $listFa = ($missing -join "`n  - ")
        Fail-Fa "ساختار مخزن ناقص است. مسیرهای زیر یافت نشدند:`n  - $listFa`nمطمئن شوید که این اسکریپت داخل مخزن کامل RastiChat اجرا می‌شود." `
            "Missing required repository paths: $($missing -join ', ')"
    }
    return $root
}

# ============================================================================
# 3. RUNTIME DIRECTORIES / LOCK
# ============================================================================

function Initialize-RuntimeDirs {
    foreach ($dir in @($RuntimeDir, $LogsDir)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
}

function Enter-LauncherLock {
    Initialize-RuntimeDirs
    if (Test-Path $LockFile) {
        try {
            $lockPid = [int](Get-Content $LockFile -Raw -ErrorAction Stop).Trim()
            $existing = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
            if ($existing) {
                Fail-Fa "یک نمونه دیگر از راه‌انداز (PID $lockPid) در حال اجراست. صبر کنید یا با دستور 'stop' آن را متوقف کنید." `
                    "Another launcher instance (PID $lockPid) appears to be running."
            }
            else {
                Write-WarnMsg "یک فایل قفل قدیمی (متعلق به فرآیندی که دیگر وجود ندارد) پاک شد."
            }
        }
        catch {
            Write-WarnMsg 'فایل قفل نامعتبر بود و پاک شد.'
        }
    }
    Set-Content -Path $LockFile -Value $PID -Encoding utf8 -NoNewline
}

function Exit-LauncherLock {
    if (Test-Path $LockFile) {
        try {
            $lockPid = [int](Get-Content $LockFile -Raw -ErrorAction Stop).Trim()
            if ($lockPid -eq $PID) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
        }
        catch { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
    }
}

# ============================================================================
# 4. LAN IP DISCOVERY
# ============================================================================

function Get-CandidateAdapters {
    # Cross-platform-safe path first (works for validation on non-Windows
    # hosts too); Windows-specific richer detection (adapter name / media
    # type / default-route correlation) is layered on top when available.
    $addresses = @()
    try {
        $ips = [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
            Where-Object { $_.OperationalStatus -eq 'Up' -and $_.NetworkInterfaceType -ne 'Loopback' }
        foreach ($iface in $ips) {
            $props = $iface.GetIPProperties()
            foreach ($ua in $props.UnicastAddresses) {
                if ($ua.Address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { continue }
                $ip = $ua.Address.ToString()
                if ($ip -eq '127.0.0.1') { continue }
                if ($ip -like '169.254.*') { continue }
                $name = $iface.Name
                $desc = $iface.Description
                if ($name -match 'vEthernet|WSL|Hyper-V|Docker|VMware|VirtualBox|Loopback' -or
                    $desc -match 'vEthernet|WSL|Hyper-V|Docker|VMware|VirtualBox|Loopback') { continue }
                $addresses += [PSCustomObject]@{
                    IP          = $ip
                    AdapterName = $name
                    Description = $desc
                    IsWifi      = ($name -match 'Wi-?Fi|WLAN' -or $desc -match 'Wi-?Fi|WLAN|Wireless')
                    IsEthernet  = ($iface.NetworkInterfaceType -eq 'Ethernet')
                }
            }
        }
    }
    catch {
        Write-WarnMsg "شناسایی آداپتورهای شبکه با خطا مواجه شد: $($_.Exception.Message)"
    }
    return $addresses
}

function Get-DefaultGatewayIP {
    # Windows-only enrichment: correlates the chosen adapter with the one
    # actually carrying the default route, when Get-NetRoute is available.
    try {
        if (Get-Command Get-NetRoute -ErrorAction SilentlyContinue) {
            $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
                Sort-Object -Property RouteMetric | Select-Object -First 1
            if ($route) {
                $cfg = Get-NetIPConfiguration -InterfaceIndex $route.ifIndex -ErrorAction SilentlyContinue
                if ($cfg -and $cfg.IPv4Address) { return $cfg.IPv4Address.IPAddress }
            }
        }
    }
    catch { }
    return $null
}

function Get-LanIPAddress {
    if ($LanIP) {
        Write-Ok "آی‌پی LAN به‌صورت دستی تنظیم شد: $LanIP"
        return $LanIP
    }

    $candidates = Get-CandidateAdapters
    if ($candidates.Count -eq 0) {
        Fail-Fa 'هیچ آداپتور شبکه فعال و معتبری یافت نشد. به Wi-Fi یا شبکه محلی متصل شوید یا از -LanIP استفاده کنید.' `
            'No active, valid LAN adapter found. Connect to Wi-Fi/LAN or pass -LanIP explicitly.'
    }

    # Preference order per spec: Wi-Fi/WLAN, then Ethernet, then whatever's
    # left. Within a tier with more than one candidate, the adapter actually
    # carrying the default route (if determinable) breaks the tie — it is
    # never allowed to override the Wi-Fi > Ethernet tier order itself.
    $gatewayIp = Get-DefaultGatewayIP
    $tiers = @(
        @{ Name = 'Wi-Fi'; Items = @($candidates | Where-Object { $_.IsWifi }) },
        @{ Name = 'Ethernet'; Items = @($candidates | Where-Object { $_.IsEthernet -and -not $_.IsWifi }) },
        @{ Name = 'سایر'; Items = @($candidates | Where-Object { -not $_.IsWifi -and -not $_.IsEthernet }) }
    )

    foreach ($tier in $tiers) {
        $items = $tier.Items
        if ($items.Count -eq 0) { continue }
        if ($items.Count -eq 1) {
            Write-Ok "آی‌پی LAN شناسایی شد ($($tier.Name)): $($items[0].IP) [$($items[0].AdapterName)]"
            return $items[0].IP
        }
        if ($gatewayIp) {
            $match = $items | Where-Object { $_.IP -eq $gatewayIp } | Select-Object -First 1
            if ($match) {
                Write-Ok "آی‌پی LAN شناسایی شد ($($tier.Name)، مسیر پیش‌فرض شبکه): $($match.IP) [$($match.AdapterName)]"
                return $match.IP
            }
        }
        Write-WarnMsg "چند آداپتور $($tier.Name) معتبر یافت شد؛ لطفاً یکی را انتخاب کنید:"
        for ($i = 0; $i -lt $items.Count; $i++) {
            Write-Host ("  [$i] $($items[$i].IP)  ($($items[$i].AdapterName) - $($items[$i].Description))")
        }
        $selection = Read-Host 'شماره آداپتور مورد نظر را وارد کنید'
        $idx = 0
        if ([int]::TryParse($selection, [ref]$idx) -and $idx -ge 0 -and $idx -lt $items.Count) {
            return $items[$idx].IP
        }
        Fail-Fa 'انتخاب نامعتبر بود.' 'Invalid adapter selection.'
    }

    Fail-Fa 'هیچ آداپتور شبکه فعال و معتبری یافت نشد.' 'No active, valid LAN adapter found.'
}

# ============================================================================
# 5. PORT MANAGEMENT
# ============================================================================

function Test-TcpPortFree([int]$Port) {
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $Port)
        $listener.Start()
        $listener.Stop()
        return $true
    }
    catch {
        return $false
    }
}

function Get-PortOwnerInfo([int]$Port) {
    # Windows-rich path (process name/PID via Get-NetTCPConnection); falls
    # back to "unknown owner" cross-platform so doctor/status never crash
    # outside Windows.
    try {
        if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
            $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($conn) {
                $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
                return [PSCustomObject]@{ Pid = $conn.OwningProcess; Name = if ($proc) { $proc.ProcessName } else { 'unknown' } }
            }
        }
    }
    catch { }
    return $null
}

function Test-PortOwnedByLauncher([int]$Port) {
    $state = Get-State
    if (-not $state) { return $false }
    foreach ($svc in $state.Services.PSObject.Properties) {
        if ($svc.Value.Port -eq $Port -and $svc.Value.Pid) {
            $proc = Get-Process -Id $svc.Value.Pid -ErrorAction SilentlyContinue
            if ($proc) { return $true }
        }
    }
    return $false
}

function Resolve-Ports {
    $ports = @{}
    foreach ($key in $DefaultPorts.Keys) {
        $port = $DefaultPorts[$key]
        if (Test-TcpPortFree $port) {
            $ports[$key] = $port
            continue
        }
        if (Test-PortOwnedByLauncher $port) {
            Write-Info "پورت $port ($key) قبلاً توسط همین راه‌انداز استفاده شده — بازاستفاده امن می‌شود."
            $ports[$key] = $port
            continue
        }
        $owner = Get-PortOwnerInfo $port
        if ($owner) {
            Write-WarnMsg "پورت $port ($key) توسط برنامه دیگری اشغال شده: $($owner.Name) (PID $($owner.Pid))"
        }
        else {
            Write-WarnMsg "پورت $port ($key) در دسترس نیست (مالک نامشخص — احتمالاً بدون دسترسی مدیر قابل شناسایی نیست)."
        }
        Fail-Fa "پورت $port برای سرویس '$key' اشغال است. برنامه اشغال‌کننده را ببندید یا با ویرایش `$DefaultPorts در اسکریپت پورت دیگری تعیین کنید. برای جلوگیری از رفتار غیرقابل پیش‌بینی، این راه‌انداز به‌صورت خودکار پورت Vite/Next.js را تغییر نمی‌دهد." `
            "Port $port ($key) is occupied by another application and cannot be safely reused. Refusing to silently move Vite/Next.js to a different port."
    }
    return $ports
}

# ============================================================================
# 6. STATE FILE
# ============================================================================

function Get-State {
    if (-not (Test-Path $StateFile)) { return $null }
    try {
        return Get-Content $StateFile -Raw -Encoding utf8 | ConvertFrom-Json
    }
    catch {
        Write-WarnMsg 'فایل وضعیت (state.json) خراب بود و نادیده گرفته شد.'
        return $null
    }
}

function Save-State($StateObject) {
    Initialize-RuntimeDirs
    $StateObject | ConvertTo-Json -Depth 6 | Set-Content -Path $StateFile -Encoding utf8
}

function Clear-StaleState {
    $state = Get-State
    if (-not $state) { return }
    $changed = $false
    foreach ($svc in $state.Services.PSObject.Properties) {
        if ($svc.Value.Pid) {
            $proc = Get-Process -Id $svc.Value.Pid -ErrorAction SilentlyContinue
            if (-not $proc -or $proc.StartTime -ne [datetime]$svc.Value.StartTime) {
                Write-Info "رکورد PID قدیمی برای سرویس '$($svc.Name)' پاک شد (فرآیند دیگر وجود ندارد یا PID بازاستفاده شده)."
                $svc.Value.Pid = $null
                $changed = $true
            }
        }
    }
    if ($changed) { Save-State $state }
}

# ============================================================================
# 7. DOCTOR (PREREQUISITE CHECKS)
# ============================================================================

function Test-DiskSpaceGb([string]$Path, [double]$MinGb) {
    try {
        $drive = (Get-Item $Path).PSDrive
        $freeGb = [math]::Round($drive.Free / 1GB, 1)
        return @{ Ok = ($freeGb -ge $MinGb); FreeGb = $freeGb }
    }
    catch {
        return @{ Ok = $true; FreeGb = $null }  # can't determine — don't block
    }
}

function Invoke-Doctor {
    Write-Section 'بررسی پیش‌نیازها (Doctor)'
    $root = Test-RepoStructure
    $failures = 0

    # PowerShell version
    if ($PSVersionTable.PSVersion.Major -ge 5) {
        Write-Ok "نسخه PowerShell: $($PSVersionTable.PSVersion)"
    }
    else {
        Write-ErrMsg "نسخه PowerShell خیلی قدیمی است ($($PSVersionTable.PSVersion)). حداقل 5.1 لازم است."
        $failures++
    }

    # Git (only needed if the user wants `git pull` — informational)
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Ok "Git یافت شد: $(git --version)"
    }
    else {
        Write-WarnMsg 'Git یافت نشد — برای به‌روزرسانی مخزن لازم است، اما برای اجرای فعلی راه‌انداز اختیاری است.'
    }

    # Docker
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($dockerCmd) {
        Write-Ok "Docker نصب است: $(docker --version)"
        try {
            docker info *> $null
            if ($LASTEXITCODE -eq 0) {
                Write-Ok 'Docker Desktop/daemon در حال اجراست.'
            }
            else {
                Write-ErrMsg 'Docker نصب است اما daemon در حال اجرا نیست. Docker Desktop را باز کنید.'
                $failures++
            }
        }
        catch {
            Write-ErrMsg 'Docker نصب است اما daemon در حال اجرا نیست. Docker Desktop را باز کنید.'
            $failures++
        }
        try {
            docker compose version *> $null
            if ($LASTEXITCODE -eq 0) { Write-Ok "Docker Compose در دسترس است: $(docker compose version --short 2>$null)" }
            else { Write-ErrMsg 'Docker Compose (پلاگین) در دسترس نیست.'; $failures++ }
        }
        catch { Write-ErrMsg 'Docker Compose (پلاگین) در دسترس نیست.'; $failures++ }
    }
    else {
        Write-ErrMsg 'Docker نصب نیست. Docker Desktop for Windows را نصب کنید. این راه‌انداز آن را به‌صورت خودکار نصب نمی‌کند.'
        $failures++
    }

    # Node / npm
    if (Get-Command node -ErrorAction SilentlyContinue) {
        Write-Ok "Node.js نصب است: $(node --version)"
    }
    else {
        Write-ErrMsg 'Node.js نصب نیست. آن را نصب کنید (نسخه 20 یا بالاتر پیشنهاد می‌شود).'
        $failures++
    }
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Ok "npm نصب است: $(npm --version)"
    }
    else {
        Write-ErrMsg 'npm نصب نیست.'
        $failures++
    }

    # Ports
    foreach ($key in $DefaultPorts.Keys) {
        $port = $DefaultPorts[$key]
        if (Test-TcpPortFree $port) {
            Write-Ok "پورت $port ($key) آزاد است."
        }
        else {
            $owner = Get-PortOwnerInfo $port
            if (Test-PortOwnedByLauncher $port) {
                Write-Ok "پورت $port ($key) توسط اجرای قبلی همین راه‌انداز استفاده شده (قابل بازاستفاده)."
            }
            elseif ($owner) {
                Write-WarnMsg "پورت $port ($key) اشغال است توسط $($owner.Name) (PID $($owner.Pid))."
            }
            else {
                Write-WarnMsg "پورت $port ($key) اشغال است (برنامه نامشخص)."
            }
        }
    }

    # Network adapter / Wi-Fi
    $candidates = Get-CandidateAdapters
    if ($candidates.Count -gt 0) {
        Write-Ok "آداپتور شبکه معتبر یافت شد: $($candidates.Count) مورد."
    }
    else {
        Write-ErrMsg 'هیچ آداپتور شبکه فعالی یافت نشد.'
        $failures++
    }

    # Private network profile (Windows-only signal)
    try {
        if (Get-Command Get-NetConnectionProfile -ErrorAction SilentlyContinue) {
            $profiles = Get-NetConnectionProfile -ErrorAction SilentlyContinue
            $public = $profiles | Where-Object { $_.NetworkCategory -eq 'Public' }
            if ($public) {
                Write-WarnMsg "شبکه '$($public[0].Name)' به‌صورت Public تنظیم شده — Windows Firewall ممکن است ارتباط LAN را مسدود کند. آن را به Private تغییر دهید (تنظیمات > شبکه)."
            }
            else {
                Write-Ok 'پروفایل شبکه فعال Private/Domain است.'
            }
        }
        else {
            Write-Info 'بررسی پروفایل شبکه در این سیستم‌عامل در دسترس نیست (فقط ویندوز).'
        }
    }
    catch { Write-Info 'بررسی پروفایل شبکه ممکن نشد.' }

    # Disk space
    $disk = Test-DiskSpaceGb -Path $root -MinGb 5
    if ($null -eq $disk.FreeGb) {
        Write-Info 'فضای دیسک قابل بررسی نبود.'
    }
    elseif ($disk.Ok) {
        Write-Ok "فضای آزاد دیسک: $($disk.FreeGb) GB"
    }
    else {
        Write-WarnMsg "فضای آزاد دیسک کم است: $($disk.FreeGb) GB (حداقل ۵ گیگابایت توصیه می‌شود)."
    }

    # Repo structure (already validated by Test-RepoStructure above)
    Write-Ok 'ساختار مخزن معتبر است.'

    # Lock files
    foreach ($lock in @(
            @{ Path = (Join-Path $root 'apps/operator-dashboard/package-lock.json'); Name = 'operator-dashboard' },
            @{ Path = (Join-Path $root 'apps/platform-dashboard/package-lock.json'); Name = 'platform-dashboard' },
            @{ Path = (Join-Path $root 'packages/widget/package-lock.json'); Name = 'widget' }
        )) {
        if (Test-Path $lock.Path) { Write-Ok "package-lock.json برای $($lock.Name) موجود است." }
        else { Write-WarnMsg "package-lock.json برای $($lock.Name) یافت نشد — npm install ممکن است کند یا غیرقطعی باشد." }
    }

    # Local env files (informational only — launcher generates its own)
    foreach ($envFile in @($OperatorEnvTarget, $PlatformEnvTarget)) {
        if (Test-Path $envFile) { Write-Info "فایل $(Split-Path $envFile -Leaf) از اجرای قبلی موجود است — در شروع بعدی بازنویسی می‌شود." }
    }

    Write-Host ''
    if ($failures -gt 0) {
        Write-ErrMsg "بررسی پیش‌نیازها با $failures خطا مواجه شد. قبل از 'start' آن‌ها را برطرف کنید."
        return $false
    }
    Write-Ok 'همه پیش‌نیازهای الزامی برقرار است.'
    return $true
}

# ============================================================================
# 8. RUNTIME CONFIGURATION GENERATION
# ============================================================================

function New-RuntimeConfig([string]$Ip, [hashtable]$Ports) {
    Initialize-RuntimeDirs
    $root = Get-RepoRoot

    $config = [ordered]@{
        lanIp        = $Ip
        ports        = $Ports
        backendApi   = "http://${Ip}:$($Ports.backend)/api/v1"
        backendWs    = "ws://${Ip}:$($Ports.backend)/ws"
        widgetUrl    = "http://${Ip}:$($Ports.widget)/e2e.html"
        operatorUrl  = "http://${Ip}:$($Ports.operator)/login"
        platformUrl  = "http://${Ip}:$($Ports.platform)/login"
        generatedAt  = (Get-Date).ToString('o')
    }
    # UTF8 (no BOM) so any Persian content downstream stays intact.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($LanConfigFile, ($config | ConvertTo-Json -Depth 5), $utf8NoBom)

    $operatorEnv = @"
# Generated by tools\lan-launcher\RastiChat-LAN-Manager.ps1 — DO NOT EDIT BY HAND.
# Regenerated on every `start`. Never committed (see .gitignore).
NEXT_PUBLIC_API_BASE_URL=$($config.backendApi)
NEXT_PUBLIC_WS_BASE_URL=$($config.backendWs)
"@
    $platformEnv = $operatorEnv

    [System.IO.File]::WriteAllText($OperatorEnvFile, $operatorEnv, $utf8NoBom)
    [System.IO.File]::WriteAllText($PlatformEnvFile, $platformEnv, $utf8NoBom)

    # Next.js only reads .env.local from the app's own directory. That file
    # is project-standard-ignored (dashboards' own .gitignore covers
    # .env*.local — see Test-EnvFilesIgnored), so writing it there is the
    # established mechanism, not a source-file mutation: no TRACKED file is
    # touched, only the conventional local-override file every Next.js app
    # already expects and ignores.
    [System.IO.File]::WriteAllText((Join-Path $root 'apps/operator-dashboard/.env.local'), $operatorEnv, $utf8NoBom)
    [System.IO.File]::WriteAllText((Join-Path $root 'apps/platform-dashboard/.env.local'), $platformEnv, $utf8NoBom)

    Write-Ok 'فایل‌های پیکربندی زمان اجرا ساخته شدند.'
    return $config
}

function Test-EnvFilesIgnored {
    $root = Get-RepoRoot
    foreach ($rel in @('apps/operator-dashboard', 'apps/platform-dashboard')) {
        Push-Location (Join-Path $root $rel)
        try {
            $ignored = git check-ignore -q '.env.local'; $isIgnored = ($LASTEXITCODE -eq 0)
            if (-not $isIgnored) {
                Write-WarnMsg "$rel/.env.local توسط git نادیده گرفته نمی‌شود — لطفاً بررسی کنید که در .gitignore این پروژه لیست شده باشد."
            }
        }
        finally { Pop-Location }
    }
}

function New-LanComposeOverride {
    Initialize-RuntimeDirs
    # Tightens host-port binding for db/redis to loopback-only (the base
    # docker-compose.yml publishes them on 0.0.0.0 by default, which would
    # otherwise expose Postgres/Redis to every device on the LAN). The
    # backend port is republished explicitly on 0.0.0.0 since phones/laptops
    # DO need to reach it. This file is regenerated every run, never
    # hand-edited, and only ever affects local `docker compose -f ... -f
    # docker-compose.lan.yml` invocations from this launcher.
    $content = @"
# Generated by tools\lan-launcher\RastiChat-LAN-Manager.ps1 — DO NOT EDIT BY HAND.
# LAN-only override: keeps Postgres/Redis reachable from this host only,
# while the backend stays reachable from other devices on the same Wi-Fi.
# Regenerated on every `start`; safe to delete between runs.
services:
  db:
    ports:
      - "127.0.0.1:5433:5432"
  redis:
    ports:
      - "127.0.0.1:6380:6379"
  backend:
    ports:
      - "0.0.0.0:$($DefaultPorts.backend):8000"
"@
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($LanComposeFile, $content, $utf8NoBom)
}

# ============================================================================
# 9. ENCODING INTEGRITY
# ============================================================================

function Get-FileHashSafe([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash
}

function Get-EncodingBaseline {
    $root = Get-RepoRoot
    $baseline = @{}
    foreach ($rel in $PersianSourceFiles) {
        $baseline[$rel] = Get-FileHashSafe (Join-Path $root $rel)
    }
    return $baseline
}

function Test-EncodingIntegrity([hashtable]$Baseline) {
    $root = Get-RepoRoot
    $ok = $true
    foreach ($rel in $PersianSourceFiles) {
        $current = Get-FileHashSafe (Join-Path $root $rel)
        if ($Baseline[$rel] -ne $current) {
            Write-ErrMsg "یکپارچگی فایل فارسی نقض شد: $rel تغییر کرده است! این راه‌انداز هرگز نباید فایل‌های منبع را ویرایش کند."
            $ok = $false
        }
    }
    if ($ok) { Write-Ok 'یکپارچگی کدگذاری فایل‌های فارسی تأیید شد (بدون تغییر).' }
    return $ok
}

# ============================================================================
# 10. WINDOWS FIREWALL
# ============================================================================

$FirewallRuleGroup = 'RastiChat LAN Development'

function Test-IsElevated {
    try {
        if (-not $IsWindows -and $null -ne (Get-Variable -Name IsWindows -ErrorAction SilentlyContinue)) { return $true }
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    catch {
        return $true  # non-Windows: elevation concept doesn't apply, don't block
    }
}

function Set-FirewallRules([hashtable]$Ports) {
    if (-not (Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue)) {
        Write-Info 'مدیریت Windows Firewall در این سیستم‌عامل در دسترس نیست (فقط ویندوز) — این مرحله رد شد.'
        return
    }
    if (-not (Test-IsElevated)) {
        Write-WarnMsg "برای افزودن قوانین Windows Firewall نیاز به دسترسی Administrator است. یک پنجره PowerShell جدید با دسترسی بالا برای همین مرحله باز می‌شود."
        $psExe = (Get-Process -Id $PID).Path
        $scriptPath = $PSCommandPath
        $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$scriptPath`"", '-FirewallOnly')
        if ($LanIP) { $args += @('-LanIP', $LanIP) }
        try {
            Start-Process -FilePath $psExe -Verb RunAs -ArgumentList $args -Wait -ErrorAction Stop
            Write-Ok 'مرحله Firewall با دسترسی بالا اجرا شد (در صورت تأیید UAC).'
        }
        catch {
            Write-WarnMsg "افزودن خودکار قوانین Firewall انجام نشد (UAC رد شد یا خطا رخ داد): $($_.Exception.Message)"
            Write-WarnMsg 'برنامه بدون قوانین Firewall ادامه می‌یابد — دستگاه‌های دیگر ممکن است نتوانند متصل شوند تا این قوانین را دستی اضافه کنید یا دوباره با دسترسی Administrator اجرا کنید.'
        }
        return
    }

    foreach ($key in $Ports.Keys) {
        $port = $Ports[$key]
        $ruleName = "RastiChat LAN - $key ($port)"
        $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Info "قانون Firewall برای $key از قبل موجود است — رد شد (idempotent)."
            continue
        }
        try {
            New-NetFirewallRule -DisplayName $ruleName -Group $FirewallRuleGroup `
                -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow `
                -Profile Private -ErrorAction Stop | Out-Null
            Write-Ok "قانون Firewall برای $key (پورت $port) اضافه شد (فقط پروفایل Private)."
        }
        catch {
            Write-WarnMsg "افزودن قانون Firewall برای $key ناموفق بود: $($_.Exception.Message)"
        }
    }
}

function Remove-LauncherFirewallRules {
    if (-not (Get-Command Remove-NetFirewallRule -ErrorAction SilentlyContinue)) {
        Write-Info 'مدیریت Windows Firewall در این سیستم‌عامل در دسترس نیست — این مرحله رد شد.'
        return
    }
    if (-not (Test-IsElevated)) {
        Write-WarnMsg "برای حذف قوانین Windows Firewall نیاز به دسترسی Administrator است. یک پنجره PowerShell جدید با دسترسی بالا باز می‌شود."
        $psExe = (Get-Process -Id $PID).Path
        $scriptPath = $PSCommandPath
        $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$scriptPath`"", '-FirewallRemoveOnly')
        try {
            Start-Process -FilePath $psExe -Verb RunAs -ArgumentList $args -Wait -ErrorAction Stop
            Write-Ok 'حذف قوانین Firewall با دسترسی بالا اجرا شد (در صورت تأیید UAC).'
        }
        catch {
            Write-WarnMsg "حذف خودکار قوانین Firewall انجام نشد: $($_.Exception.Message)"
        }
        return
    }
    $rules = Get-NetFirewallRule -Group $FirewallRuleGroup -ErrorAction SilentlyContinue
    if ($rules) {
        $rules | Remove-NetFirewallRule -ErrorAction SilentlyContinue
        Write-Ok "$($rules.Count) قانون Firewall مربوط به RastiChat حذف شد."
    }
    else {
        Write-Info 'قانون Firewall مربوط به RastiChat یافت نشد.'
    }
}

# ============================================================================
# 11. DOCKER / BACKEND
# ============================================================================

function Get-ComposeArgs {
    $root = Get-RepoRoot
    New-LanComposeOverride
    return @('compose', '-f', (Join-Path $root 'docker-compose.yml'), '-f', $LanComposeFile)
}

function Start-DockerStack {
    $root = Get-RepoRoot
    Push-Location $root
    try {
        Write-Info 'در حال اجرای Docker Compose (ممکن است در اولین اجرا چند دقیقه طول بکشد)...'
        $composeArgs = Get-ComposeArgs
        & docker @composeArgs up -d --build 2>&1 | Tee-Object -FilePath (Join-Path $LogsDir 'docker-compose.log') -Append
        if ($LASTEXITCODE -ne 0) {
            Fail-Fa 'اجرای Docker Compose ناموفق بود. جزئیات را در runtime\logs\docker-compose.log ببینید.' 'docker compose up failed.'
        }
    }
    finally { Pop-Location }
}

function Wait-ContainerHealthy([string]$ServiceName, [int]$TimeoutSec = 90) {
    $root = Get-RepoRoot
    Push-Location $root
    try {
        $composeArgs = Get-ComposeArgs
        $deadline = (Get-Date).AddSeconds($TimeoutSec)
        while ((Get-Date) -lt $deadline) {
            $cid = (& docker @composeArgs ps -q $ServiceName 2>$null)
            if ($cid) {
                $health = (docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $cid 2>$null)
                if ($health -eq 'healthy' -or $health -eq 'running') { return $true }
            }
            Start-Sleep -Seconds 2
        }
        return $false
    }
    finally { Pop-Location }
}

function Invoke-BackendMigrate {
    $root = Get-RepoRoot
    Push-Location $root
    try {
        $composeArgs = Get-ComposeArgs
        Write-Info 'در حال اجرای مایگریشن‌های بک‌اند...'
        & docker @composeArgs exec -T backend python manage.py migrate 2>&1 |
            Tee-Object -FilePath (Join-Path $LogsDir 'backend-migrate.log')
        if ($LASTEXITCODE -ne 0) {
            Fail-Fa 'اجرای مایگریشن ناموفق بود. جزئیات را در runtime\logs\backend-migrate.log ببینید.' 'manage.py migrate failed.'
        }
        Write-Ok 'مایگریشن‌ها با موفقیت اجرا شدند.'
    }
    finally { Pop-Location }
}

function Invoke-BackendSeed {
    $root = Get-RepoRoot
    Push-Location $root
    try {
        $composeArgs = Get-ComposeArgs
        Write-Info 'در حال اجرای seed (داده‌های آزمایشی قطعی)...'
        $output = & docker @composeArgs exec -T backend python seed_data.py 2>&1
        $output | Tee-Object -FilePath (Join-Path $LogsDir 'backend-seed.log')
        if ($LASTEXITCODE -ne 0) {
            Write-WarnMsg 'اجرای seed با خطا مواجه شد — جزئیات در runtime\logs\backend-seed.log. برنامه بدون داده seed ادامه می‌یابد.'
            return $null
        }
        Write-Ok 'داده‌های seed آماده شدند (idempotent — قابل اجرای مجدد بدون تکرار).'
        return $output
    }
    finally { Pop-Location }
}

function Wait-BackendHealthy([string]$Ip, [int]$Port, [int]$TimeoutSec = 60) {
    $url = "http://${Ip}:$Port/api/v1/health/"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) { return $true }
        }
        catch { }
        Start-Sleep -Seconds 2
    }
    return $false
}

# ============================================================================
# 12. FRONTEND PROCESS MANAGEMENT
# ============================================================================

function Start-BackgroundProcess([string]$Name, [string]$FilePath, [string]$ArgumentList, [string]$WorkingDirectory, [hashtable]$EnvVars) {
    $logFile = Join-Path $LogsDir "$Name.log"
    if (Test-Path $logFile) { Remove-Item $logFile -Force -ErrorAction SilentlyContinue }

    # cmd.exe /c wrapper lets us redirect both stdout+stderr into one log
    # file reliably across PowerShell versions without a job object, and
    # keeps the actual Node/npm window fully hidden (no visible console).
    $envPrefix = ''
    if ($EnvVars) {
        foreach ($k in $EnvVars.Keys) { $envPrefix += "set `"$k=$($EnvVars[$k])`" && " }
    }
    $fullCommand = "$envPrefix`"$FilePath`" $ArgumentList >> `"$logFile`" 2>&1"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = if ($IsWindows -or $null -eq (Get-Variable -Name IsWindows -ErrorAction SilentlyContinue)) { 'cmd.exe' } else { '/bin/sh' }
    $argSwitch = if ($psi.FileName -eq 'cmd.exe') { '/c' } else { '-c' }
    $psi.Arguments = "$argSwitch `"$fullCommand`""
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $proc = [System.Diagnostics.Process]::Start($psi)
    return [PSCustomObject]@{
        Name      = $Name
        Pid       = $proc.Id
        StartTime = $proc.StartTime.ToString('o')
        Command   = "$FilePath $ArgumentList"
        LogFile   = $logFile
    }
}

function Test-ProcessIsLauncherOwned([int]$ProcessId, [string]$ExpectedStartTime) {
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    if ($ExpectedStartTime) {
        try {
            return ($proc.StartTime.ToString('o') -eq $ExpectedStartTime)
        }
        catch { return $false }  # access denied reading StartTime => not ours, don't touch it
    }
    return $true
}

function Stop-LauncherProcess([string]$Name, $ServiceState) {
    if (-not $ServiceState -or -not $ServiceState.Pid) {
        Write-Info "سرویس '$Name' در حال اجرا نبود."
        return
    }
    if (-not (Test-ProcessIsLauncherOwned -ProcessId $ServiceState.Pid -ExpectedStartTime $ServiceState.StartTime)) {
        Write-Info "سرویس '$Name' (PID $($ServiceState.Pid)) دیگر متعلق به این راه‌انداز نیست — نادیده گرفته شد (کشتن فرآیندهای ناشناس هرگز انجام نمی‌شود)."
        return
    }
    try {
        # Stop the whole process tree (npm/next spawn a child node process;
        # killing only the parent npm.cmd wrapper leaves the real server
        # running) — enumerate children first, then stop parent-down.
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($ServiceState.Pid)" -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $ServiceState.Pid -Force -ErrorAction SilentlyContinue
        Write-Ok "سرویس '$Name' (PID $($ServiceState.Pid)) متوقف شد."
    }
    catch {
        Write-WarnMsg "توقف سرویس '$Name' با خطا مواجه شد: $($_.Exception.Message)"
    }
}

function Wait-HttpOk([string]$Url, [int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) { return $true }
        }
        catch { }
        Start-Sleep -Seconds 2
    }
    return $false
}

# ============================================================================
# 13. URL OUTPUT
# ============================================================================

function Get-SeededProjectKey {
    # Reads the project's public_key straight from the running backend
    # container via the ORM — the single source of truth, never guessed or
    # hardcoded, and correct even if seed_data.py's fixtures ever change.
    $root = Get-RepoRoot
    Push-Location $root
    try {
        $composeArgs = Get-ComposeArgs
        $script = "from projects.models import Project`ntry:`n    print(Project.objects.get(name='Sample Website').public_key)`nexcept Project.DoesNotExist:`n    print('')"
        $key = (& docker @composeArgs exec -T backend python manage.py shell -c $script 2>$null | Select-Object -Last 1)
        if ($key) { return $key.Trim() }
        return ''
    }
    catch { return '' }
    finally { Pop-Location }
}

function Get-FullSummary([hashtable]$Config, [hashtable]$HealthResults) {
    $projectKey = Get-SeededProjectKey
    $widgetDemoUrl = if ($projectKey) {
        "$($Config.widgetUrl)?projectKey=$projectKey&apiBase=$([uri]::EscapeDataString($Config.backendApi))&wsBase=$([uri]::EscapeDataString($Config.backendWs))"
    }
    else { $Config.widgetUrl }

    $lines = @()
    $lines += '=================================================='
    $lines += 'RastiChat LAN آماده است'
    $lines += '=================================================='
    $lines += ''
    $lines += 'آی‌پی این سیستم:'
    $lines += $Config.lanIp
    $lines += ''
    $lines += 'مشتری / ویجت (روی موبایل یا لپ‌تاپ دیگر باز کنید):'
    $lines += $widgetDemoUrl
    $lines += ''
    $lines += 'پنل اپراتور:'
    $lines += $Config.operatorUrl
    $lines += "  (localhost: http://localhost:$($Config.ports.operator)/login)"
    $lines += ''
    $lines += 'پنل پلتفرم:'
    $lines += $Config.platformUrl
    $lines += "  (localhost: http://localhost:$($Config.ports.platform)/login)"
    $lines += ''
    $lines += 'Backend API:'
    $lines += "$($Config.backendApi)/"
    $lines += ''
    $lines += 'حساب‌های ورود محلی (فقط برای تست — هرگز در محیط واقعی استفاده نشود):'
    $lines += '  مدیر فضای‌کار : admin@ws.com / pass1234'
    $lines += '  اپراتور ۱     : operator@ws.com / pass1234'
    $lines += '  اپراتور ۲     : operator2@ws.com / pass1234'
    $lines += '  پشتیبانی پلتفرم: support@platform.com / pass1234'
    $lines += ''
    $lines += 'وضعیت:'
    foreach ($k in @('backend', 'widget', 'operator', 'platform')) {
        $label = @{ backend = 'Backend'; widget = 'Widget'; operator = 'Operator Dashboard'; platform = 'Platform Dashboard' }[$k]
        $status = if ($HealthResults -and $HealthResults.ContainsKey($k)) { if ($HealthResults[$k]) { 'READY' } else { 'NOT READY' } } else { 'UNKNOWN' }
        $lines += ('  {0,-20}{1}' -f $label, $status)
    }
    $lines += ''
    $lines += 'هشدار: همه دستگاه‌های تست باید به یک روتر Wi-Fi متصل باشند.'
    $lines += 'هشدار: اگر اتصال برقرار نشد، VPN یا داده موبایل را خاموش کنید.'
    $lines += ''
    $lines += 'مسیر لاگ‌ها:'
    $lines += $LogsDir
    $lines += ''
    $lines += 'برای توقف همه سرویس‌ها:'
    $lines += '  Stop-RastiChat-LAN.bat'
    $lines += '=================================================='
    return ($lines -join "`r`n")
}

function Write-UrlsOutput([hashtable]$Config, [hashtable]$HealthResults) {
    $summary = Get-FullSummary -Config $Config -HealthResults $HealthResults
    Write-Host ''
    Write-Host $summary
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($UrlsTextFile, $summary, $utf8NoBom)

    $projectKey = Get-SeededProjectKey
    $widgetDemoUrl = if ($projectKey) {
        "$($Config.widgetUrl)?projectKey=$projectKey&apiBase=$([uri]::EscapeDataString($Config.backendApi))&wsBase=$([uri]::EscapeDataString($Config.backendWs))"
    }
    else { $Config.widgetUrl }

    $html = @"
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head><meta charset="UTF-8"><title>RastiChat LAN</title>
<style>body{font-family:Tahoma,sans-serif;max-width:640px;margin:40px auto;padding:0 16px}
a{display:block;margin:10px 0;padding:12px;background:#f0f4ff;border-radius:8px;text-decoration:none;color:#1d4ed8}
h1{color:#111}</style></head>
<body>
<h1>RastiChat LAN آماده است</h1>
<p>آی‌پی: $($Config.lanIp)</p>
<a href="$widgetDemoUrl" target="_blank">مشتری / ویجت</a>
<a href="$($Config.operatorUrl)" target="_blank">پنل اپراتور</a>
<a href="$($Config.platformUrl)" target="_blank">پنل پلتفرم</a>
<a href="$($Config.backendApi)/" target="_blank">Backend API</a>
</body></html>
"@
    [System.IO.File]::WriteAllText($UrlsHtmlFile, $html, $utf8NoBom)
    Write-Info "خلاصه در $UrlsTextFile و $UrlsHtmlFile ذخیره شد."
    return $widgetDemoUrl
}

function Open-LauncherBrowsers([hashtable]$Config, [string]$WidgetDemoUrl) {
    if ($NoBrowser) { return }
    try {
        Start-Process $WidgetDemoUrl
        Start-Sleep -Milliseconds 400
        Start-Process $Config.operatorUrl
        if ($OpenPlatform) {
            Start-Sleep -Milliseconds 400
            Start-Process $Config.platformUrl
        }
    }
    catch {
        Write-Info 'باز کردن خودکار مرورگر ممکن نشد — لینک‌ها را دستی باز کنید.'
    }
}

# ============================================================================
# 14. COMMAND: start
# ============================================================================

function Invoke-CmdStart {
    Write-Section 'شروع RastiChat LAN'
    Enter-LauncherLock
    try {
        $root = Test-RepoStructure
        $baseline = Get-EncodingBaseline

        if (-not (Invoke-Doctor)) {
            Fail-Fa 'راه‌اندازی به دلیل نبود پیش‌نیازهای الزامی متوقف شد. خروجی doctor را بررسی کنید.' 'Mandatory prerequisites missing; aborting start.'
        }

        Clear-StaleState
        $ip = Get-LanIPAddress
        $ports = Resolve-Ports
        $config = New-RuntimeConfig -Ip $ip -Ports $ports
        Test-EnvFilesIgnored
        Set-FirewallRules -Ports $ports

        # ---- Backend (Docker) ----
        Start-DockerStack
        if (-not (Wait-ContainerHealthy -ServiceName 'db' -TimeoutSec 60)) { Fail-Fa 'دیتابیس PostgreSQL آماده نشد.' 'Postgres did not become healthy in time.' }
        Write-Ok 'PostgreSQL آماده است.'
        if (-not (Wait-ContainerHealthy -ServiceName 'redis' -TimeoutSec 60)) { Fail-Fa 'Redis آماده نشد.' 'Redis did not become healthy in time.' }
        Write-Ok 'Redis آماده است.'
        if (-not (Wait-BackendHealthy -Ip $ip -Port $ports.backend -TimeoutSec 90)) {
            Show-FailedServiceLogs 'backend'
            Fail-Fa 'بک‌اند در بازه زمانی مشخص پاسخ نداد.' 'Backend did not become healthy in time.'
        }
        Write-Ok 'Backend API آماده است.'
        Invoke-BackendMigrate
        Invoke-BackendSeed | Out-Null

        # ---- Frontends ----
        # If a previous `start` left frontend processes running (the user
        # ran `start` twice without `stop`), stop those launcher-owned
        # processes first rather than leaking orphans and double-binding
        # the same ports.
        $previousState = Get-State
        if ($previousState -and $previousState.Services) {
            foreach ($svcName in @('widget', 'operator', 'platform')) {
                $prevSvc = $previousState.Services.$svcName
                if ($prevSvc -and $prevSvc.Pid) {
                    Stop-LauncherProcess -Name $svcName -ServiceState $prevSvc
                }
            }
        }
        $state = @{ Services = @{} }

        Write-Info 'در حال ساخت Widget (vite build)...'
        Push-Location (Join-Path $root 'packages/widget')
        try {
            & npm run build 2>&1 | Tee-Object -FilePath (Join-Path $LogsDir 'widget-build.log')
            if ($LASTEXITCODE -ne 0) { Fail-Fa 'ساخت Widget ناموفق بود.' 'Widget build failed.' }
        }
        finally { Pop-Location }

        $widgetProc = Start-BackgroundProcess -Name 'widget' -FilePath 'npx' `
            -ArgumentList "vite --host 0.0.0.0 --port $($ports.widget) --strictPort" `
            -WorkingDirectory (Join-Path $root 'packages/widget')
        $state.Services['widget'] = @{ Pid = $widgetProc.Pid; StartTime = $widgetProc.StartTime; Port = $ports.widget; Url = "http://${ip}:$($ports.widget)/"; Command = $widgetProc.Command; LogFile = $widgetProc.LogFile }

        $operatorProc = Start-BackgroundProcess -Name 'operator' -FilePath 'npx' `
            -ArgumentList "next dev --hostname 0.0.0.0 --port $($ports.operator)" `
            -WorkingDirectory (Join-Path $root 'apps/operator-dashboard')
        $state.Services['operator'] = @{ Pid = $operatorProc.Pid; StartTime = $operatorProc.StartTime; Port = $ports.operator; Url = "http://${ip}:$($ports.operator)/login"; Command = $operatorProc.Command; LogFile = $operatorProc.LogFile }

        $platformProc = Start-BackgroundProcess -Name 'platform' -FilePath 'npx' `
            -ArgumentList "next dev --hostname 0.0.0.0 --port $($ports.platform)" `
            -WorkingDirectory (Join-Path $root 'apps/platform-dashboard')
        $state.Services['platform'] = @{ Pid = $platformProc.Pid; StartTime = $platformProc.StartTime; Port = $ports.platform; Url = "http://${ip}:$($ports.platform)/login"; Command = $platformProc.Command; LogFile = $platformProc.LogFile }

        $state.LanIp = $ip
        $state.StartedAt = (Get-Date).ToString('o')
        Save-State $state

        # ---- Health checks (bounded retries; do not declare ready on launch alone) ----
        Write-Info 'در انتظار آماده‌شدن سرویس‌های فرانت‌اند...'
        $health = @{ backend = $true }
        $health['widget'] = Wait-HttpOk -Url "http://${ip}:$($ports.widget)/e2e.html" -TimeoutSec 60
        $health['operator'] = Wait-HttpOk -Url "http://${ip}:$($ports.operator)/login" -TimeoutSec 90
        $health['platform'] = Wait-HttpOk -Url "http://${ip}:$($ports.platform)/login" -TimeoutSec 90

        foreach ($k in $health.Keys) {
            if (-not $health[$k]) {
                Write-ErrMsg "سرویس '$k' آماده نشد."
                Show-FailedServiceLogs $k
            }
        }

        if (-not (Test-EncodingIntegrity $baseline)) {
            Write-ErrMsg 'یکپارچگی کدگذاری فارسی نقض شد — این یک خطای جدی است، لطفاً گزارش دهید.'
        }

        $widgetDemoUrl = Write-UrlsOutput -Config $config -HealthResults $health
        Open-LauncherBrowsers -Config $config -WidgetDemoUrl $widgetDemoUrl

        if ($health.Values -contains $false) {
            Write-Host ''
            Write-WarnMsg 'برخی سرویس‌ها آماده نشدند. برای تلاش دوباره: Restart-RastiChat-LAN.bat'
            exit 1
        }
        Write-Host ''
        Write-Ok 'RastiChat LAN با موفقیت آماده شد.'
    }
    finally {
        Exit-LauncherLock
    }
}

function Show-FailedServiceLogs([string]$ServiceName) {
    $logFile = Join-Path $LogsDir "$ServiceName.log"
    if ($ServiceName -eq 'backend') { $logFile = Join-Path $LogsDir 'docker-compose.log' }
    if (Test-Path $logFile) {
        Write-Host "--- آخرین خطوط لاگ ($ServiceName) ---" -ForegroundColor DarkYellow
        Get-Content $logFile -Tail 25 -Encoding utf8 | ForEach-Object { Write-Host "  $_" }
        Write-Host '---' -ForegroundColor DarkYellow
    }
}

# ============================================================================
# 15. COMMAND: stop
# ============================================================================

function Invoke-CmdStop {
    Write-Section 'توقف RastiChat LAN'
    Initialize-RuntimeDirs
    $root = Test-RepoStructure
    $state = Get-State

    if ($state -and $state.Services) {
        foreach ($svcName in @('widget', 'operator', 'platform')) {
            $svc = $state.Services.$svcName
            if ($svc) { Stop-LauncherProcess -Name $svcName -ServiceState $svc }
        }
    }
    else {
        Write-Info 'هیچ فرآیند فرانت‌اند ثبت‌شده‌ای یافت نشد.'
    }

    Push-Location $root
    try {
        if (Test-Path (Join-Path $root 'docker-compose.yml')) {
            $composeArgs = Get-ComposeArgs
            Write-Info 'در حال توقف Docker Compose (دیتابیس‌ها نگه داشته می‌شوند)...'
            & docker @composeArgs down 2>&1 | Tee-Object -FilePath (Join-Path $LogsDir 'docker-compose.log') -Append
            if ($LASTEXITCODE -eq 0) {
                Write-Ok 'کانتینرهای Docker متوقف شدند (حجم‌های داده حفظ شدند).'
            }
            else {
                Write-WarnMsg "توقف Docker Compose با خطا مواجه شد (کد $LASTEXITCODE) — جزئیات در runtime\logs\docker-compose.log."
            }
        }
    }
    finally { Pop-Location }

    if ($RemoveFirewallRules) {
        Remove-LauncherFirewallRules
    }
    else {
        Write-Info 'قوانین Firewall حفظ شدند (برای حذف: stop -RemoveFirewallRules).'
    }

    if ($state) {
        $state.Services = @{}
        Save-State $state
    }
    Exit-LauncherLock
    Write-Ok 'همه سرویس‌های RastiChat LAN متوقف شدند.'
}

# ============================================================================
# 16. COMMAND: restart
# ============================================================================

function Invoke-CmdRestart {
    Write-Section 'راه‌اندازی مجدد RastiChat LAN'
    Invoke-CmdStop
    Start-Sleep -Seconds 2
    Invoke-CmdStart
}

# ============================================================================
# 17. COMMAND: status
# ============================================================================

function Invoke-CmdStatus {
    Write-Section 'وضعیت RastiChat LAN'
    Test-RepoStructure | Out-Null
    $state = Get-State

    if (-not $state -or -not $state.LanIp) {
        Write-WarnMsg 'هیچ اجرای فعالی از راه‌انداز یافت نشد. برای شروع: Start-RastiChat-LAN.bat'
        return
    }

    Write-Host "آی‌پی LAN ثبت‌شده: $($state.LanIp)"
    Write-Host "شروع‌شده در: $($state.StartedAt)"
    Write-Host ''

    Push-Location (Get-RepoRoot)
    try {
        $composeArgs = Get-ComposeArgs
        Write-Host '--- کانتینرهای Docker ---'
        & docker @composeArgs ps 2>&1 | ForEach-Object { Write-Host "  $_" }
    }
    finally { Pop-Location }

    Write-Host ''
    Write-Host '--- سرویس‌های فرانت‌اند ---'
    foreach ($svcName in @('widget', 'operator', 'platform')) {
        $svc = $state.Services.$svcName
        if (-not $svc -or -not $svc.Pid) {
            Write-Host ("  {0,-10} متوقف" -f $svcName) -ForegroundColor DarkGray
            continue
        }
        $isRunning = Test-ProcessIsLauncherOwned -ProcessId $svc.Pid -ExpectedStartTime $svc.StartTime
        $uptime = 'نامشخص'
        if ($isRunning) {
            try {
                $proc = Get-Process -Id $svc.Pid
                $uptime = [string]((Get-Date) - $proc.StartTime)
            }
            catch { }
        }
        $healthy = if ($isRunning) { Wait-HttpOk -Url $svc.Url -TimeoutSec 3 } else { $false }
        $statusText = if (-not $isRunning) { 'متوقف (PID یافت نشد)' } elseif ($healthy) { 'READY' } else { 'در حال اجرا (پاسخ HTTP دریافت نشد)' }
        $color = if ($healthy) { 'Green' } elseif ($isRunning) { 'Yellow' } else { 'DarkGray' }
        Write-Host ("  {0,-10} PID={1,-8} Port={2,-6} {3}" -f $svcName, $svc.Pid, $svc.Port, $statusText) -ForegroundColor $color
        Write-Host "             URL: $($svc.Url)"
        Write-Host "             Uptime: $uptime"
    }
    Write-Host ''
}

# ============================================================================
# 18. COMMAND: logs
# ============================================================================

function Invoke-CmdLogs {
    Test-RepoStructure | Out-Null
    $services = if ($Service -eq 'all') { @('backend', 'widget', 'operator', 'platform') } else { @($Service) }

    if ($Follow -and $services.Count -eq 1) {
        $target = $services[0]
        $logFile = if ($target -eq 'backend') {
            Push-Location (Get-RepoRoot)
            try { $composeArgs = Get-ComposeArgs; & docker @composeArgs logs -f --tail 50 backend; return }
            finally { Pop-Location }
        }
        else { Join-Path $LogsDir "$target.log" }
        if (-not (Test-Path $logFile)) { Write-WarnMsg "لاگ برای '$target' یافت نشد."; return }
        Write-Info "دنبال‌کردن لاگ '$target' (Ctrl+C برای توقف)..."
        Get-Content $logFile -Wait -Tail 30 -Encoding utf8
        return
    }

    foreach ($svc in $services) {
        Write-Section "لاگ: $svc"
        if ($svc -eq 'backend') {
            Push-Location (Get-RepoRoot)
            try { $composeArgs = Get-ComposeArgs; & docker @composeArgs logs --tail 80 backend 2>&1 | ForEach-Object { Write-Host $_ } }
            finally { Pop-Location }
            continue
        }
        $logFile = Join-Path $LogsDir "$svc.log"
        if (Test-Path $logFile) {
            Get-Content $logFile -Tail 80 -Encoding utf8 | ForEach-Object { Write-Host $_ }
        }
        else {
            Write-WarnMsg "لاگ برای '$svc' یافت نشد."
        }
    }
}

# ============================================================================
# 19. COMMAND: urls
# ============================================================================

function Invoke-CmdUrls {
    Test-RepoStructure | Out-Null
    $state = Get-State
    if (-not $state -or -not $state.LanIp) {
        Write-WarnMsg 'راه‌انداز در حال اجرا نیست. ابتدا start را اجرا کنید.'
        return
    }

    $currentIp = Get-LanIPAddress
    if ($currentIp -ne $state.LanIp) {
        Write-WarnMsg "آی‌پی Wi-Fi تغییر کرده است: $($state.LanIp) -> $currentIp"
        Write-Info 'پیکربندی زمان اجرا بازسازی و فرانت‌اندها مجدداً راه‌اندازی می‌شوند...'
        $ports = @{}
        foreach ($svcName in @('backend', 'widget', 'operator', 'platform')) {
            $ports[$svcName] = if ($state.Services.$svcName) { $state.Services.$svcName.Port } else { $DefaultPorts[$svcName] }
        }
        $config = New-RuntimeConfig -Ip $currentIp -Ports $ports
        foreach ($svcName in @('operator', 'platform')) {
            $svc = $state.Services.$svcName
            if ($svc -and $svc.Pid) { Stop-LauncherProcess -Name $svcName -ServiceState $svc }
        }
        $root = Get-RepoRoot
        $operatorProc = Start-BackgroundProcess -Name 'operator' -FilePath 'npx' `
            -ArgumentList "next dev --hostname 0.0.0.0 --port $($ports.operator)" `
            -WorkingDirectory (Join-Path $root 'apps/operator-dashboard')
        $state.Services.operator = @{ Pid = $operatorProc.Pid; StartTime = $operatorProc.StartTime; Port = $ports.operator; Url = "http://${currentIp}:$($ports.operator)/login"; Command = $operatorProc.Command; LogFile = $operatorProc.LogFile }
        $platformProc = Start-BackgroundProcess -Name 'platform' -FilePath 'npx' `
            -ArgumentList "next dev --hostname 0.0.0.0 --port $($ports.platform)" `
            -WorkingDirectory (Join-Path $root 'apps/platform-dashboard')
        $state.Services.platform = @{ Pid = $platformProc.Pid; StartTime = $platformProc.StartTime; Port = $ports.platform; Url = "http://${currentIp}:$($ports.platform)/login"; Command = $platformProc.Command; LogFile = $platformProc.LogFile }
        $state.LanIp = $currentIp
        Save-State $state
        Write-Ok 'لینک‌های جدید:'
    }
    else {
        $ports = @{}
        foreach ($svcName in @('backend', 'widget', 'operator', 'platform')) {
            $ports[$svcName] = if ($state.Services.$svcName) { $state.Services.$svcName.Port } else { $DefaultPorts[$svcName] }
        }
        $config = New-RuntimeConfig -Ip $currentIp -Ports $ports
    }

    $health = @{}
    foreach ($svcName in @('backend', 'widget', 'operator', 'platform')) {
        $url = if ($svcName -eq 'backend') { "$($config.backendApi)/" } else { $state.Services.$svcName.Url }
        $health[$svcName] = Wait-HttpOk -Url $url -TimeoutSec 5
    }
    Write-UrlsOutput -Config $config -HealthResults $health | Out-Null
}

# ============================================================================
# 20. COMMAND: seed
# ============================================================================

function Invoke-CmdSeed {
    Write-Section 'اجرای Seed'
    Initialize-RuntimeDirs
    Test-RepoStructure | Out-Null
    Push-Location (Get-RepoRoot)
    try {
        $composeArgs = Get-ComposeArgs
        $cid = (& docker @composeArgs ps -q backend 2>$null)
        if (-not $cid) {
            Fail-Fa 'بک‌اند در حال اجرا نیست. ابتدا start را اجرا کنید.' 'Backend is not running.'
        }
    }
    finally { Pop-Location }
    Invoke-BackendSeed | Out-Null
}

# ============================================================================
# 21. COMMAND: reset (destructive)
# ============================================================================

function Invoke-CmdReset {
    Write-Section 'بازنشانی کامل (مخرب)'
    if (-not $Confirm) {
        Fail-Fa "دستور reset داده‌های دیتابیس را کاملاً پاک می‌کند. برای تأیید، دوباره با -Confirm اجرا کنید:`n  RastiChat-LAN-Manager.ps1 reset -Confirm" `
            'reset requires -Confirm to proceed (destructive: removes Docker volumes).'
    }
    $root = Test-RepoStructure
    Invoke-CmdStop
    Push-Location $root
    try {
        $composeArgs = Get-ComposeArgs
        Write-WarnMsg 'در حال حذف حجم‌های داده (Postgres/Redis)...'
        & docker @composeArgs down -v 2>&1 | Tee-Object -FilePath (Join-Path $LogsDir 'docker-compose.log') -Append
        Write-Ok 'بازنشانی کامل انجام شد. برای شروع مجدد: start'
    }
    finally { Pop-Location }
}

# ============================================================================
# 22. COMMAND: smoke
# ============================================================================
# Automated LAN functional smoke test (task spec section 17): drives the
# real widget + operator dashboard over the actual LAN URLs (never
# localhost) with Playwright, covering widget load, RastiChat.init, sending
# a message, a real customer conversation being created, operator login,
# live bidirectional delivery (which only passes if the websocket actually
# works over the LAN IP), refresh-preserves-history, and a check for any
# CORS-related console/page errors. Lives in its own tools\lan-launcher\smoke
# mini-project so it never depends on (or interferes with) e2e/, whose
# global-setup assumes a local non-Docker backend.
function Invoke-CmdSmoke {
    Write-Section 'تست خودکار LAN (Smoke Test)'
    Test-RepoStructure | Out-Null
    $state = Get-State
    if (-not $state -or -not $state.LanIp -or -not $state.Services -or -not $state.Services.widget -or -not $state.Services.operator) {
        Fail-Fa 'راه‌انداز در حال اجرا نیست. ابتدا start را اجرا کنید.' 'LAN stack is not running; run start first.'
    }

    $ip = $state.LanIp
    $ports = @{}
    foreach ($svcName in @('backend', 'widget', 'operator', 'platform')) {
        $ports[$svcName] = if ($state.Services.$svcName) { $state.Services.$svcName.Port } else { $DefaultPorts[$svcName] }
    }
    $config = New-RuntimeConfig -Ip $ip -Ports $ports

    $projectKey = Get-SeededProjectKey
    if (-not $projectKey) {
        Fail-Fa 'کلید پروژه seed‌شده یافت نشد. ابتدا seed را اجرا کنید.' 'Seeded project key not found; run seed first.'
    }
    $widgetDemoUrl = "$($config.widgetUrl)?projectKey=$projectKey&apiBase=$([uri]::EscapeDataString($config.backendApi))&wsBase=$([uri]::EscapeDataString($config.backendWs))"

    $smokeDir = Join-Path $LauncherRoot 'smoke'
    if (-not (Test-Path (Join-Path $smokeDir 'node_modules'))) {
        Write-Info 'نصب وابستگی‌های تست Smoke (فقط بار اول)...'
        Push-Location $smokeDir
        try {
            & npm install 2>&1 | Tee-Object -FilePath (Join-Path $LogsDir 'smoke-install.log')
            if ($LASTEXITCODE -ne 0) { Fail-Fa 'نصب وابستگی‌های Smoke ناموفق بود.' 'npm install failed for the smoke test project.' }
        }
        finally { Pop-Location }
    }

    Write-Info 'در حال اجرای بررسی‌های عملکردی روی آدرس‌های واقعی LAN...'
    Push-Location $smokeDir
    try {
        $env:LAN_WIDGET_URL = $widgetDemoUrl
        $env:LAN_OPERATOR_URL = $config.operatorUrl
        & npx playwright test 2>&1 | Tee-Object -FilePath (Join-Path $LogsDir 'smoke-test.log')
        $exitCode = $LASTEXITCODE
    }
    finally {
        Remove-Item Env:\LAN_WIDGET_URL -ErrorAction SilentlyContinue
        Remove-Item Env:\LAN_OPERATOR_URL -ErrorAction SilentlyContinue
        Pop-Location
    }

    if ($exitCode -eq 0) {
        Write-Ok 'تست Smoke با موفقیت انجام شد — همه بررسی‌ها سبز هستند.'
    }
    else {
        Write-ErrMsg "تست Smoke ناموفق بود (کد خروج $exitCode). گزارش کامل: $(Join-Path $LogsDir 'smoke-test.log')"
    }
    exit $exitCode
}

# ============================================================================
# 23. INTERNAL: elevated firewall-only re-entry, then command dispatch
# ============================================================================
# When Set-FirewallRules / Remove-LauncherFirewallRules need Administrator
# rights they re-launch this exact script with -FirewallOnly /
# -FirewallRemoveOnly via `Start-Process -Verb RunAs`. That elevated child
# must do ONLY the firewall step and exit — never fall through to a normal
# start/stop — which is why this check runs first, before the normal
# $Command dispatch below, and short-circuits with `exit`.
if ($FirewallOnly -or $FirewallRemoveOnly) {
    Initialize-RuntimeDirs
    if ($FirewallRemoveOnly) {
        Remove-LauncherFirewallRules
        exit 0
    }
    if (-not (Test-Path $LanConfigFile)) {
        Write-ErrMsg 'فایل پیکربندی زمان اجرا (lan-config.json) یافت نشد — ابتدا فرآیند اصلی start باید آن را بسازد.'
        exit 1
    }
    $cfg = Get-Content $LanConfigFile -Raw -Encoding utf8 | ConvertFrom-Json
    $ports = @{}
    foreach ($prop in $cfg.ports.PSObject.Properties) { $ports[$prop.Name] = [int]$prop.Value }
    Set-FirewallRules -Ports $ports
    exit 0
}

switch ($Command.ToLowerInvariant()) {
    'start' { Invoke-CmdStart }
    'stop' { Invoke-CmdStop }
    'restart' { Invoke-CmdRestart }
    'status' { Invoke-CmdStatus }
    'logs' { Invoke-CmdLogs }
    'doctor' { Test-RepoStructure | Out-Null; if (Invoke-Doctor) { exit 0 } else { exit 1 } }
    'urls' { Invoke-CmdUrls }
    'seed' { Invoke-CmdSeed }
    'smoke' { Invoke-CmdSmoke }
    'reset' { Invoke-CmdReset }
    default {
        Write-ErrMsg "دستور نامعتبر: $Command"
        exit 1
    }
}
