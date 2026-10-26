#Requires -Version 5.1
<#
.SYNOPSIS
    One-command local dev boot for csctalon (Windows / Windows PowerShell 5.1+).

.DESCRIPTION
    Boots the whole dev stack in the order the project expects
    (see README "本地开发" and docs/arch/A-20260922-001):

      1. PostgreSQL 15 + Redis 7 via `docker compose up -d db redis`
         (only the two infrastructure containers; backend/frontend run as
         native processes so hot reload works)
      2. backend  -> uvicorn app.main:app --reload on 127.0.0.1:8000
      3. frontend -> vite dev server on 5173 (proxies /api -> :8000)

    Both servers run detached; logs go to logs/dev/. Stop them with -Stop.

    NOTE: the script is intentionally ASCII-only. Windows PowerShell 5.1 reads
    .ps1 files using the ANSI code page (cp936 on a Chinese system), so non-ASCII
    source without a BOM gets mangled. Keep it ASCII if you edit it.

.PARAMETER Stop
    Stop the backend and frontend started by this script (and free ports).

.PARAMETER StopInfra
    With -Stop, also stop the db/redis containers.

.PARAMETER Restart
    Stop anything already running, then boot again.

.PARAMETER Logs
    Tail the four log files (backend/frontend, out/err). Ctrl+C to stop tailing.

.PARAMETER SkipInstall
    Do not install python/npm dependencies (fast start).

.PARAMETER Reinstall
    Force `pip install -e ".[dev]"` and `npm install` before booting.

.PARAMETER NoInfra
    Do not touch docker (assume PostgreSQL/Redis are already reachable).

.PARAMETER PythonPath
    Interpreter used to create backend/.venv. Auto-detected when omitted.

.EXAMPLE
    .\scripts\dev.ps1
.EXAMPLE
    .\scripts\dev.ps1 -Restart
.EXAMPLE
    .\scripts\dev.ps1 -Stop -StopInfra
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$StopInfra,
    [switch]$Restart,
    [switch]$Logs,
    [switch]$SkipInstall,
    [switch]$Reinstall,
    [switch]$NoInfra,
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- paths/consts
$Root       = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root 'backend'
$WebDir     = Join-Path $Root 'web'
$LogDir     = Join-Path $Root 'logs\dev'
$StateFile  = Join-Path $LogDir 'pids.json'

$BackendPort  = 8000
$FrontendPort = 5173
$BackendUrl   = "http://127.0.0.1:$BackendPort"
$FrontendUrl  = "http://localhost:$FrontendPort"

$BackendEnv = Join-Path $BackendDir '.env'
$RootEnv    = Join-Path $Root '.env'
$VenvPy     = Join-Path $BackendDir '.venv\Scripts\python.exe'

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "    OK  $Message" -ForegroundColor Green
}

function Write-Warn2([string]$Message) {
    Write-Host "    !!  $Message" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- small helpers
function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(400)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-Http([string]$Url, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $true }
        } catch {
            # not up yet
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Get-ListenerPids([int]$Port) {
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        return @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        return @()
    }
}

# Resolve whoever actually LISTENS on the port.
# Needed because `uvicorn --reload` and `npm run dev` both spawn children: the PID
# returned by Start-Process is not necessarily the process holding the socket.
function Resolve-ListenerPid([int]$Port, [int]$TimeoutSec = 5) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $found = Get-ListenerPids $Port
        if ($found.Count -gt 0) { return [int]$found[0] }
        Start-Sleep -Milliseconds 500
    }
    return 0
}

function Read-DotEnv([string]$Path) {
    $map = @{}
    if (-not (Test-Path $Path)) { return $map }
    foreach ($line in (Get-Content -Path $Path -Encoding UTF8)) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $i = $t.IndexOf('=')
        if ($i -lt 1) { continue }
        $map[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
    }
    return $map
}

function Assert-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "'$Name' not found on PATH. $Hint"
    }
}

# Run a native executable without letting its STDERR trip $ErrorActionPreference='Stop'.
#
# Why this exists: Windows PowerShell 5.1 converts *any* stderr line from a native
# command into a NativeCommandError, which terminates the script when EAP=Stop.
# docker compose / pip / npm all write normal progress to stderr, so a perfectly
# successful `docker compose up` would abort the whole boot. We therefore drop EAP
# to Continue for the duration of the native call and rely on the exit code instead.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @(),
        [string]$WorkDir = '',
        [switch]$Quiet
    )
    $prevEap = $ErrorActionPreference
    $prevLoc = (Get-Location).Path
    $ErrorActionPreference = 'Continue'
    try {
        if ($WorkDir) { Set-Location -LiteralPath $WorkDir }
        if ($Quiet) {
            & $Exe @Arguments 2>&1 | Out-Null
        } else {
            & $Exe @Arguments 2>&1 | ForEach-Object { Write-Host ('    ' + $_) }
        }
        return $LASTEXITCODE
    } finally {
        Set-Location -LiteralPath $prevLoc
        $ErrorActionPreference = $prevEap
    }
}

# ---------------------------------------------------------------- stop
function Stop-DevProcesses {
    $pids = New-Object System.Collections.Generic.HashSet[int]

    if (Test-Path $StateFile) {
        try {
            $st = Get-Content $StateFile -Raw | ConvertFrom-Json
            foreach ($p in @($st.backendPid, $st.frontendPid)) {
                if ($p -and [int]$p -gt 0) { [void]$pids.Add([int]$p) }
            }
        } catch {
            Write-Warn2 "cannot parse $StateFile (ignored)"
        }
    }

    # The uvicorn reloader spawns a child, and `npm run dev` spawns node:
    # killing the recorded parent is not enough, so also take whoever listens.
    foreach ($port in @($BackendPort, $FrontendPort)) {
        foreach ($p in (Get-ListenerPids $port)) {
            if ($p) { [void]$pids.Add([int]$p) }
        }
    }

    if ($pids.Count -eq 0) {
        Write-Ok 'no backend/frontend process found'
    }

    foreach ($id in $pids) {
        $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
        # Safety: only kill things that look like our dev servers, never a random
        # listener that happens to sit on the port.
        if ($proc -and $proc.ProcessName -notmatch '^(python|node|npm|cmd)$') {
            Write-Warn2 ("skip PID {0} ({1}): not a dev process" -f $id, $proc.ProcessName)
            continue
        }
        # taskkill /T also takes down the children (uvicorn reloader -> server,
        # npm -> node). Used even when Get-Process could not read the PID, because in
        # that case Stop-Process would silently do nothing while the process lives on.
        $rc = Invoke-Native -Exe 'taskkill' -Arguments @('/PID', "$id", '/T', '/F') -Quiet
        if ($rc -ne 0) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
        $name = if ($proc) { $proc.ProcessName } else { 'unknown' }
        Write-Ok ("stopped PID {0} ({1})" -f $id, $name)
    }

    # Second pass by command line: covers the case where the socket owner is a child
    # (uvicorn reloader / node under npm) that outlived its recorded parent.
    $orphans = @()
    try {
        $orphans = Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -like '*uvicorn*app.main:app*' -or
                $_.CommandLine -like '*node_modules\vite*' -or
                $_.CommandLine -like '*vite.js*'
            )
        }
    } catch {
        # Get-CimInstance can be blocked by policy; the port pass above is the fallback.
    }
    foreach ($o in $orphans) {
        Stop-Process -Id $o.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Ok ("stopped orphan PID {0} ({1})" -f $o.ProcessId, $o.Name)
    }

    # Only drop the state file when the ports are actually free; otherwise keep it so a
    # second `-Stop` still has something to work with.
    $still = @()
    foreach ($port in @($BackendPort, $FrontendPort)) {
        foreach ($p in (Get-ListenerPids $port)) { if ($p) { $still += $p } }
    }
    if ($still.Count -gt 0) {
        Write-Warn2 ("port(s) still listening (PID {0}) - state file kept, rerun -Stop or close them manually" -f (($still | Select-Object -Unique) -join ','))
        return
    }

    Remove-Item $StateFile -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------- config
function Initialize-BackendEnv {
    if (Test-Path $BackendEnv) {
        Write-Ok 'backend/.env present'
        return
    }
    $tpl = Join-Path $BackendDir '.env.dev.example'
    if (-not (Test-Path $tpl)) {
        throw "backend/.env is missing and $tpl not found; create backend/.env manually."
    }
    Copy-Item $tpl $BackendEnv
    Write-Warn2 'backend/.env created from backend/.env.dev.example - review it (dev credentials!)'
}

# Root .env only feeds docker compose (db/redis). It is DERIVED from backend/.env
# so the container's POSTGRES_* can never drift from what the app connects with.
function Sync-RootEnv {
    $appEnv  = Read-DotEnv $BackendEnv
    $dbName  = $appEnv['POSTGRES_DB']
    $dbUser  = $appEnv['POSTGRES_USER']
    $dbPass  = $appEnv['POSTGRES_PASSWORD']

    if (-not $dbName) { throw 'backend/.env has no POSTGRES_DB; cannot prepare docker db.' }

    if (Test-Path $RootEnv) {
        $rootEnv = Read-DotEnv $RootEnv
        if ($rootEnv['POSTGRES_DB'] -ne $dbName) {
            Write-Warn2 ("root .env POSTGRES_DB={0} != backend/.env POSTGRES_DB={1} - they MUST match" -f $rootEnv['POSTGRES_DB'], $dbName)
        }
        Write-Ok 'root .env present'
        return
    }

    $lines = @(
        '# Generated by scripts/dev.ps1 - local dev only (git-ignored).',
        '# Root .env is consumed by docker compose for the db/redis containers.',
        '# POSTGRES_* are copied from backend/.env so the container and the app',
        '# can never disagree about database name/user/password.',
        'APP_MODE=dev',
        "POSTGRES_USER=$dbUser",
        "POSTGRES_PASSWORD=$dbPass",
        "POSTGRES_DB=$dbName",
        '# dev redis has no password (prod fail-fasts if empty)',
        'REDIS_PASSWORD='
    )
    Set-Content -Path $RootEnv -Value $lines -Encoding UTF8
    Write-Ok 'root .env generated from backend/.env'
}

# ---------------------------------------------------------------- python
function Get-PythonVersion([string]$Exe) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe --version 2>&1
        return "$out"
    } catch {
        return ''
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Resolve-Python {
    if ($PythonPath) {
        if (-not (Test-Path $PythonPath)) { throw "PythonPath not found: $PythonPath" }
        return $PythonPath
    }
    # NOTE: on Windows a bare `python` may be the Microsoft Store stub, which
    # prints nothing and exits non-zero; and many machines have Python only inside a
    # conda/installer folder that is NOT on PATH. So gather candidates first, then
    # validate the version string instead of trusting the command name.
    $candidates = New-Object System.Collections.Generic.List[string]
    foreach ($name in @('python', 'python3')) {
        $c = Get-Command $name -ErrorAction SilentlyContinue
        if ($c) { $candidates.Add($c.Source) }
    }

    # py launcher -> ask for the real interpreter path (we always invoke python.exe
    # directly, the 'py' shim itself is not usable as our -Exe).
    if (Get-Command 'py' -ErrorAction SilentlyContinue) {
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { $pyOut = & py -3 -c "import sys; print(sys.executable)" 2>&1 } catch { $pyOut = '' }
        $ErrorActionPreference = $prevEap
        if ("$pyOut" -match 'python\.exe') { $candidates.Add(("$pyOut").Trim()) }
    }

    $globs = @()
    if ($env:LOCALAPPDATA) { $globs += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe') }
    if ($env:USERPROFILE) {
        $globs += (Join-Path $env:USERPROFILE 'anaconda3\python.exe')
        $globs += (Join-Path $env:USERPROFILE 'miniconda3\python.exe')
    }
    if ($env:CONDA_PREFIX) { $globs += (Join-Path $env:CONDA_PREFIX 'python.exe') }
    $globs += 'C:\Python3*\python.exe'
    $globs += 'C:\ProgramData\anaconda3\python.exe'
    foreach ($g in $globs) {
        foreach ($hit in (Resolve-Path $g -ErrorAction SilentlyContinue)) { $candidates.Add($hit.Path) }
    }

    foreach ($cand in $candidates) {
        $ver = Get-PythonVersion $cand
        if ($ver -match 'Python 3\.(1[1-9]|[2-9][0-9])') {
            Write-Host "    python: $cand ($ver)"
            return $cand
        }
    }
    throw 'No Python 3.11+ found (PATH, py launcher, conda and common install dirs checked). Install Python 3.11+, or pass -PythonPath <path\to\python.exe>.'
}

function Initialize-Venv {
    if (Test-Path $VenvPy) {
        Write-Ok 'backend/.venv present'
    } else {
        $py = Resolve-Python
        Write-Host "    creating venv with $py"
        $code = Invoke-Native -Exe $py -Arguments @('-m', 'venv', (Join-Path $BackendDir '.venv')) -WorkDir $BackendDir
        if ($code -ne 0) { throw 'venv creation failed' }
        Write-Ok 'backend/.venv created'
        if (-not $SkipInstall) {
            Write-Host '    installing backend deps (first run, a few minutes)'
            [void](Invoke-Native -Exe $VenvPy -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet') -WorkDir $BackendDir -Quiet)
            $code = Invoke-Native -Exe $VenvPy -Arguments @('-m', 'pip', 'install', '-e', '.[dev]') -WorkDir $BackendDir
            if ($code -ne 0) { throw 'pip install failed' }
            Write-Ok 'backend deps installed'
        }
        return
    }

    if ($Reinstall -and -not $SkipInstall) {
        Write-Host '    reinstalling backend deps'
        $code = Invoke-Native -Exe $VenvPy -Arguments @('-m', 'pip', 'install', '-e', '.[dev]') -WorkDir $BackendDir
        if ($code -ne 0) { throw 'pip install failed' }
        Write-Ok 'backend deps installed'
    }
}

function Initialize-WebDeps {
    $nodeModules = Join-Path $WebDir 'node_modules'
    if ((Test-Path $nodeModules) -and -not $Reinstall) {
        Write-Ok 'web/node_modules present'
        return
    }
    if ($SkipInstall) { Write-Warn2 'web/node_modules missing but -SkipInstall set'; return }
    Assert-Command 'npm' 'Install Node.js 22+ (vite/jsdom need a modern Node).'
    Write-Host '    npm install'
    $code = Invoke-Native -Exe 'npm' -Arguments @('install', '--no-audit', '--no-fund') -WorkDir $WebDir
    if ($code -ne 0) { throw 'npm install failed' }
    Write-Ok 'web deps installed'
}

# ---------------------------------------------------------------- infra
function Start-Infra {
    Assert-Command 'docker' 'Install Docker Desktop and make sure the daemon is running.'
    Write-Step 'infrastructure (PostgreSQL 15 + Redis 7)'
    $code = Invoke-Native -Exe 'docker' -Arguments @('compose', 'up', '-d', 'db', 'redis') -WorkDir $Root
    if ($code -ne 0) { throw 'docker compose up failed (is Docker running?)' }

    # wait for postgres to accept connections
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port 5432) { break }
        Start-Sleep -Seconds 2
    }
    if (-not (Test-Port 5432)) { throw 'PostgreSQL did not become reachable on 127.0.0.1:5432' }
    Write-Ok 'PostgreSQL reachable on 127.0.0.1:5432'
    Write-Ok 'Redis via 127.0.0.1:<REDIS_PORT in backend/.env> (see docker-compose.override.yml)'
}

# ---------------------------------------------------------------- migrations
function Invoke-Migrations {
    Write-Step 'database migrations (alembic upgrade head)'
    $code = Invoke-Native -Exe $VenvPy -Arguments @('-m', 'alembic', 'upgrade', 'head') -WorkDir $BackendDir
    if ($code -ne 0) { throw 'alembic upgrade head failed' }
    Write-Ok 'schema at head'
}

# ---------------------------------------------------------------- start
function Start-Backend {
    if (Test-Port $BackendPort) {
        $owner = Resolve-ListenerPid $BackendPort 3
        Write-Warn2 "port $BackendPort already in use (PID $owner) - not started; use -Restart to replace it"
        return @{ id = $owner; started = $false }
    }
    Write-Step "backend (uvicorn --reload, $BackendUrl)"
    $outLog = Join-Path $LogDir 'backend.out.log'
    $errLog = Join-Path $LogDir 'backend.err.log'
    $p = Start-Process -FilePath $VenvPy `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--reload', '--host', '127.0.0.1', '--port', "$BackendPort") `
        -WorkingDirectory $BackendDir `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
        -WindowStyle Hidden -PassThru
    Write-Ok ("started PID {0} -> logs/dev/backend.*.log" -f $p.Id)
    return @{ id = $p.Id; started = $true }
}

function Start-Frontend {
    if (Test-Port $FrontendPort) {
        $owner = Resolve-ListenerPid $FrontendPort 3
        Write-Warn2 "port $FrontendPort already in use (PID $owner) - not started; use -Restart to replace it"
        return @{ id = $owner; started = $false }
    }
    Assert-Command 'npm' 'Install Node.js 22+.'
    Write-Step "frontend (vite, $FrontendUrl)"
    $npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue)
    $exe = if ($npmCmd) { $npmCmd.Source } else { (Get-Command npm).Source }
    $outLog = Join-Path $LogDir 'frontend.out.log'
    $errLog = Join-Path $LogDir 'frontend.err.log'
    $p = Start-Process -FilePath $exe `
        -ArgumentList @('run', 'dev') `
        -WorkingDirectory $WebDir `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
        -WindowStyle Hidden -PassThru
    Write-Ok ("started PID {0} -> logs/dev/frontend.*.log" -f $p.Id)
    return @{ id = $p.Id; started = $true }
}

# ---------------------------------------------------------------- main
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if ($Stop -or $Restart) {
    Write-Step 'stopping dev servers'
    Stop-DevProcesses
    if ($StopInfra) {
        Write-Step 'stopping db/redis containers'
        [void](Invoke-Native -Exe 'docker' -Arguments @('compose', 'stop', 'db', 'redis') -WorkDir $Root)
    }
    if ($Stop -and -not $Restart) { return }
    Start-Sleep -Seconds 2
}

if ($Logs) {
    $files = @('backend.out.log', 'backend.err.log', 'frontend.out.log', 'frontend.err.log') |
        ForEach-Object { Join-Path $LogDir $_ } |
        Where-Object { Test-Path $_ }
    if (-not $files) { Write-Warn2 "no logs yet in $LogDir"; return }
    Write-Step 'tailing logs (Ctrl+C to stop)'
    Get-Content -Path $files -Wait -Tail 15
    return
}

Write-Step 'preflight'
Assert-Command 'docker' 'Install Docker Desktop and make sure the daemon is running.'
Initialize-BackendEnv
Sync-RootEnv
Initialize-Venv
Initialize-WebDeps
Write-Ok 'preflight done'

if (-not $NoInfra) { Start-Infra } else { Write-Warn2 '-NoInfra: skipping docker' }

Invoke-Migrations

$backend  = Start-Backend
$frontend = Start-Frontend

Write-Step 'waiting for services to come up'
$backendOk = Wait-Http "$BackendUrl/health" 180
$frontendOk = Wait-Http $FrontendUrl 180

$state = @{
    backendPid  = [int](Resolve-ListenerPid $BackendPort 10)
    frontendPid = [int](Resolve-ListenerPid $FrontendPort 10)
    startedAt   = (Get-Date).ToString('s')
}
# Fall back to the launcher PID when the socket owner cannot be resolved (e.g. the
# NetTCPIP cmdlets are blocked). Either way -Stop has something to work with.
if ($state.backendPid -eq 0 -and $backend.id)   { $state.backendPid = [int]$backend.id }
if ($state.frontendPid -eq 0 -and $frontend.id) { $state.frontendPid = [int]$frontend.id }
$state | ConvertTo-Json | Set-Content -Path $StateFile -Encoding UTF8

Write-Host ''
Write-Host '---------------- dev stack ----------------'
if ($backendOk)  { Write-Host ("  backend   {0}/health   [up]" -f $BackendUrl) -ForegroundColor Green }
else             { Write-Host ("  backend   {0}/health   [NOT READY - see logs/dev/backend.err.log]" -f $BackendUrl) -ForegroundColor Red }
if ($frontendOk) { Write-Host ("  API docs  {0}/docs" -f $BackendUrl) }
if ($frontendOk) { Write-Host ("  frontend  {0}   [up]" -f $FrontendUrl) -ForegroundColor Green }
else             { Write-Host ("  frontend  {0}   [NOT READY - see logs/dev/frontend.err.log]" -f $FrontendUrl) -ForegroundColor Red }
Write-Host ''
Write-Host ("  logs      {0}" -f $LogDir)
Write-Host '  stop      .\scripts\dev.ps1 -Stop      (add -StopInfra to also stop db/redis)'
Write-Host '  follow    .\scripts\dev.ps1 -Logs'
Write-Host '-------------------------------------------'

# Print the seeded admin username; never print the password (dev/.env holds it).
$adminUser = (Read-DotEnv $BackendEnv)['INIT_ADMIN_USERNAME']
if ($adminUser) {
    Write-Host ("  login     user '{0}' / password from INIT_ADMIN_PASSWORD in backend/.env" -f $adminUser)
}

if (-not ($backendOk -and $frontendOk)) { exit 1 }
