param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*$')]
    [string]$Server,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$IdentityFile,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$KnownHostsFile,
    [ValidateRange(1, 65535)]
    [int]$Port = 22,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')]
    [string]$ReleaseId = (Get-Date -Format 'yyyyMMdd-HHmmss'),
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
if ($ReleaseId.Contains('..')) { throw 'Invalid release ID.' }
$packageRoot = Split-Path -Parent $PSScriptRoot
foreach ($path in @($IdentityFile, $KnownHostsFile)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required SSH file does not exist: $path"
    }
}
$keyPath = (Resolve-Path -LiteralPath $IdentityFile).Path
$knownHosts = (Resolve-Path -LiteralPath $KnownHostsFile).Path.Replace('\', '/')
if ($knownHosts -match '["\r\n]') { throw 'Invalid known-hosts file path.' }
$sshExe = (Get-Command ssh.exe -ErrorAction Stop).Source
$scpExe = (Get-Command scp.exe -ErrorAction Stop).Source
$sshOptions = @('-i', $keyPath, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
    '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=30',
    '-o', 'ServerAliveCountMax=2', '-o', 'StrictHostKeyChecking=yes',
    '-o', ('UserKnownHostsFile="{0}"' -f $knownHosts))

function Invoke-Remote([string]$Command) {
    & $sshExe -p $port @sshOptions $server $Command
    if ($LASTEXITCODE -ne 0) { throw "Remote operation failed (exit $LASTEXITCODE)." }
}

if ($Rollback) {
    if (-not $PSBoundParameters.ContainsKey('ReleaseId')) {
        throw 'Specify the existing release to restore with -ReleaseId.'
    }
    Invoke-Remote "bash /opt/telegram-quiz-bot/current/deploy/install.sh --rollback '$ReleaseId'"
    return
}

$files = @('bot.py', 'telegraph_pages.py', 'example-quiz.json', 'quiz.schema.json', 'PROMPT.md', 'FORMAT.md', 'README.md',
    '.env.example', 'deploy/install.sh', 'deploy/telegram-quiz-bot.service', 'deploy/bot.env.example',
    'deploy/configure.py', 'deploy/setup-token.py', 'deploy/setup-telegraph.py', 'deploy/deploy-server.ps1',
    'DEPLOYMENT.md')
$files += @(Get-ChildItem -LiteralPath (Join-Path $packageRoot 'tests') -Filter 'test_*.py' -File |
    ForEach-Object { 'tests/' + $_.Name })
foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath (Join-Path $packageRoot $file) -PathType Leaf)) {
        throw "Required package file is missing: $file"
    }
}

$archive = Join-Path $env:TEMP ('telegram-quiz-' + [guid]::NewGuid().ToString('N') + '.tar.gz')
try {
    & tar.exe -czf $archive -C $packageRoot -- @files
    if ($LASTEXITCODE -ne 0) { throw 'Could not build the release archive.' }
    $remoteStage = (Invoke-Remote 'mktemp -d /tmp/telegram-quiz-deploy.XXXXXXXXXX' | Out-String).Trim()
    if ($remoteStage -notmatch '^/tmp/telegram-quiz-deploy\.[A-Za-z0-9]{10}$') {
        throw 'Unexpected remote staging path.'
    }
    & $scpExe -P $port @sshOptions $archive "${server}:$remoteStage/package.tar.gz"
    if ($LASTEXITCODE -ne 0) { throw 'Release upload failed.' }
    Invoke-Remote "set -eu; mkdir '$remoteStage/package'; tar -xzf '$remoteStage/package.tar.gz' -C '$remoteStage/package'; bash '$remoteStage/package/deploy/install.sh' '$remoteStage/package' '$ReleaseId'"
    Invoke-Remote "rm -rf -- '$remoteStage'"
    Write-Output "Release installed: $ReleaseId"
}
finally {
    if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
}
