$passwordFile = Join-Path $PSScriptRoot '..\secrets\restic_pass'

$env:RESTIC_PASSWORD = (Get-Content -LiteralPath $passwordFile -Raw).TrimEnd("`r", "`n")
