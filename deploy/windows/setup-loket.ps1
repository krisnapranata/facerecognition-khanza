<#
  Setup PC loket Windows untuk SIMRS Face Recognition (kamera USB lewat browser).
  Jalankan SEKALI per PC sebagai Administrator (PowerShell):

    powershell -ExecutionPolicy Bypass -File .\setup-loket.ps1 -ServerUrl https://192.168.1.20:8443 -Station loket-1

  Yang dilakukan:
    1. Memasang CA internal (simrs-ca.crt) ke "Trusted Root Certification Authorities" komputer,
       sehingga https://server tidak memunculkan peringatan sertifikat.
    2. Kebijakan Edge & Chrome: izinkan kamera untuk ServerUrl tanpa prompt (VideoCaptureAllowedUrls).
    3. Membuat shortcut desktop "SIMRS Identifikasi Wajah" (mode aplikasi; opsional mode kiosk).
  Tidak ada software tambahan yang dipasang. Tidak mengubah pengaturan lain.
#>
param(
  [Parameter(Mandatory = $true)][string]$ServerUrl,
  [string]$CaFile = (Join-Path $PSScriptRoot "simrs-ca.crt"),
  [string]$Station = "loket-1",
  # Bila nama domain (mis. facerec.rs.local) belum ada di DNS internal: IP server untuk file hosts.
  [string]$HostsIp = "",
  [switch]$Kiosk
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Jalankan PowerShell sebagai Administrator."
}
$uri = [Uri]$ServerUrl
if ($uri.Scheme -ne "https") { throw "ServerUrl harus https:// (kamera USB hanya diizinkan di HTTPS)." }
# Port 443 tidak ditulis (https://facerec.rs.local) agar persis sama dengan origin di browser.
$origin = if ($uri.IsDefaultPort) { "{0}://{1}" -f $uri.Scheme, $uri.Host } else { "{0}://{1}:{2}" -f $uri.Scheme, $uri.Host, $uri.Port }

if ($HostsIp) {
  if ($HostsIp -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { throw "HostsIp harus alamat IPv4." }
  $hostsFile = "$env:SystemRoot\System32\drivers\etc\hosts"
  $entry = "$HostsIp`t$($uri.Host)"
  $lines = Get-Content $hostsFile -ErrorAction SilentlyContinue | Where-Object { $_ -notmatch "\s$([regex]::Escape($uri.Host))\s*$" }
  Write-Host "==> File hosts: $entry"
  Set-Content -Path $hostsFile -Value (@($lines) + $entry) -Encoding ASCII
  ipconfig /flushdns | Out-Null
}
if ($Station -notmatch '^[A-Za-z0-9_.\-]{1,40}$') { throw "Nama stasiun hanya huruf/angka/titik/garis bawah/tanda hubung." }

# 1. CA internal
if (-not (Test-Path $CaFile)) { throw "File CA tidak ditemukan: $CaFile" }
Write-Host "==> Memasang CA internal ke LocalMachine\Root"
Import-Certificate -FilePath $CaFile -CertStoreLocation Cert:\LocalMachine\Root | Out-Null

# 2. Kebijakan kamera untuk Edge & Chrome
function Add-AllowedUrl([string]$keyPath, [string]$url) {
  New-Item -Path $keyPath -Force | Out-Null
  $existing = (Get-ItemProperty -Path $keyPath -ErrorAction SilentlyContinue).PSObject.Properties |
    Where-Object { $_.Name -match '^\d+$' } | ForEach-Object { $_.Value }
  if ($existing -contains $url) { return }
  $next = 1
  while (Get-ItemProperty -Path $keyPath -Name "$next" -ErrorAction SilentlyContinue) { $next++ }
  New-ItemProperty -Path $keyPath -Name "$next" -Value $url -PropertyType String -Force | Out-Null
}
Write-Host "==> Mengizinkan kamera untuk $origin di Edge & Chrome"
Add-AllowedUrl "HKLM:\SOFTWARE\Policies\Microsoft\Edge\VideoCaptureAllowedUrls" $origin
Add-AllowedUrl "HKLM:\SOFTWARE\Policies\Google\Chrome\VideoCaptureAllowedUrls" $origin

# 3. Shortcut desktop
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) {
  $target = "$origin/"
  $edgeArgs = if ($Kiosk) { "--kiosk $target --edge-kiosk-type=fullscreen --no-first-run" } else { "--app=$target --start-maximized" }
  $lnk = Join-Path ([Environment]::GetFolderPath("CommonDesktopDirectory")) "SIMRS Identifikasi Wajah.lnk"
  $shell = New-Object -ComObject WScript.Shell
  $s = $shell.CreateShortcut($lnk)
  $s.TargetPath = $edge
  $s.Arguments = $edgeArgs
  $s.Description = "SIMRS Identifikasi Wajah ($Station)"
  $s.Save()
  Write-Host "==> Shortcut dibuat: $lnk"
} else {
  Write-Host "Edge tidak ditemukan; buka $origin/ secara manual di Chrome."
}

Write-Host ""
Write-Host "Selesai. Tutup lalu buka ulang browser, buka shortcut, login, pilih 'Kamera USB di PC ini',"
Write-Host "isi Nama stasiun = $Station, pilih kamera USB, lalu klik Mulai."
Write-Host "Periksa kebijakan di edge://policy atau chrome://policy (VideoCaptureAllowedUrls)."
