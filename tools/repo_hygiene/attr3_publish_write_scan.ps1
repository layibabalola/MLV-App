# attr3_publish_write_scan.ps1 -- AST proof that an emitted PLAYBACK-ATTR-3-CUDA job template
# performs NO raw filesystem write outside its job-owned work tree.
#
# WHY AN AST AND NOT A LINE SCAN (sol, PR #133 r5 BLOCKER). A per-line regex that looks for a
# guard "near" a write is not sound: a multi-line command, a destination aliased through another
# variable, a .NET or Out-File write, or a guard mentioned only in a comment all pass it. This
# scanner parses the template with the PowerShell parser and inverts the invariant:
#
#   Every raw write primitive -- a write cmdlet, a file redirection, a static [IO.File]/
#   [IO.Directory] mutator, an instance CopyTo/MoveTo/Delete, or dynamic code -- is a VIOLATION
#   unless its destination is PROVABLY under $Work. Anything that must land elsewhere (outbox
#   artifacts, cache entries) goes through Publish-AttrCudaText / Publish-AttrCudaFileCopy /
#   Publish-AttrCudaFileMove / New-AttrCudaDirectory / Remove-AttrCudaPartialFile, which perform
#   the slot check and the write in ONE call (tools/profiling/bachelor/AttrCudaArtifacts.psm1).
#
# "Provably under $Work":
#   - the expression is $Work itself (the axiom: every template assigns $Work from its fixed job
#     root and clears it with Remove-AttrCudaTree, which refuses reparse points), or
#   - (Join-Path <provable> ...), or [string]<provable>, or
#   - a variable whose EVERY assignment in the template is provable (a foreach variable, a
#     parameter, or a variable with any unprovable assignment is NOT provable).
# The destination must be passed by an explicit, unabbreviated-or-abbreviated NAMED parameter;
# a positional destination is a violation (conservative), as is an alias the table does not know.
#
# Known limit, by design: writes performed by CHILD PROCESSES (MLVApp, PresentMon, the backend
# build scripts) are not visible to a static scan of this script; their output directories are
# $Work-rooted arguments.
#
# Output: JSON { templates: [...], violations: [ { source, line, kind, text, reason } ] }.

[CmdletBinding()]
param(
    [string[]]$GeneratorPath = @(),
    [string[]]$TemplateFile = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# `pwsh -File` cannot pass an array, so ';'-separated lists are accepted too.
$GeneratorPath = @($GeneratorPath | ForEach-Object { $_ -split ';' } | Where-Object { $_ })
$TemplateFile = @($TemplateFile | ForEach-Object { $_ -split ';' } | Where-Object { $_ })

$writeParams = @{
    'set-content'      = @('LiteralPath', 'Path')
    'add-content'      = @('LiteralPath', 'Path')
    'clear-content'    = @('LiteralPath', 'Path')
    'out-file'         = @('FilePath', 'LiteralPath')
    'tee-object'       = @('FilePath', 'LiteralPath')
    'export-csv'       = @('LiteralPath', 'Path')
    'export-clixml'    = @('LiteralPath', 'Path')
    'copy-item'        = @('Destination')
    'move-item'        = @('Destination')
    'rename-item'      = @('LiteralPath', 'Path')
    'new-item'         = @('Path')
    'remove-item'      = @('LiteralPath', 'Path')
    'set-item'         = @('LiteralPath', 'Path')
    'expand-archive'   = @('DestinationPath')
    'compress-archive' = @('DestinationPath')
    'start-process'    = @('RedirectStandardOutput', 'RedirectStandardError')
}
$aliases = @{
    'sc' = 'set-content'; 'ac' = 'add-content'; 'clc' = 'clear-content'; 'tee' = 'tee-object'
    'epcsv' = 'export-csv'; 'cp' = 'copy-item'; 'copy' = 'copy-item'; 'cpi' = 'copy-item'
    'mv' = 'move-item'; 'move' = 'move-item'; 'mi' = 'move-item'; 'ren' = 'rename-item'; 'rni' = 'rename-item'
    'ni' = 'new-item'; 'md' = 'new-item'; 'mkdir' = 'new-item'; 'rm' = 'remove-item'; 'del' = 'remove-item'
    'erase' = 'remove-item'; 'ri' = 'remove-item'; 'rd' = 'remove-item'; 'rmdir' = 'remove-item'
    'si' = 'set-item'; 'saps' = 'start-process'; 'start' = 'start-process'
}
$dynamicCode = @('invoke-expression', 'iex', 'invoke-command', 'icm', 'new-psdrive')
$instanceMutators = @('CopyTo', 'MoveTo', 'Delete', 'Create', 'CreateSubdirectory', 'Open', 'OpenWrite',
    'AppendText', 'CreateText', 'Encrypt', 'Decrypt', 'Refresh')

$violations = [System.Collections.Generic.List[object]]::new()
$scanned = [System.Collections.Generic.List[string]]::new()

function Get-NamedArgument([System.Management.Automation.Language.CommandAst]$Command, [string[]]$Names) {
    $elements = $Command.CommandElements
    for ($i = 1; $i -lt $elements.Count; $i++) {
        $element = $elements[$i]
        if ($element -isnot [System.Management.Automation.Language.CommandParameterAst]) { continue }
        $given = $element.ParameterName
        foreach ($name in $Names) {
            if ($given.Length -ge 2 -and $name.StartsWith($given, [StringComparison]::OrdinalIgnoreCase)) {
                if ($null -ne $element.Argument) { return $element.Argument }
                if ($i + 1 -lt $elements.Count) { return $elements[$i + 1] }
                return $null
            }
        }
    }
    return $null
}

function Test-Provable($Ast, $Root, [System.Collections.Generic.HashSet[string]]$Visiting) {
    if ($null -eq $Ast) { return $false }
    switch ($Ast.GetType().Name) {
        'CommandExpressionAst' { return (Test-Provable $Ast.Expression $Root $Visiting) }
        'ParenExpressionAst' { return (Test-Provable $Ast.Pipeline $Root $Visiting) }
        'ConvertExpressionAst' { return (Test-Provable $Ast.Child $Root $Visiting) }
        'PipelineAst' {
            if ($Ast.PipelineElements.Count -ne 1) { return $false }
            return (Test-Provable $Ast.PipelineElements[0] $Root $Visiting)
        }
        'CommandAst' {
            if ($Ast.GetCommandName() -ine 'Join-Path') { return $false }
            $base = Get-NamedArgument $Ast @('Path')
            if ($null -eq $base) {
                if ($Ast.CommandElements.Count -lt 2) { return $false }
                $base = $Ast.CommandElements[1]
                if ($base -is [System.Management.Automation.Language.CommandParameterAst]) { return $false }
            }
            return (Test-Provable $base $Root $Visiting)
        }
        'VariableExpressionAst' {
            $name = $Ast.VariablePath.UserPath
            if ($name -ieq 'Work') { return $true }
            # $null is a sink, not a file: 2>$null / > $null discard output.
            if ($name -ieq 'null') { return $true }
            if (-not $Visiting.Add($name.ToLowerInvariant())) { return $false }
            try {
                $assignments = @($Root.FindAll({
                    param($n)
                    $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
                    $n.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
                    $n.Left.VariablePath.UserPath -ieq $name
                }, $true))
                if ($assignments.Count -eq 0) { return $false }
                foreach ($assignment in $assignments) {
                    if ($assignment.Operator -ne [System.Management.Automation.Language.TokenKind]::Equals) { return $false }
                    if (-not (Test-Provable $assignment.Right $Root $Visiting)) { return $false }
                }
                return $true
            } finally {
                [void]$Visiting.Remove($name.ToLowerInvariant())
            }
        }
        default { return $false }
    }
}

function Add-Violation([string]$Source, $Ast, [string]$Kind, [string]$Reason) {
    $text = ($Ast.Extent.Text -replace '\s+', ' ')
    if ($text.Length -gt 160) { $text = $text.Substring(0, 160) }
    $violations.Add([ordered]@{ source = $Source; line = $Ast.Extent.StartLineNumber; kind = $Kind; text = $text; reason = $Reason })
}

function Invoke-Scan([string]$Source, [string]$Text) {
    $tokens = $null
    $errors = $null
    $root = [System.Management.Automation.Language.Parser]::ParseInput($Text, [ref]$tokens, [ref]$errors)
    foreach ($err in @($errors)) {
        # The generators substitute placeholders into string literals only, so a template must
        # parse on its own; an unparseable template cannot be proved and is itself a violation.
        $violations.Add([ordered]@{ source = $Source; line = $err.Extent.StartLineNumber; kind = 'parse'; text = $err.Message; reason = 'template does not parse' })
    }
    $scanned.Add($Source)

    foreach ($command in $root.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)) {
        $name = $command.GetCommandName()
        if ([string]::IsNullOrEmpty($name)) { continue }
        $key = $name.ToLowerInvariant()
        if ($dynamicCode -contains $key) { Add-Violation $Source $command 'dynamic' "$name executes code the scan cannot see"; continue }
        if ($aliases.ContainsKey($key)) { $key = $aliases[$key] }
        if (-not $writeParams.ContainsKey($key)) { continue }
        $destination = Get-NamedArgument $command $writeParams[$key]
        if ($key -eq 'start-process') {
            if ($null -eq $destination) { continue }
        } elseif ($null -eq $destination) {
            Add-Violation $Source $command 'write' "$name has no named destination parameter ($($writeParams[$key] -join '/'))"
            continue
        }
        $visiting = [System.Collections.Generic.HashSet[string]]::new()
        if (-not (Test-Provable $destination $root $visiting)) {
            Add-Violation $Source $command 'write' "$name destination is not provably under `$Work; use a Publish-AttrCuda* helper"
        }
    }

    foreach ($redirect in $root.FindAll({ param($n) $n -is [System.Management.Automation.Language.FileRedirectionAst] }, $true)) {
        $visiting = [System.Collections.Generic.HashSet[string]]::new()
        if (-not (Test-Provable $redirect.Location $root $visiting)) {
            Add-Violation $Source $redirect 'redirect' "redirection target is not provably under `$Work"
        }
    }

    foreach ($member in $root.FindAll({ param($n) $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] }, $true)) {
        $memberName = $member.Member.Extent.Text.Trim("'`"")
        if ($member.Static -and $member.Expression -is [System.Management.Automation.Language.TypeExpressionAst]) {
            $typeName = $member.Expression.TypeName.FullName
            if ($typeName -match '^(System\.)?IO\.(File|Directory|FileInfo|DirectoryInfo)$' -and
                $memberName -notmatch '^(Exists|ReadAll|ReadAllText|ReadAllLines|ReadAllBytes|ReadLines|GetAttributes|GetLastWriteTime|GetLastWriteTimeUtc|GetCreationTime|EnumerateFiles|EnumerateDirectories|EnumerateFileSystemEntries|GetFiles|GetDirectories|OpenRead|GetCurrentDirectory)$') {
                Add-Violation $Source $member 'dotnet' "[$typeName]::$memberName mutates the filesystem outside the helpers"
            }
        } elseif (-not $member.Static -and $instanceMutators -contains $memberName) {
            Add-Violation $Source $member 'dotnet' ".$memberName() may mutate the filesystem outside the helpers"
        }
    }
}

foreach ($generator in $GeneratorPath) {
    $text = [IO.File]::ReadAllText($generator)
    $match = [regex]::Match($text, "(?s)\`$template = @'\r?\n(.*?)\r?\n'@")
    if (-not $match.Success) {
        $violations.Add([ordered]@{ source = $generator; line = 0; kind = 'template'; text = ''; reason = 'no $template here-string found' })
        continue
    }
    # The generator substitutes __PLACEHOLDER__ tokens before emitting; some sit outside quotes
    # (e.g. a boolean), so replace them with a neutral variable so the template parses.
    Invoke-Scan $generator ([regex]::Replace($match.Groups[1].Value, '__[A-Z0-9_]+__', '$attrCudaPlaceholder'))
}
foreach ($file in $TemplateFile) {
    Invoke-Scan $file ([IO.File]::ReadAllText($file))
}

[ordered]@{ templates = @($scanned); violations = @($violations) } | ConvertTo-Json -Depth 6
