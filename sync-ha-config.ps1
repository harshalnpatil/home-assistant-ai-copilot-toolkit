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
    ,

    [Parameter(HelpMessage = "Skip the Node-RED Project pull stage")]
    [switch]$SkipNodeRedDeployment,

    [Parameter(HelpMessage = "Restart Node-RED after a successful Project pull")]
    [switch]$RestartNodeRed,

    [switch]$SkipHaDeployment,

    [switch]$SkipHaPull,

    [switch]$SkipHaBackup,

    [switch]$SkipBackupRefresh,

    [switch]$SkipGeneratedArtifacts,

    [Parameter(HelpMessage = "Home Assistant base URL")]
    [string]$HassHost = "",

    [Parameter(HelpMessage = "Home Assistant long-lived access token")]
    [string]$HassToken = "",

    [Parameter(HelpMessage = "HA script entity that runs the managed config pull")]
    [string]$ManagedPullScriptEntityId = "",

    [Parameter(HelpMessage = "HA script entity that runs the managed config backup")]
    [string]$ManagedBackupScriptEntityId = "",

    [Parameter(HelpMessage = "HA script entity that runs the managed Node-RED Project pull")]
    [string]$ManagedNodeRedPullScriptEntityId = "",

    [Parameter(HelpMessage = "HA input_text entity used as run-ID/stage/status scratchpad")]
    [string]$StatusEntityId = "",

    [Parameter(HelpMessage = "Optional HA input_text entity that captures bounded Node-RED stderr on failure")]
    [string]$DiagnosticEntityId = "",

    [Parameter(HelpMessage = "Optional HA bootstrap script entity that creates the managed entities if missing")]
    [string]$BootstrapPullScriptEntityId = "",

    [Parameter(HelpMessage = "Per-stage timeout in seconds for managed HA stages")]
    [ValidateRange(5, 3600)][int]$StageTimeoutSeconds = 180,

    [Parameter(HelpMessage = "Poll interval in seconds while waiting for managed HA stages")]
    [ValidateRange(1, 30)][int]$PollIntervalSeconds = 2
)

$ErrorActionPreference = 'Stop'
if ($h) { $Help = $true }
if ($v) { $DoVerbose = $true }

# Toolkit mode selection:
#   - Explicit-path mode (default): publish sibling repos by layout, regenerate
#     local views. No Home Assistant REST calls.
#   - Managed mode (opt-in): publish repos, then drive HA REST to run the
#     managed pull, backup, and Node-RED Project pull scripts by run ID, with
#     bounded polling and bounded stderr capture. Activates when any managed
#     flag is passed or when config.json supplies managed entity IDs.
#
# Managed mode targets Home Assistant Supervisor installations running the
# Node-RED add-on with Projects enabled. It is opt-in and is not universal
# Node-RED support.
$usesExplicitLayout = [bool]($ConfigPath -or $WorkspaceRoot -or $HaConfigRepo -or $EditableRepo -or $NodeRedRepo -or $IndexOutput)
$managedFlagsPresent = [bool]($SkipNodeRedDeployment -or $RestartNodeRed -or $SkipHaDeployment -or $SkipHaPull -or $SkipHaBackup -or $SkipBackupRefresh -or $SkipGeneratedArtifacts -or $HassHost -or $HassToken -or $ManagedPullScriptEntityId -or $ManagedBackupScriptEntityId -or $ManagedNodeRedPullScriptEntityId -or $StatusEntityId -or $DiagnosticEntityId -or $BootstrapPullScriptEntityId)
if ($managedFlagsPresent -and $usesExplicitLayout) {
    throw 'Managed deployment flags are incompatible with explicit-path mode. Explicit-path mode only publishes local repositories; pass managed entity IDs via config.json or flags without -ConfigPath/-WorkspaceRoot/-HaConfigRepo/-EditableRepo/-NodeRedRepo/-IndexOutput.'
}

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

# --- Managed-mode helpers (Home Assistant REST) ---------------------------

function Get-DotEnvValue {
    param([string]$Name, [string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    foreach ($line in Get-Content $Path) {
        if ($line -match ('^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*)\s*$')) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Resolve-HassSetting {
    param([string]$Name, [string]$ExplicitValue, [string]$DefaultValue = '', [string]$DotEnvPath)
    if (-not [string]::IsNullOrWhiteSpace($ExplicitValue)) { return $ExplicitValue }
    $processValue = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if (-not [string]::IsNullOrWhiteSpace($processValue)) { return $processValue }
    $userValue = [Environment]::GetEnvironmentVariable($Name, 'User')
    if (-not [string]::IsNullOrWhiteSpace($userValue)) { return $userValue }
    $envValue = Get-DotEnvValue -Name $Name -Path $DotEnvPath
    if (-not [string]::IsNullOrWhiteSpace($envValue)) { return $envValue }
    return $DefaultValue
}

function Invoke-HassRest {
    param([string]$Method, [string]$Path, [object]$Body = $null)
    $uri = "$($script:HassBaseUri)$Path"
    $params = @{ Uri = $uri; Method = $Method; Headers = @{ Authorization = "Bearer $script:HassToken" }; ContentType = 'application/json' }
    if ($null -ne $Body) { $params.Body = $Body | ConvertTo-Json -Depth 8 -Compress }
    try { return Invoke-RestMethod @params } catch { throw "Home Assistant REST $Method $Path failed: $($_.Exception.Message)" }
}

function Get-HassState {
    param([string]$EntityId, [switch]$AllowMissing)
    try { return Invoke-HassRest -Method 'Get' -Path ("/api/states/" + $EntityId) }
    catch {
        if ($AllowMissing -and $_.Exception.Message -match '404|Not Found') { return $null }
        throw
    }
}

function Start-HassScript {
    param([string]$EntityId, [hashtable]$Variables)
    Invoke-HassRest -Method 'Post' -Path '/api/services/script/turn_on' -Body @{ entity_id = $EntityId; variables = $Variables } | Out-Null
}

function Wait-HassScriptIdle {
    param([string]$EntityId, [int]$TimeoutSeconds, [int]$PollSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $state = Get-HassState $EntityId
        if ($state.state -ne 'on') { return }
        Start-Sleep -Seconds $PollSeconds
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $EntityId to finish."
}

function Wait-HassStage {
    param(
        [string]$RunId,
        [ValidateSet('pull', 'backup', 'node_red')][string]$Stage,
        [string]$StatusEntity,
        [int]$TimeoutSeconds,
        [int]$PollSeconds,
        [string]$DiagnosticEntity
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $state = Get-HassState $StatusEntity
        $parts = @($state.state -split '\|', 4)
        if ($parts.Count -ne 4) { throw "Malformed status from $StatusEntity`: '$($state.state)'" }
        if ($parts[0] -ne $RunId) { throw "Status helper reported a different run ID ('$($parts[0])'); expected '$RunId'." }
        if ($parts[1] -ne $Stage) { throw "Status helper reported stage '$($parts[1])'; expected '$Stage'." }
        if ($parts[2] -eq 'success' -and $parts[3] -eq '0') { return }
        if ($parts[2] -eq 'failure') {
            $detail = ''
            if ($DiagnosticEntity) {
                $diag = Get-HassState $DiagnosticEntity -AllowMissing
                if ($diag -and $diag.state -and $diag.state -ne 'idle') { $detail = " stderr: $($diag.state)" }
            }
            throw "Home Assistant $Stage stage failed with return code $($parts[3]).$detail"
        }
        if ($parts[2] -ne 'running' -and $parts[2] -ne 'pending') { throw "Unexpected $Stage status '$($state.state)'." }
        Start-Sleep -Seconds $PollSeconds
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for Home Assistant $Stage status for run $RunId."
}

function Initialize-HassDeployment {
    param(
        [hashtable]$EntityIds,
        [int]$TimeoutSeconds,
        [int]$PollSeconds
    )
    $script:HassToken = Resolve-HassSetting -Name 'HASS_TOKEN' -ExplicitValue $HassToken -DotEnvPath (Join-Path $scriptRoot '.env')
    if ([string]::IsNullOrWhiteSpace($script:HassToken)) { throw 'HASS_TOKEN is required for managed mode. Set it in the process/User environment or a toolkit-local .env.' }
    $hassHostResolved = Resolve-HassSetting -Name 'HASS_HOST' -ExplicitValue $HassHost -DefaultValue 'http://homeassistant.local:8123' -DotEnvPath (Join-Path $scriptRoot '.env')
    $script:HassBaseUri = $hassHostResolved.TrimEnd('/')
    $config = Invoke-HassRest -Method 'Get' -Path '/api/config'
    if (-not $config.version) { throw 'Home Assistant REST authentication probe returned no version.' }
    foreach ($entity in @($EntityIds.ManagedPullScript, $EntityIds.ManagedBackupScript, $EntityIds.ManagedNodeRedPullScript)) {
        $state = Get-HassState $entity -AllowMissing
        if ($state -and $state.state -eq 'on') { throw "Managed deployment is already running: $entity" }
    }
    $managedPull = Get-HassState $EntityIds.ManagedPullScript -AllowMissing
    $managedBackup = Get-HassState $EntityIds.ManagedBackupScript -AllowMissing
    $managedNodeRed = Get-HassState $EntityIds.ManagedNodeRedPullScript -AllowMissing
    $status = Get-HassState $EntityIds.StatusEntity -AllowMissing
    if ((-not $managedPull -or -not $managedBackup -or -not $managedNodeRed -or -not $status) -and $EntityIds.BootstrapScript) {
        Write-Host '==> Bootstrapping managed Home Assistant sync entities' -ForegroundColor Cyan
        $bootstrap = Get-HassState $EntityIds.BootstrapScript -AllowMissing
        if (-not $bootstrap) { throw "Bootstrap script is missing: $($EntityIds.BootstrapScript)" }
        if ($bootstrap.state -eq 'on') { throw "Bootstrap script is already running: $($EntityIds.BootstrapScript)" }
        Invoke-HassRest -Method 'Post' -Path '/api/services/script/turn_on' -Body @{ entity_id = $EntityIds.BootstrapScript } | Out-Null
        Wait-HassScriptIdle -EntityId $EntityIds.BootstrapScript -TimeoutSeconds $TimeoutSeconds -PollSeconds $PollSeconds
        Invoke-HassRest -Method 'Post' -Path '/api/services/input_text/reload' -Body @{} | Out-Null
        foreach ($entity in @($EntityIds.ManagedPullScript, $EntityIds.ManagedBackupScript, $EntityIds.ManagedNodeRedPullScript, $EntityIds.StatusEntity)) {
            if (-not (Get-HassState $entity -AllowMissing)) { throw "Bootstrap completed, but managed entity is still missing: $entity" }
        }
    } elseif (-not $managedPull -or -not $managedBackup -or -not $managedNodeRed -or -not $status) {
        throw "Managed entities are missing and no -BootstrapPullScriptEntityId was provided. Missing: $((@($EntityIds.ManagedPullScript, $EntityIds.ManagedBackupScript, $EntityIds.ManagedNodeRedPullScript, $EntityIds.StatusEntity) | Where-Object { -not (Get-HassState $_ -AllowMissing) }) -join ', ')"
    }
}

function Invoke-ManagedSync {
    param(
        [hashtable]$EntityIds,
        [bool]$DoPull,
        [bool]$DoBackup,
        [bool]$DoNodeRed,
        [bool]$RestartNodeRed,
        [bool]$DoBackupRefresh,
        [bool]$DoGeneratedArtifacts,
        [string[]]$ReposToPublish,
        [string]$HaBackupRepo,
        [string]$NodeRedRepoForSplit,
        [string]$SplitScript,
        [string]$IndexScript,
        [string]$WorkspaceBaseForIndex,
        [string]$IndexOutput,
        [int]$TimeoutSeconds,
        [int]$PollSeconds
    )
    foreach ($repo in $ReposToPublish) {
        $msg = if ($repo -eq $NodeRedRepoForSplit) { 'Sync Node-RED flows from toolkit' } else { 'Sync editable HA config from toolkit' }
        Publish-BidirectionalRepo -RepoPath $repo -CommitMessage $msg
    }

    Initialize-HassDeployment -EntityIds $EntityIds -TimeoutSeconds $TimeoutSeconds -PollSeconds $PollSeconds

    $existingStatus = Get-HassState $EntityIds.StatusEntity
    if ($existingStatus.state -match '\|running\|') { throw "Managed deployment status is already running: $($existingStatus.state)" }

    if ($DoPull) {
        $runId = [guid]::NewGuid().ToString('N')
        Write-Host "==> Deploying editable config to Home Assistant (run $runId)" -ForegroundColor Cyan
        Invoke-HassRest -Method 'Post' -Path '/api/services/input_text/set_value' -Body @{ entity_id = $EntityIds.StatusEntity; value = "$runId|pull|pending|0" } | Out-Null
        Start-HassScript -EntityId $EntityIds.ManagedPullScript -Variables @{ run_id = $runId }
        Wait-HassStage -RunId $runId -Stage 'pull' -StatusEntity $EntityIds.StatusEntity -TimeoutSeconds $TimeoutSeconds -PollSeconds $PollSeconds -DiagnosticEntity $EntityIds.DiagnosticEntity
    } else { Write-Host '==> Skipping Home Assistant pull by request' -ForegroundColor Yellow }

    if ($DoBackup) {
        $runId = [guid]::NewGuid().ToString('N')
        Write-Host "==> Creating Home Assistant backup (run $runId)" -ForegroundColor Cyan
        Invoke-HassRest -Method 'Post' -Path '/api/services/input_text/set_value' -Body @{ entity_id = $EntityIds.StatusEntity; value = "$runId|backup|pending|0" } | Out-Null
        Start-HassScript -EntityId $EntityIds.ManagedBackupScript -Variables @{ run_id = $runId }
        Wait-HassStage -RunId $runId -Stage 'backup' -StatusEntity $EntityIds.StatusEntity -TimeoutSeconds $TimeoutSeconds -PollSeconds $PollSeconds -DiagnosticEntity $EntityIds.DiagnosticEntity
    } else { Write-Host '==> Skipping Home Assistant backup by request' -ForegroundColor Yellow }

    if ($DoNodeRed) {
        $runId = [guid]::NewGuid().ToString('N')
        Write-Host "==> Pulling Node-RED Project files (run $runId, restart=$RestartNodeRed)" -ForegroundColor Cyan
        Invoke-HassRest -Method 'Post' -Path '/api/services/input_text/set_value' -Body @{ entity_id = $EntityIds.StatusEntity; value = "$runId|node_red|pending|0" } | Out-Null
        Start-HassScript -EntityId $EntityIds.ManagedNodeRedPullScript -Variables @{ run_id = $runId; restart = [bool]$RestartNodeRed }
        Wait-HassStage -RunId $runId -Stage 'node_red' -StatusEntity $EntityIds.StatusEntity -TimeoutSeconds $TimeoutSeconds -PollSeconds $PollSeconds -DiagnosticEntity $EntityIds.DiagnosticEntity
    } else { Write-Host '==> Skipping Node-RED deployment by request' -ForegroundColor Yellow }

    if ($DoBackupRefresh -and $HaBackupRepo) {
        Write-Host "`n==> Pulling one-way HA backup repo: $HaBackupRepo" -ForegroundColor Cyan
        Invoke-GitChecked $HaBackupRepo @('pull', '--ff-only') 'git pull --ff-only failed for one-way backup repo'
    }

    if ($DoGeneratedArtifacts -and $NodeRedRepoForSplit -and (Test-Path (Join-Path $NodeRedRepoForSplit 'flows.json'))) {
        Write-Host "`n==> Splitting Node-RED flows" -ForegroundColor Cyan
        $splitOutput = Join-Path $NodeRedRepoForSplit 'output\flows-split'
        if ($DoVerbose) {
            python $SplitScript --root $NodeRedRepoForSplit --input (Join-Path $NodeRedRepoForSplit 'flows.json') --output $splitOutput -v
        } else {
            python $SplitScript --root $NodeRedRepoForSplit --input (Join-Path $NodeRedRepoForSplit 'flows.json') --output $splitOutput
        }
        if ($LASTEXITCODE -ne 0) { throw "split_flows.py failed (exit $LASTEXITCODE)" }
        Write-Host "`n==> Building toolkit index" -ForegroundColor Cyan
        $indexArgs = @($IndexScript, '--root', $WorkspaceBaseForIndex)
        if ($IndexOutput) { $indexArgs += @('--output', $IndexOutput) }
        python @indexArgs
        if ($LASTEXITCODE -ne 0) { throw "build_ha_index.py failed (exit $LASTEXITCODE)" }
    }
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

Managed mode (opt-in, requires Home Assistant Supervisor + Node-RED add-on
with Projects). Activates when any managed flag is passed or when config.json
supplies managed entity IDs. Targets HA Supervisor installations only; it is
not universal Node-RED support:

  -SkipNodeRedDeployment  Omit the managed Node-RED Project pull.
  -RestartNodeRed         Request an add-on restart after a successful pull.
                          Activation is the Node-RED flow's responsibility.
  -SkipHaDeployment       Skip both managed HA stages (pull and backup).
  -SkipHaPull             Skip only the managed HA pull.
  -SkipHaBackup           Skip only the managed HA backup.
  -SkipBackupRefresh      Skip only the local HA backup refresh.
  -SkipGeneratedArtifacts Skip flow splitting and index generation.
  -HassHost <url>         Home Assistant base URL.
  -HassToken <token>      Home Assistant long-lived access token.
  -ManagedPullScriptEntityId <entity_id>
  -ManagedBackupScriptEntityId <entity_id>
  -ManagedNodeRedPullScriptEntityId <entity_id>
  -StatusEntityId <entity_id>
  -DiagnosticEntityId <entity_id>    Optional, captures bounded Node-RED stderr.
  -BootstrapPullScriptEntityId <entity_id>
                                      Optional, creates managed entities if missing.
  -StageTimeoutSeconds <int>         Per-stage timeout (default 180).
  -PollIntervalSeconds <int>         Poll interval (default 2).

HASS_TOKEN and HASS_HOST precedence in managed mode: explicit flag, process
environment, Windows User environment, toolkit-local .env. HASS_HOST defaults
to http://homeassistant.local:8123.

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

# Resolve managed-entity IDs from explicit flags first, then config.json.
$managedPullScript = if ($ManagedPullScriptEntityId) { $ManagedPullScriptEntityId } elseif ($config) { Get-ConfigValue -Config $config -Name 'managedPullScriptEntityId' } else { '' }
$managedBackupScript = if ($ManagedBackupScriptEntityId) { $ManagedBackupScriptEntityId } elseif ($config) { Get-ConfigValue -Config $config -Name 'managedBackupScriptEntityId' } else { '' }
$managedNodeRedPullScript = if ($ManagedNodeRedPullScriptEntityId) { $ManagedNodeRedPullScriptEntityId } elseif ($config) { Get-ConfigValue -Config $config -Name 'managedNodeRedPullScriptEntityId' } else { '' }
$statusEntity = if ($StatusEntityId) { $StatusEntityId } elseif ($config) { Get-ConfigValue -Config $config -Name 'statusEntityId' } else { '' }
$diagnosticEntity = if ($DiagnosticEntityId) { $DiagnosticEntityId } elseif ($config) { Get-ConfigValue -Config $config -Name 'diagnosticEntityId' } else { '' }
$bootstrapScript = if ($BootstrapPullScriptEntityId) { $BootstrapPullScriptEntityId } elseif ($config) { Get-ConfigValue -Config $config -Name 'bootstrapPullScriptEntityId' } else { '' }
$configStageTimeout = if ($config) { Get-ConfigValue -Config $config -Name 'stageTimeoutSeconds' } else { $null }
$configPollInterval = if ($config) { Get-ConfigValue -Config $config -Name 'pollIntervalSeconds' } else { $null }
if ($configStageTimeout) { $StageTimeoutSeconds = [int]$configStageTimeout }
if ($configPollInterval) { $PollIntervalSeconds = [int]$configPollInterval }

$managedEntityIds = @{
    ManagedPullScript        = $managedPullScript
    ManagedBackupScript      = $managedBackupScript
    ManagedNodeRedPullScript = $managedNodeRedPullScript
    StatusEntity             = $statusEntity
    DiagnosticEntity         = $diagnosticEntity
    BootstrapScript          = $bootstrapScript
}

# Managed mode is active when managed flags were passed OR config supplies the
# minimum required managed entity IDs (pull, backup, node-red, status).
$managedConfigPresent = [bool]($managedPullScript -and $managedBackupScript -and $managedNodeRedPullScript -and $statusEntity)
$managedModeActive = $managedFlagsPresent -or $managedConfigPresent

if ($managedModeActive) {
    if (-not $managedPullScript -or -not $managedBackupScript -or -not $managedNodeRedPullScript -or -not $statusEntity) {
        throw 'Managed mode requires -ManagedPullScriptEntityId, -ManagedBackupScriptEntityId, -ManagedNodeRedPullScriptEntityId, and -StatusEntityId (or their config.json equivalents).'
    }
    if (-not (Test-Path $editableRepo)) { throw "Editable repo not found: $editableRepo" }
    if (-not (Test-Path $nodeRedRepo)) { throw "Node-RED repo not found: $nodeRedRepo" }
    $doPull = -not $SkipHaDeployment -and -not $SkipHaPull
    $doBackup = -not $SkipHaDeployment -and -not $SkipHaBackup
    $doNodeRed = -not $SkipNodeRedDeployment
    $doBackupRefresh = -not $SkipBackupRefresh
    $doGeneratedArtifacts = -not $SkipGeneratedArtifacts
    $splitScript = Join-Path $scriptRoot 'scripts\split_flows.py'
    $buildIndexScript = Join-Path $scriptRoot 'build_ha_index.py'
    Invoke-ManagedSync `
        -EntityIds $managedEntityIds `
        -DoPull $doPull `
        -DoBackup $doBackup `
        -DoNodeRed $doNodeRed `
        -RestartNodeRed ([bool]$RestartNodeRed) `
        -DoBackupRefresh $doBackupRefresh `
        -DoGeneratedArtifacts $doGeneratedArtifacts `
        -ReposToPublish @($editableRepo, $nodeRedRepo) `
        -HaBackupRepo $haRepo `
        -NodeRedRepoForSplit $nodeRedRepo `
        -SplitScript $splitScript `
        -IndexScript $buildIndexScript `
        -WorkspaceBaseForIndex $workspaceBase `
        -IndexOutput $indexOutputRoot `
        -TimeoutSeconds $StageTimeoutSeconds `
        -PollSeconds $PollIntervalSeconds
    Write-Host "`nAll steps completed successfully." -ForegroundColor Green
    exit 0
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
