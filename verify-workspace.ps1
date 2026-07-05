[CmdletBinding()]
param(
    [Parameter(HelpMessage = "Show this help message")]
    [switch]$Help,

    [Parameter(HelpMessage = "Show this help message (short form)")]
    [switch]$h,

    [Parameter(HelpMessage = "Workspace root used for relative defaults")]
    [string]$Root = "",

    [Parameter(HelpMessage = "Optional Home Assistant config export root")]
    [string]$HaConfigRoot = "",

    [Parameter(HelpMessage = "Optional editable Home Assistant repo root")]
    [string]$EditableRoot = "",

    [Parameter(HelpMessage = "Optional Node-RED repo root")]
    [string]$NodeRedRoot = "",

    [Parameter(HelpMessage = "Optional Lovelace repo root")]
    [string]$LovelaceRoot = "",

    [Parameter(HelpMessage = "Optional prebuilt index output directory")]
    [string]$IndexOutput = ""
)

$ErrorActionPreference = 'Stop'
if ($h) { $Help = $true }

$scriptRoot = $PSScriptRoot
if (-not $scriptRoot) {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

if ($Help) {
    Write-Host @"
Usage: .\verify-workspace.ps1 [OPTIONS]

Run the toolkit smoke tests.

The verifier accepts optional sibling repo paths. If you do not pass them, it
will only check the scripts and any example data that exists under the chosen
root.

Options:
  -Root <path>          Workspace root used for relative defaults.
  -HaConfigRoot <path>  Optional Home Assistant config export root.
  -EditableRoot <path>  Optional editable Home Assistant repo root.
  -NodeRedRoot <path>   Optional Node-RED repo root.
  -LovelaceRoot <path>  Optional Lovelace repo root.
  -IndexOutput <path>   Optional index output directory.
  -h, -Help             Show this help message
"@
    exit 0
}

$rootPath = if ($Root) { (Resolve-Path $Root).Path } else { $scriptRoot }
Set-Location $scriptRoot

$pythonArgs = @(
    '.\verify_workspace.py',
    '--root', $rootPath
)
if ($HaConfigRoot) { $pythonArgs += @('--ha-config-root', $HaConfigRoot) }
if ($EditableRoot) { $pythonArgs += @('--editable-root', $EditableRoot) }
if ($NodeRedRoot) { $pythonArgs += @('--node-red-root', $NodeRedRoot) }
if ($LovelaceRoot) { $pythonArgs += @('--lovelace-root', $LovelaceRoot) }
if ($IndexOutput) { $pythonArgs += @('--index-output', $IndexOutput) }

python @pythonArgs
exit $LASTEXITCODE
