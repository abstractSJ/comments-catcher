#requires -Version 5.1

<#
.SYNOPSIS
    Install the canonical skill folder for Codex, Claude Code, or both.

.DESCRIPTION
    The installer is local-only. It copies skills/comments-catcher and never
    downloads or executes remote content. Auto mode prefers a shared skill root
    and falls back to both native roots when they are separate.
#>

[CmdletBinding()]
param(
    [ValidateSet("User", "Project")]
    [string]$Scope = "User",

    [string]$ProjectPath = (Get-Location).Path,

    [ValidateSet("Auto", "Codex", "Claude", "Both")]
    [string]$Target = "Auto",

    [string]$TargetDir,

    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-CanonicalSkillFolder {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SkillPath
    )

    if (-not (Test-Path -LiteralPath $SkillPath -PathType Container)) {
        throw "Canonical skill folder does not exist: $SkillPath"
    }

    $requiredFiles = @(
        "SKILL.md",
        "agents/openai.yaml",
        "scripts/comments_catcher.py",
        "references/setup.md",
        "references/cli-reference.md",
        "references/architecture.md",
        "references/safety-privacy.md",
        "references/troubleshooting.md",
        "references/output-schema-v1.json",
        "references/output-schema-v2.json"
    )

    foreach ($relativePath in $requiredFiles) {
        $candidate = Join-Path -Path $SkillPath -ChildPath $relativePath
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "Incomplete skill package; missing: $candidate"
        }
    }
}

function Remove-PythonCaches {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath
    )

    $cacheDirectories = @(Get-ChildItem -LiteralPath $RootPath -Directory -Force -Recurse | Where-Object {
        $_.Name -eq "__pycache__"
    })
    foreach ($cacheDirectory in $cacheDirectories) {
        Remove-Item -LiteralPath $cacheDirectory.FullName -Recurse -Force
    }

    $bytecodeFiles = @(Get-ChildItem -LiteralPath $RootPath -File -Force -Recurse | Where-Object {
        $_.Extension -in @(".pyc", ".pyo")
    })
    foreach ($bytecodeFile in $bytecodeFiles) {
        Remove-Item -LiteralPath $bytecodeFile.FullName -Force
    }
}

function Get-UserHome {
    $userHome = [Environment]::GetFolderPath("UserProfile")
    if ([string]::IsNullOrWhiteSpace($userHome)) {
        $userHome = $env:USERPROFILE
    }
    if ([string]::IsNullOrWhiteSpace($userHome)) {
        throw "Unable to determine the current user home directory."
    }
    return [System.IO.Path]::GetFullPath($userHome)
}

function Get-CanonicalPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    if (Test-Path -LiteralPath $fullPath -PathType Container) {
        $item = Get-Item -LiteralPath $fullPath -Force
        $resolvedProperty = $item.PSObject.Properties["ResolvedTarget"]
        if ($null -ne $resolvedProperty -and -not [string]::IsNullOrWhiteSpace([string]$resolvedProperty.Value)) {
            return [System.IO.Path]::GetFullPath([string]$resolvedProperty.Value).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
        }
        $targetProperty = $item.PSObject.Properties["Target"]
        if ($null -ne $targetProperty) {
            $targetValue = @($targetProperty.Value) | Select-Object -First 1
            if (-not [string]::IsNullOrWhiteSpace([string]$targetValue)) {
                return [System.IO.Path]::GetFullPath([string]$targetValue).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
            }
        }
    }
    return $fullPath
}

$script:InstallRoots = New-Object 'System.Collections.Generic.List[string]'

function Add-InstallRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate
    )

    $fullCandidate = [System.IO.Path]::GetFullPath($Candidate)
    $canonicalCandidate = Get-CanonicalPath -Path $fullCandidate
    foreach ($existing in $script:InstallRoots) {
        if ((Get-CanonicalPath -Path $existing) -ieq $canonicalCandidate) {
            return
        }
    }
    $script:InstallRoots.Add($fullCandidate)
}

function Get-InstallRoots {
    $script:InstallRoots.Clear()

    if (-not [string]::IsNullOrWhiteSpace($TargetDir)) {
        Add-InstallRoot -Candidate $TargetDir
        return @($script:InstallRoots)
    }

    if ($Scope -eq "User") {
        $basePath = Get-UserHome
        if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
            $codexRoot = Join-Path -Path ([System.IO.Path]::GetFullPath($env:CODEX_HOME)) -ChildPath "skills"
        }
        else {
            $codexRoot = Join-Path -Path $basePath -ChildPath ".agents/skills"
        }
    }
    else {
        if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
            throw "Project path does not exist: $ProjectPath"
        }
        $basePath = (Resolve-Path -LiteralPath $ProjectPath).Path
        $codexRoot = Join-Path -Path $basePath -ChildPath ".agents/skills"
    }

    $claudeRoot = Join-Path -Path $basePath -ChildPath ".claude/skills"

    switch ($Target) {
        "Codex" { Add-InstallRoot -Candidate $codexRoot }
        "Claude" { Add-InstallRoot -Candidate $claudeRoot }
        "Both" {
            Add-InstallRoot -Candidate $codexRoot
            Add-InstallRoot -Candidate $claudeRoot
        }
        default {
            Add-InstallRoot -Candidate $codexRoot
            Add-InstallRoot -Candidate $claudeRoot
        }
    }

    return @($script:InstallRoots)
}

$repositoryRoot = $PSScriptRoot
$sourceSkill = Join-Path -Path $repositoryRoot -ChildPath "skills/comments-catcher"
Assert-CanonicalSkillFolder -SkillPath $sourceSkill

$installRoots = @(Get-InstallRoots)
if ($installRoots.Count -eq 0) {
    throw "No installation target was selected."
}

$destinations = @($installRoots | ForEach-Object {
    Join-Path -Path $_ -ChildPath "comments-catcher"
})

foreach ($destination in $destinations) {
    if ((Test-Path -LiteralPath $destination) -and (-not $Force)) {
        throw "Target already exists: $destination. Re-run with -Force to replace it."
    }
}

foreach ($destination in $destinations) {
    $destinationParent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null

    $temporarySkill = Join-Path -Path $destinationParent -ChildPath (
        ".comments-catcher.install.{0}" -f [Guid]::NewGuid().ToString("N")
    )

    try {
        Copy-Item -LiteralPath $sourceSkill -Destination $temporarySkill -Recurse -Force
        Remove-PythonCaches -RootPath $temporarySkill
        Assert-CanonicalSkillFolder -SkillPath $temporarySkill

        if (Test-Path -LiteralPath $destination) {
            Remove-Item -LiteralPath $destination -Recurse -Force
        }

        Move-Item -LiteralPath $temporarySkill -Destination $destination
        Write-Host "Installed comments-catcher to: $destination"
    }
    finally {
        if (Test-Path -LiteralPath $temporarySkill) {
            Remove-Item -LiteralPath $temporarySkill -Recurse -Force
        }
    }
}

Write-Host "Installation completed. No remote content was downloaded or executed."
