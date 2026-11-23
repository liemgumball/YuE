# PreToolUse guard: only allow `git push` to origin (liemgumball/YuE) and
# `gh pr create` that targets liemgumball/YuE. Everything else passes through.
$ErrorActionPreference = 'Stop'
$Fork = 'liemgumball/YuE'

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$cmd = [string]$payload.tool_input.command
if (-not $cmd) { exit 0 }

function Deny([string]$reason) {
    @{ hookSpecificOutput = @{
        hookEventName = 'PreToolUse'
        permissionDecision = 'deny'
        permissionDecisionReason = $reason
    } } | ConvertTo-Json -Compress -Depth 4
    exit 0
}

# Split chained commands so each git/gh invocation is checked on its own.
foreach ($seg in ($cmd -split '\|\||&&|;|\||\r?\n')) {
    $s = $seg.Trim()

    if ($s -match '\bgit(\.exe)?\b.*\bpush\b') {
        $args_ = ($s -replace '^.*?\bpush\b', '').Trim() -split '\s+' | Where-Object { $_ -and -not $_.StartsWith('-') }
        $remote = $args_ | Select-Object -First 1
        if ($remote -ne 'origin') {
            if ($remote -match '(?i)github\.com[:/]liemgumball/YuE(\.git)?$') { continue }
            Deny "Blocked by fork guard: push only to 'origin' ($Fork), written explicitly (e.g. git push origin <branch>). Got remote: '$remote'."
        }
    }

    if ($s -match '\bgh(\.exe)?\b.*\bpr\s+create\b') {
        if ($s -match "(?i)(--repo[=\s]+|-R\s+)(https://github\.com/)?$([regex]::Escape($Fork))\b") { continue }
        Deny "Blocked by fork guard: gh pr create must target the fork. Add --repo $Fork."
    }
}
exit 0
