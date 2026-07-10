param(
  [string]$HostName = "192.168.3.246",
  [string]$User = "wte",
  [string]$RemoteScript = "/tmp/c63_keyboard_teleop.sh",
  [string]$RemoteWorkspace = "/home/wte/wheeltec_robot"
)

$ErrorActionPreference = "Stop"

$localScript = Join-Path $PSScriptRoot "c63_keyboard_teleop.sh"
if (-not (Test-Path -LiteralPath $localScript)) {
  throw "Missing local teleop script: $localScript"
}

$target = "${User}@${HostName}"

Write-Host "[info] Copying teleop script to ${target}:$RemoteScript"
scp -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no $localScript "${target}:$RemoteScript"

Write-Host "[info] Starting keyboard teleop over SSH. Press Ctrl-C to stop and exit."
ssh -tt -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no $target "chmod +x '$RemoteScript' && WORKSPACE='$RemoteWorkspace' '$RemoteScript'"
