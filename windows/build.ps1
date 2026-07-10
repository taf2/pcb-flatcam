[CmdletBinding()]
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [switch]$Publish
)

$ErrorActionPreference = "Stop"
$project = Join-Path $PSScriptRoot "PcbCam.Windows\PcbCam.Windows.csproj"

if ($Publish) {
    dotnet publish $project -c $Configuration -r win-x64 --self-contained false -o (Join-Path $PSScriptRoot "..\artifacts\PcbCam")
} else {
    dotnet build $project -c $Configuration
}
