<#
Create the data source NEON_INVOICE_PG for Excel on Windows (free psqlODBC driver, no row limit).

  1. Install psqlODBC x64 once:  https://odbc.postgresql.org/  (psqlodbc_x64.msi; use the x86 one for 32-bit Office)
  2. powershell -ExecutionPolicy Bypass -File odbc\setup_windows.ps1 -Server <host> -Database <db>
     add -SaveLogin to store the user and password in the data source, so Excel refreshes without asking
  3. Open excel\Invoice_Live_Query.xlsx, choose Enable Content, type a filter.

Server and Database default to DATABASE_URL_READONLY / DATABASE_URL in .env.local when that file is present.
The data source is created for the current Windows user only (HKCU). Nothing is written to the workbook.
#>
param(
    [string]$Dsn = "NEON_INVOICE_PG",
    [string]$Server,
    [string]$Database,
    [int]$Port = 5432,
    [string]$User = "invoice_reporting_ro",
    [switch]$SaveLogin,
    [switch]$ForgetLogin
)
$ErrorActionPreference = "Stop"

# server / database from .env.local (the secret part of the URL is not used here)
if (-not $Server -or -not $Database) {
    $envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env.local"
    if (Test-Path $envFile) {
        $text = Get-Content $envFile -Raw
        foreach ($key in @("DATABASE_URL_READONLY", "DATABASE_URL")) {
            if ($text -match "(?m)^\s*$key\s*=\s*['""]?\w+://[^@]*@([^/:?'""]+)(?::\d+)?/([^?'""\s]+)") {
                if (-not $Server) { $Server = $Matches[1] }
                if (-not $Database) { $Database = $Matches[2] }
                break
            }
        }
    }
}
if (-not $Server -or -not $Database) { throw "Pass -Server and -Database (no .env.local with DATABASE_URL was found)." }

# the installed psqlODBC driver that matches this PowerShell (run 64-bit PowerShell for 64-bit Excel)
$driverName = $null
foreach ($name in @("PostgreSQL Unicode(x64)", "PostgreSQL Unicode")) {
    if (Test-Path "HKLM:\SOFTWARE\ODBC\ODBCINST.INI\$name") { $driverName = $name; break }
}
if (-not $driverName) { throw "psqlODBC is not installed. Get psqlodbc_x64.msi from https://odbc.postgresql.org/ and run this again." }
$driverPath = (Get-ItemProperty "HKLM:\SOFTWARE\ODBC\ODBCINST.INI\$driverName").Driver

$key = "HKCU:\Software\ODBC\ODBC.INI\$Dsn"
$old = if (Test-Path $key) { Get-ItemProperty $key } else { $null }
$password = if ($old -and $old.Username -eq $User -and $old.PSObject.Properties["Password"]) { $old.Password } else { "" }
if ($SaveLogin) {
    $typed = Read-Host "Database user [$User]"
    if ($typed) { $User = $typed }
    $secure = Read-Host "Password for $User (not shown)" -AsSecureString
    $password = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}
if ($ForgetLogin) { $password = "" }

New-Item -Path $key -Force | Out-Null
$values = [ordered]@{
    Driver = $driverPath; Description = "Invoice reporting view (free PostgreSQL driver, no row limit)"
    Servername = $Server; Port = "$Port"; Database = $Database; Username = $User; SSLmode = "require"
    Password = $password
}
foreach ($k in $values.Keys) { Set-ItemProperty -Path $key -Name $k -Value $values[$k] }
$list = "HKCU:\Software\ODBC\ODBC.INI\ODBC Data Sources"
if (-not (Test-Path $list)) { New-Item -Path $list -Force | Out-Null }
Set-ItemProperty -Path $list -Name $Dsn -Value $driverName

$stored = if ($password) { "password stored for this Windows user" } else { "no password stored: Excel asks on refresh" }
Write-Host "Data source '$Dsn' -> $Server / $Database as $User, driver '$driverName' ($stored)."
Write-Host "Open excel\Invoice_Live_Query.xlsx and choose Data > Refresh All."
