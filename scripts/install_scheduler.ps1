param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,
    [string]$DataDir = "$env:APPDATA\PinForge",
    [string]$TaskName = "PinForge Queue Publisher"
)

$ErrorActionPreference = "Stop"
$PythonPath = [System.IO.Path]::GetFullPath($PythonPath)
$ProjectPath = [System.IO.Path]::GetFullPath($ProjectPath)
$DataDir = [System.IO.Path]::GetFullPath($DataDir)

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python executable not found: $PythonPath"
}
if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
    throw "Project directory not found: $ProjectPath"
}
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

$arguments = "-m pinforge --data-dir `"$DataDir`" drain-queue"
$action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument $arguments `
    -WorkingDirectory $ProjectPath
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 4)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Publishes due PinForge Pinterest queue items." `
    -Force | Out-Null

Write-Host "Scheduled task '$TaskName' installed."
