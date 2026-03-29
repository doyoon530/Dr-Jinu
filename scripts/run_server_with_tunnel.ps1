param(
  [int]$Port = 5000,
  [string]$BindHost = "127.0.0.1",
  [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$tmpDir = Join-Path $projectRoot "docs\.tmp"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

$serverOutLog = Join-Path $tmpDir "server-tunnel.out.log"
$serverErrLog = Join-Path $tmpDir "server-tunnel.err.log"
$tunnelOutLog = Join-Path $tmpDir "cloudflared-tunnel.out.log"
$tunnelErrLog = Join-Path $tmpDir "cloudflared-tunnel.err.log"

if (-not ("KillOnCloseJob.Native" -as [type])) {
  Add-Type -Language CSharp -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace KillOnCloseJob {
  public static class Native {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetInformationJobObject(
      IntPtr hJob,
      JOBOBJECTINFOCLASS JobObjectInformationClass,
      IntPtr lpJobObjectInfo,
      uint cbJobObjectInfoLength
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool CloseHandle(IntPtr handle);

    public const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;

    public enum JOBOBJECTINFOCLASS {
      JobObjectExtendedLimitInformation = 9
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct IO_COUNTERS {
      public ulong ReadOperationCount;
      public ulong WriteOperationCount;
      public ulong OtherOperationCount;
      public ulong ReadTransferCount;
      public ulong WriteTransferCount;
      public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
      public long PerProcessUserTimeLimit;
      public long PerJobUserTimeLimit;
      public uint LimitFlags;
      public UIntPtr MinimumWorkingSetSize;
      public UIntPtr MaximumWorkingSetSize;
      public uint ActiveProcessLimit;
      public UIntPtr Affinity;
      public uint PriorityClass;
      public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
      public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
      public IO_COUNTERS IoInfo;
      public UIntPtr ProcessMemoryLimit;
      public UIntPtr JobMemoryLimit;
      public UIntPtr PeakProcessMemoryUsed;
      public UIntPtr PeakJobMemoryUsed;
    }
  }
}
"@
}

function New-KillOnCloseJobHandle {
  $jobHandle = [KillOnCloseJob.Native]::CreateJobObject([IntPtr]::Zero, $null)
  if ($jobHandle -eq [IntPtr]::Zero) {
    throw "Failed to create Windows Job Object."
  }

  $info = New-Object KillOnCloseJob.Native+JOBOBJECT_EXTENDED_LIMIT_INFORMATION
  $info.BasicLimitInformation.LimitFlags = [KillOnCloseJob.Native]::JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

  $size = [Runtime.InteropServices.Marshal]::SizeOf([type]([KillOnCloseJob.Native+JOBOBJECT_EXTENDED_LIMIT_INFORMATION]))
  $ptr = [Runtime.InteropServices.Marshal]::AllocHGlobal($size)

  try {
    [Runtime.InteropServices.Marshal]::StructureToPtr($info, $ptr, $false)
    $ok = [KillOnCloseJob.Native]::SetInformationJobObject(
      $jobHandle,
      [KillOnCloseJob.Native+JOBOBJECTINFOCLASS]::JobObjectExtendedLimitInformation,
      $ptr,
      [uint32]$size
    )

    if (-not $ok) {
      throw "Failed to configure Job Object."
    }
  }
  finally {
    [Runtime.InteropServices.Marshal]::FreeHGlobal($ptr)
  }

  return $jobHandle
}

function Add-ProcessToJob {
  param(
    [Parameter(Mandatory = $true)]$JobHandle,
    [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process
  )

  $null = $Process.Handle
  $ok = [KillOnCloseJob.Native]::AssignProcessToJobObject($JobHandle, $Process.Handle)
  if (-not $ok) {
    throw "Failed to assign process ID=$($Process.Id) to Job Object."
  }
}

function Resolve-CommandPath {
  param([Parameter(Mandatory = $true)][string]$CommandName)
  $command = Get-Command $CommandName -ErrorAction SilentlyContinue
  if ($command) {
    return $command.Source
  }
  return $null
}

function Get-CloudflaredPath {
  $direct = Resolve-CommandPath -CommandName "cloudflared"
  if ($direct) {
    return $direct
  }

  $candidates = @(
    "$env:ProgramFiles\cloudflared\cloudflared.exe",
    "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Links\cloudflared.exe"
  )

  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }

  $wingetMatch = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter "cloudflared.exe" -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1

  if ($wingetMatch) {
    return $wingetMatch.FullName
  }

  throw "cloudflared executable was not found."
}

function Stop-ExistingManagedProcesses {
  param(
    [int]$Port
  )

  $pythonTargets = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.Name -match '^python(\.exe)?$' -and
      $_.CommandLine -match 'app\.py' -and
      $_.CommandLine -match 'ncai-dementia-risk-monitor'
    }

  foreach ($proc in $pythonTargets) {
    try {
      Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
    } catch {}
  }

  $tunnelTargets = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.Name -eq 'cloudflared.exe' -and
      $_.CommandLine -match 'tunnel' -and
      $_.CommandLine -match "127\.0\.0\.1:$Port"
    }

  foreach ($proc in $tunnelTargets) {
    try {
      Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
    } catch {}
  }
}

function Wait-ForHealth {
  param(
    [string]$Url,
    [int]$TimeoutSeconds = 90
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
      if ($response.StatusCode -eq 200) {
        return $true
      }
    } catch {}
    Start-Sleep -Milliseconds 1200
  }

  throw "Server health check failed: $Url"
}

function Get-HealthPayload {
  param([string]$Url)

  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
    if ($response.StatusCode -eq 200 -and $response.Content) {
      return $response.Content | ConvertFrom-Json
    }
  } catch {}

  return $null
}

function Get-LanUrls {
  param([int]$Port)

  $urls = New-Object System.Collections.Generic.List[string]

  try {
    $configs = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
      Where-Object {
        $_.IPAddress -match '^\d+\.\d+\.\d+\.\d+$' -and
        $_.IPAddress -notlike '127.*' -and
        $_.PrefixOrigin -ne 'WellKnown'
      } |
      Sort-Object InterfaceMetric, InterfaceAlias, IPAddress

    foreach ($config in $configs) {
      $urls.Add(("http://{0}:{1}" -f $config.IPAddress, $Port))
    }
  } catch {}

  return $urls | Select-Object -Unique
}

function Wait-ForTunnelUrl {
  param(
    [string]$LogPath,
    [int]$TimeoutSeconds = 90
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-Path $LogPath) {
      try {
        $match = Get-Content -Path $LogPath -ErrorAction SilentlyContinue |
          Select-String -Pattern 'https://[-a-z0-9]+\.trycloudflare\.com' |
          Select-Object -First 1
        if ($match) {
          return $match.Matches[0].Value
        }
      } catch {}
    }
    Start-Sleep -Milliseconds 1200
  }

  throw "Could not resolve the Cloudflare Quick Tunnel URL."
}

$pythonPath = Resolve-CommandPath -CommandName "python"
if (-not $pythonPath) {
  throw "python command was not found."
}

$cloudflaredPath = Get-CloudflaredPath

if ($ValidateOnly) {
  Write-Host "Python: $pythonPath"
  Write-Host "cloudflared: $cloudflaredPath"
  Write-Host "Project: $projectRoot"
  exit 0
}

Stop-ExistingManagedProcesses -Port $Port

$jobHandle = $null
$serverProcess = $null
$tunnelProcess = $null

try {
  $jobHandle = New-KillOnCloseJobHandle

  Remove-Item $serverOutLog, $serverErrLog, $tunnelOutLog, $tunnelErrLog -Force -ErrorAction SilentlyContinue

  $serverProcess = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList "app.py" `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $serverOutLog `
    -RedirectStandardError $serverErrLog `
    -PassThru `
    -WindowStyle Hidden

  Add-ProcessToJob -JobHandle $jobHandle -Process $serverProcess

  $healthUrl = "http://{0}:{1}/health" -f $BindHost, $Port

  Write-Host ""
  Write-Host "Starting Dr. Jinu local server..."
  Wait-ForHealth -Url $healthUrl
  $healthPayload = Get-HealthPayload -Url $healthUrl
  $lanUrls = Get-LanUrls -Port $Port

  $tunnelProcess = Start-Process `
    -FilePath $cloudflaredPath `
    -ArgumentList @("tunnel", "--url", "http://$BindHost`:$Port", "--loglevel", "info", "--no-autoupdate") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $tunnelOutLog `
    -RedirectStandardError $tunnelErrLog `
    -PassThru `
    -WindowStyle Hidden

  Add-ProcessToJob -JobHandle $jobHandle -Process $tunnelProcess

  Write-Host "Opening Cloudflare Quick Tunnel..."
  $publicUrl = Wait-ForTunnelUrl -LogPath $tunnelOutLog

  Write-Host ""
  Write-Host "========================================"
  Write-Host " Dr. Jinu server + HTTPS tunnel running"
  Write-Host "========================================"
  Write-Host (" Local   : http://{0}:{1}" -f $BindHost, $Port)
  if ($lanUrls.Count -gt 0) {
    Write-Host " LAN     :"
    foreach ($url in $lanUrls) {
      Write-Host ("          {0}" -f $url)
    }
  }
  Write-Host (" Public  : {0}" -f $publicUrl)
  Write-Host (" Health  : {0}" -f $healthUrl)
  if ($healthPayload) {
    $serviceStatus = if ($healthPayload.ready) { "READY" } else { "NOT READY" }
    $defaultProvider = $healthPayload.llm_provider.default
    $localReady = $healthPayload.llm_provider.local.ready
    $modelExists = $healthPayload.model.exists
    $googleReady = $healthPayload.google_credentials.configured
    Write-Host (" Status  : {0}" -f $serviceStatus)
    Write-Host (" LLM     : default={0}, local_ready={1}, model_exists={2}" -f $defaultProvider, $localReady, $modelExists)
    Write-Host (" STT     : google_credentials={0}" -f $googleReady)
  }
  Write-Host ""
  Write-Host "Close this window to stop both the server and the tunnel."
  Write-Host "Logs:"
  Write-Host (" - {0}" -f $serverOutLog)
  Write-Host (" - {0}" -f $tunnelOutLog)
  Write-Host ""

  while ($true) {
    if ($serverProcess.HasExited) {
      throw "The app server exited. Check the logs."
    }
    if ($tunnelProcess.HasExited) {
      throw "The Cloudflare Tunnel exited. Check the logs."
    }
    Start-Sleep -Seconds 1
  }
}
finally {
  if ($tunnelProcess -and -not $tunnelProcess.HasExited) {
    try { Stop-Process -Id $tunnelProcess.Id -Force -ErrorAction SilentlyContinue } catch {}
  }

  if ($serverProcess -and -not $serverProcess.HasExited) {
    try { Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue } catch {}
  }

  if ($jobHandle -and $jobHandle -ne [IntPtr]::Zero) {
    [KillOnCloseJob.Native]::CloseHandle($jobHandle) | Out-Null
  }
}
