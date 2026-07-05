# Sync sibling Home Assistant repos from an explicit workspace layout.
# Run from anywhere. The script expects either explicit repo paths or a config
# file that names them. The conventional layout is documented in
# config.example.json.
#
# Flags:
#   -v, --verbose   Show detailed output (default: summary only)
#   -h, --help      Show this help message

[CmdletBinding()]
param(
    [Parameter(HelpMessage = "Show this help message")]
    [switch]$Help,

    [Parameter(HelpMessage = "Show this help message (short form)")]
    [switch]$h,

    [Parameter(HelpMessage = "Path to config.json with workspace and repo paths")]
    [string]$ConfigPath = "",

    [Parameter(HelpMessage = "Workspace root used for relative defaults")]
    [string]$WorkspaceRoot = "",

    [Parameter(HelpMessage = "Home Assistant backup repo path")]
    [string]$HaConfigRepo = "",

    [Parameter(HelpMessage = "Editable Home Assistant repo path")]
    [string]$EditableRepo = "",

    [Parameter(HelpMessage = "Node-RED repo path")]
    [string]$NodeRedRepo = "",

    [Parameter(HelpMessage = "Output directory for generated index files")]
    [string]$IndexOutput = "",

    [Parameter(HelpMessage = "Show detailed output (default: summary only)")]
    [switch]$DoVerbose,

    [Parameter(HelpMessage = "Show detailed output - short form (default: summary only)")]
    [switch]$v
)

$ErrorActionPreference = 'Stop'
if ($h) { $Help = $true }
if ($v) { $DoVerbose = $true }

$scriptRoot = $PSScriptRoot
if (-not $scriptRoot) {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

function Get-ConfigValue {
    param(
        [Parameter(Mandatory = $true)][object]$Config,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ($null -eq $Config) {
        return $null
    }
    if ($Config.PSObject.Properties.Name -contains $Name) {
        return $Config.$Name
    }
    return $null
}

function Resolve-AbsolutePath {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Missing required path for $Label. Pass it explicitly or add it to config.json."
    }
    if ([System.IO.Path]::IsPathRooted($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $BasePath $Value))
}

function Invoke-GitChecked {
    param(
        [Parameter(Mandatory = $true)][string]$RepoPath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )

    & git -C $RepoPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$FailureMessage (exit $LASTEXITCODE)" }
}

function Get-RepoStatus {
    param([Parameter(Mandatory = $true)][string]$RepoPath)

    $status = & git -C $RepoPath status --porcelain
    if ($LASTEXITCODE -ne 0) { throw "git status failed in $RepoPath (exit $LASTEXITCODE)" }
    return $status
}

function Publish-BidirectionalRepo {
    param(
        [Parameter(Mandatory = $true)][string]$RepoPath,
        [Parameter(Mandatory = $true)][string]$CommitMessage
    )

    Write-Host "`n==> Syncing: $RepoPath" -ForegroundColor Cyan
    Invoke-GitChecked $RepoPath @('fetch', 'origin') 'git fetch failed'

    $branch = (& git -C $RepoPath branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $branch) { throw "Could not determine current branch in $RepoPath" }

    $upstream = (& git -C $RepoPath rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $upstream) {
        $upstream = "origin/$branch"
    } else {
        $upstream = $upstream.Trim()
    }

    $aheadBehind = (& git -C $RepoPath rev-list --left-right --count "$upstream...HEAD").Trim() -split '\s+'
    if ($LASTEXITCODE -ne 0) { throw "Could not compare $RepoPath with $upstream" }
    $behind = [int]$aheadBehind[0]
    $ahead = [int]$aheadBehind[1]

    $status = Get-RepoStatus $RepoPath
    $hasLocalChanges = [bool]$status

    if ($hasLocalChanges -and $behind -gt 0) {
        Write-Host "  Skipped, local changes and upstream is newer. Resolve manually." -ForegroundColor Yellow
        return
    }

    if (-not $hasLocalChanges -and $behind -gt 0) {
        Invoke-GitChecked $RepoPath @('pull', '--ff-only') 'git pull --ff-only failed'
        return
    }

    if ($hasLocalChanges) {
        Invoke-GitChecked $RepoPath @('add', '-A') 'git add failed'
        Invoke-GitChecked $RepoPath @('commit', '-m', $CommitMessage) 'git commit failed'
        Invoke-GitChecked $RepoPath @('push') 'git push failed'
        return
    }

    if ($ahead -gt 0) {
        Invoke-GitChecked $RepoPath @('push') 'git push failed'
        return
    }

    Write-Host '  No local changes to commit.' -ForegroundColor DarkGray
}

if ($Help) {
    Write-Host @"
Usage: .\sync-ha-config.ps1 [OPTIONS]

Sync sibling Home Assistant repos and regenerate the public toolkit outputs.

Expected layout, unless you pass explicit paths:
  <workspace>/home-assistant-config
  <workspace>/home-assistant-config-editable
  <workspace>/home-assistant-node-red

The paths can also come from config.json with keys:
  workspaceRoot, haConfigRepo, editableRepo, nodeRedRepo, indexOutput

Options:
  -WorkspaceRoot <path>   Workspace root used for relative defaults.
  -ConfigPath <path>      JSON config file with the repo paths.
  -HaConfigRepo <path>    One-way backup repo path.
  -EditableRepo <path>    Editable HA repo path.
  -NodeRedRepo <path>     Node-RED repo path.
  -IndexOutput <path>     Output directory for generated index files.
  -v, -Verbose            Show detailed output per file.
  -h, -Help               Show this help message
"@
    exit 0
}

$config = $null
if ($ConfigPath) {
    $configPathResolved = Resolve-Path $ConfigPath
    $config = Get-Content $configPathResolved.Path -Raw | ConvertFrom-Json
}

$workspaceBase = if ($WorkspaceRoot) {
    [System.IO.Path]::GetFullPath($WorkspaceRoot)
} elseif ($config) {
    $value = Get-ConfigValue -Config $config -Name 'workspaceRoot'
    if ($value) { Resolve-AbsolutePath -Value $value -BasePath $scriptRoot -Label 'workspaceRoot' } else { $scriptRoot }
} else {
    $scriptRoot
}

$haRepo = if ($HaConfigRepo) {
    Resolve-AbsolutePath -Value $HaConfigRepo -BasePath $workspaceBase -Label 'HaConfigRepo'
} elseif ($config) {
    $value = Get-ConfigValue -Config $config -Name 'haConfigRepo'
    if ($value) {
        Resolve-AbsolutePath -Value $value -BasePath $workspaceBase -Label 'haConfigRepo'
    } else {
        Resolve-AbsolutePath -Value (Join-Path $workspaceBase 'home-assistant-config') -BasePath $workspaceBase -Label 'HaConfigRepo'
    }
} else {
    Resolve-AbsolutePath -Value (Join-Path $workspaceBase 'home-assistant-config') -BasePath $workspaceBase -Label 'HaConfigRepo'
}

$editableRepo = if ($EditableRepo) {
    Resolve-AbsolutePath -Value $EditableRepo -BasePath $workspaceBase -Label 'EditableRepo'
} elseif ($config) {
    $value = Get-ConfigValue -Config $config -Name 'editableRepo'
    if ($value) {
        Resolve-AbsolutePath -Value $value -BasePath $workspaceBase -Label 'editableRepo'
    } else {
        Resolve-AbsolutePath -Value (Join-Path $workspaceBase 'home-assistant-config-editable') -BasePath $workspaceBase -Label 'EditableRepo'
    }
} else {
    Resolve-AbsolutePath -Value (Join-Path $workspaceBase 'home-assistant-config-editable') -BasePath $workspaceBase -Label 'EditableRepo'
}

$nodeRedRepo = if ($NodeRedRepo) {
    Resolve-AbsolutePath -Value $NodeRedRepo -BasePath $workspaceBase -Label 'NodeRedRepo'
} elseif ($config) {
    $value = Get-ConfigValue -Config $config -Name 'nodeRedRepo'
    if ($value) {
        Resolve-AbsolutePath -Value $value -BasePath $workspaceBase -Label 'nodeRedRepo'
    } else {
        Resolve-AbsolutePath -Value (Join-Path $workspaceBase 'home-assistant-node-red') -BasePath $workspaceBase -Label 'NodeRedRepo'
    }
} else {
    Resolve-AbsolutePath -Value (Join-Path $workspaceBase 'home-assistant-node-red') -BasePath $workspaceBase -Label 'NodeRedRepo'
}

$indexOutputRoot = if ($IndexOutput) {
    Resolve-AbsolutePath -Value $IndexOutput -BasePath $workspaceBase -Label 'IndexOutput'
} elseif ($config) {
    $value = Get-ConfigValue -Config $config -Name 'indexOutput'
    if ($value) { Resolve-AbsolutePath -Value $value -BasePath $workspaceBase -Label 'indexOutput' } else { Join-Path $workspaceBase 'output\ha-index' }
} else {
    Join-Path $workspaceBase 'output\ha-index'
}

if (-not (Test-Path $haRepo)) { throw "Home Assistant backup repo not found: $haRepo" }
if (-not (Test-Path $editableRepo)) { throw "Editable repo not found: $editableRepo" }
if (-not (Test-Path $nodeRedRepo)) { throw "Node-RED repo not found: $nodeRedRepo" }

Write-Host "==> Pulling backup repo: $haRepo" -ForegroundColor Cyan
Invoke-GitChecked $haRepo @('pull') 'git pull failed for backup repo'

Publish-BidirectionalRepo -RepoPath $editableRepo -CommitMessage 'Sync editable HA config from toolkit'
Publish-BidirectionalRepo -RepoPath $nodeRedRepo -CommitMessage 'Sync Node-RED flows from toolkit'

$splitScript = Join-Path $scriptRoot 'scripts\split_flows.py'
$buildIndexScript = Join-Path $scriptRoot 'build_ha_index.py'

if (Test-Path (Join-Path $nodeRedRepo 'flows.json')) {
    Write-Host "`n==> Splitting Node-RED flows" -ForegroundColor Cyan
    $splitOutput = Join-Path $nodeRedRepo 'output\flows-split'
    if ($DoVerbose) {
        python $splitScript --root $nodeRedRepo --input (Join-Path $nodeRedRepo 'flows.json') --output $splitOutput -v
    } else {
        python $splitScript --root $nodeRedRepo --input (Join-Path $nodeRedRepo 'flows.json') --output $splitOutput
    }
    if ($LASTEXITCODE -ne 0) { throw "split_flows.py failed (exit $LASTEXITCODE)" }
}

Write-Host "`n==> Building toolkit index" -ForegroundColor Cyan
python $buildIndexScript --root $workspaceBase --ha-config-root $haRepo --editable-root $editableRepo --node-red-root $nodeRedRepo --output $indexOutputRoot
if ($LASTEXITCODE -ne 0) { throw "build_ha_index.py failed (exit $LASTEXITCODE)" }

Write-Host "`nAll steps completed successfully." -ForegroundColor Green
