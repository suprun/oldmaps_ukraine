param(
    [string]$PluginDir = "",
    [string]$ZipPath = ""
)

$ErrorActionPreference = "Stop"

function Test-IsInDirectory {
    param(
        [string]$Path,
        [string]$Directory
    )

    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd([char[]]@("\", "/"))
    $fullDirectory = [IO.Path]::GetFullPath($Directory).TrimEnd([char[]]@("\", "/"))

    if ($fullPath.Equals($fullDirectory, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    $directoryPrefix = $fullDirectory + [IO.Path]::DirectorySeparatorChar
    return $fullPath.StartsWith($directoryPrefix, [StringComparison]::OrdinalIgnoreCase)
}

function Test-IsPackageExcluded {
    param(
        [string]$FullName,
        [string]$PluginPath
    )

    if ([IO.Path]::GetExtension($FullName).Equals(".pyc", [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    $relativePath = $FullName.Substring($PluginPath.Length).TrimStart([char[]]@("\", "/"))
    $pathParts = $relativePath -split "[\\/]"
    if ($pathParts -contains "__pycache__") {
        return $true
    }

    $leafName = [IO.Path]::GetFileName($FullName)
    $extension = [IO.Path]::GetExtension($FullName)
    if ($extension -in @(".pyc", ".pyo", ".zip", ".log", ".swp", ".swo")) {
        return $true
    }
    if ($leafName -in @(".DS_Store", "Thumbs.db", "desktop.ini")) {
        return $true
    }
    if ($leafName -eq ".env" -or $leafName.StartsWith(".env.", [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    foreach ($part in $pathParts) {
        if ($part -in @("build", "dist", "node_modules", ".venv", "venv")) {
            return $true
        }
    }

    return $false
}

$scriptDir = $PSScriptRoot
if (-not $scriptDir) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$repoDir = (Resolve-Path -LiteralPath $scriptDir).Path
$workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $repoDir "..")).Path

if ([string]::IsNullOrWhiteSpace($PluginDir)) {
    $PluginDir = Join-Path $workspaceRoot "oldmaps_qgis"
}
elseif (-not [IO.Path]::IsPathRooted($PluginDir)) {
    $PluginDir = Join-Path $repoDir $PluginDir
}

if ([string]::IsNullOrWhiteSpace($ZipPath)) {
    $ZipPath = Join-Path $repoDir "oldmaps_qgis.zip"
}
elseif (-not [IO.Path]::IsPathRooted($ZipPath)) {
    $ZipPath = Join-Path $repoDir $ZipPath
}

$pluginPath = (Resolve-Path -LiteralPath $PluginDir).Path
$zipFullPath = [IO.Path]::GetFullPath($ZipPath)
$zipDir = Split-Path -Parent $zipFullPath

if (-not (Test-Path -LiteralPath $zipDir -PathType Container)) {
    throw "Zip directory does not exist: $zipDir"
}

if (-not (Test-IsInDirectory -Path $pluginPath -Directory $workspaceRoot)) {
    throw "Plugin path is outside workspace: $pluginPath"
}

if ((Split-Path -Leaf $pluginPath) -ne "oldmaps_qgis") {
    throw "Expected plugin directory named oldmaps_qgis: $pluginPath"
}

if (-not (Test-IsInDirectory -Path $zipFullPath -Directory $repoDir)) {
    throw "Zip path is outside repo directory: $zipFullPath"
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$pluginParent = Split-Path -Parent $pluginPath
$basePrefix = $pluginParent.TrimEnd([char[]]@("\", "/")) + [IO.Path]::DirectorySeparatorChar
$files = @(Get-ChildItem -LiteralPath $pluginPath -Recurse -File -Force | Where-Object {
        -not (Test-IsPackageExcluded -FullName $_.FullName -PluginPath $pluginPath)
    })

if ($files.Count -eq 0) {
    throw "No files found to package: $pluginPath"
}

$tempZip = Join-Path $zipDir ([IO.Path]::GetFileNameWithoutExtension($zipFullPath) + "." + [guid]::NewGuid().ToString("N") + ".zip")

try {
    $zip = [IO.Compression.ZipFile]::Open($tempZip, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in $files) {
            $entryName = $file.FullName.Substring($basePrefix.Length)
            $entryName = $entryName.Replace([IO.Path]::DirectorySeparatorChar, [char]"/")
            $entryName = $entryName.Replace([IO.Path]::AltDirectorySeparatorChar, [char]"/")
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $zip,
                $file.FullName,
                $entryName,
                [IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    }
    finally {
        $zip.Dispose()
    }

    Move-Item -LiteralPath $tempZip -Destination $zipFullPath -Force
}
catch {
    if (Test-Path -LiteralPath $tempZip) {
        Remove-Item -LiteralPath $tempZip -Force
    }

    throw
}

$archive = [IO.Compression.ZipFile]::OpenRead($zipFullPath)
try {
    $excludedEntries = @($archive.Entries | Where-Object {
            $_.FullName -like "*__pycache__*" -or $_.FullName -like "*.pyc"
        })

    if ($excludedEntries.Count -gt 0) {
        throw "Archive contains excluded entries."
    }

    Write-Output "Packaged $($files.Count) files to $zipFullPath"
    Write-Output "Entries: $($archive.Entries.Count)"
}
finally {
    $archive.Dispose()
}
