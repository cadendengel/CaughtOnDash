# Fetch the detection weights (Windows).
#
# PowerShell twin of fetch-weights.sh -- the Windows worker host has no bash.
# Keep the URL and SHA256 in step with that script if either changes.
#
# See fetch-weights.sh for why this exists: detect-2.0.pt is a custom BDD100K
# checkpoint and ultralytics will not download it on demand.
$ErrorActionPreference = 'Stop'

$Url    = 'https://huggingface.co/shravanda/yolo26-bdd100k/resolve/main/yolo26-bdd100k.pt'
$Sha256 = 'CB4868FA302584FEDD95B0F08EF5555AE8F16999F6DCD38C18172F68846FD07E'

$DestDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dest    = Join-Path $DestDir 'detect-2.0.pt'

if (Test-Path $Dest) {
    if ((Get-FileHash $Dest -Algorithm SHA256).Hash -eq $Sha256) {
        Write-Host 'detect-2.0.pt already present and verified.'
        exit 0
    }
}

Write-Host 'Downloading detection weights (~20 MB)...'
$Tmp = "$Dest.partial"
try {
    # Invoke-WebRequest's progress bar makes the download dramatically slower.
    $prev = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $Url -OutFile $Tmp -UseBasicParsing
    $ProgressPreference = $prev

    $actual = (Get-FileHash $Tmp -Algorithm SHA256).Hash
    if ($actual -ne $Sha256) {
        # Write-Host + exit rather than Write-Error: $ErrorActionPreference is
        # 'Stop', so Write-Error would raise a terminating exception and bury
        # this behind a stack trace -- and never reach the exit below.
        [Console]::Error.WriteLine('Checksum mismatch -- refusing to install.')
        [Console]::Error.WriteLine("  expected $Sha256")
        [Console]::Error.WriteLine("  actual   $actual")
        exit 1
    }

    Move-Item -Force $Tmp $Dest
    Write-Host "Installed $Dest"
}
finally {
    if (Test-Path $Tmp) { Remove-Item -Force $Tmp }
}
