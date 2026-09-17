# attr3_publish_write_scan.ps1 -- DEFAULT-DENY AST validation of the emitted PLAYBACK-ATTR-3-CUDA job
# templates: they may use only an explicit language subset, and the few filesystem-mutating
# primitives in that subset must target a destination PROVED to lie under the job-owned $Work.
#
# HISTORY (sol, PR #133). r5: a per-line "guard near the write" regex was unsound. r6: a
# blocklist of write primitives was still unsound -- $Work reassignment, Join-Path '..' traversal,
# Set-Variable/scoped mutation, StreamWriter/New-Object, Add-Type, the dynamic call operator and a
# second Start-Process redirect all passed. Enumerating bad shapes cannot be complete, so this
# scanner enumerates the GOOD ones and rejects everything else:
#
#   R1 commands      every command is an allowlisted cmdlet, a function DEFINED in the template, or a
#                    function DEFINED in AttrCudaArtifacts.psm1 (the embedded, slot-checked helpers),
#                    matched with exact case. Set-Content, Copy-Item, Move-Item, Remove-Item, Out-File,
#                    Add-Type, New-Object, Set-Variable, Invoke-Expression, ... are not allowlisted.
#   R2 mutators      of the allowlisted cmdlets only New-Item (-Path), Expand-Archive (-DestinationPath),
#                    Export-Csv (-LiteralPath) and Start-Process (every -Redirect* occurrence) write;
#                    each named destination must be provable. A positional argument on those cmdlets,
#                    and splatting on ANY command, is rejected. New-Item must name -ItemType
#                    'Directory' or 'File' literally (no links). ForEach-Object may take only script
#                    blocks, so no method can be invoked by member NAME.
#                    Not filesystem, accepted: `reg` (HKCU playback settings) and the processes that
#                    Start-Process/`&` launch (see the child-process limit below).
#   R3 dynamic calls `&` is allowed only on a literal ending in .exe or on a variable (or its .FullName)
#                    named in $childExecutables (external processes: the documented limit below);
#                    `.` sourcing, `&` on a script block or on a string naming a cmdlet is rejected.
#   R4 .NET          static members and instance methods must be on the allowlists below; that excludes
#                    every [IO.File]/[IO.Directory] mutator, StreamWriter::new, CopyTo/MoveTo/Delete,
#                    .Invoke(), and reflection.
#   R5 assignments   the left side is a plain unscoped variable, an index into one, `$script:<name>` for
#                    a non-root name, or `$env:TEMP`/`$env:TMP` assigned a provable value. $Work is
#                    assigned EXACTLY ONCE, at top level, in a canonical shape; root variables ($Work,
#                    $Pub, $Cache, $AgentRoot, $Root) are never scoped or compound-assigned.
#   R6 redirections  every file redirection (> >> 2> ...) targets $null or a provable destination.
#   R7 provable      $Work; (Join-Path <provable> <safe literal child>) where the child is a string
#                    constant with no '..', ':', leading separator or wildcard; or an unscoped variable
#                    whose every assignment in the template is a plain '=' of a provable expression.
#
# Known limit, by design: what a CHILD PROCESS writes (MLVApp, PresentMon, nvcc, the backend build
# scripts run by a child pwsh) is outside a static scan of this script; R3 confines which processes
# may be started and R2/R6 confine their redirected output.
#
# Output: JSON { templates: [...], violations: [ { source, line, rule, text, reason } ] }.

[CmdletBinding()]
param(
    [string[]]$GeneratorPath = @(),
    [string[]]$TemplateFile = @(),
    [string]$ModulePath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# `pwsh -File` cannot pass an array, so ';'-separated lists are accepted too.
$GeneratorPath = @($GeneratorPath | ForEach-Object { $_ -split ';' } | Where-Object { $_ })
$TemplateFile = @($TemplateFile | ForEach-Object { $_ -split ';' } | Where-Object { $_ })
if ([string]::IsNullOrWhiteSpace($ModulePath)) {
    $ModulePath = Join-Path $PSScriptRoot '..\profiling\bachelor\AttrCudaArtifacts.psm1'
}

$allowedCmdlets = @(
    'ConvertFrom-Json', 'ConvertTo-Json', 'Expand-Archive', 'Export-Csv', 'ForEach-Object', 'Get-ChildItem',
    'Get-CimInstance', 'Get-Content', 'Get-Date', 'Get-FileHash', 'Get-Process', 'Import-Csv', 'Join-Path',
    'Measure-Object', 'New-Item', 'Out-Null', 'Select-Object', 'Select-String', 'Sort-Object', 'Split-Path',
    'Start-Process', 'Start-Sleep', 'Test-Path', 'Where-Object', 'Write-Output', 'reg'
)
$mutatorParams = @{
    'new-item'       = @('Path')
    'expand-archive' = @('DestinationPath')
    'export-csv'     = @('LiteralPath', 'Path')
    'start-process'  = @('RedirectStandardOutput', 'RedirectStandardError')
}
$childExecutables = @('vsLocator', 'psExe', 'cuobjdump', 'exportTool', 'nvcc')
$allowedStatic = @(
    'double::Parse', 'double::TryParse', 'Environment::GetEnvironmentVariable',
    'Globalization.CultureInfo::InvariantCulture', 'Globalization.NumberStyles::Float',
    'IO.File::ReadAllText', 'IO.Path::GetFileNameWithoutExtension', 'IO.Path::GetFullPath',
    'math::Ceiling', 'math::Floor', 'math::Pow', 'math::Sqrt', 'regex::Escape', 'regex::Matches',
    'string::IsNullOrWhiteSpace', 'StringComparison::OrdinalIgnoreCase',
    'System.Collections.Generic.List[object]::new', 'System.Collections.Specialized.OrderedDictionary::new'
)
$allowedInstance = @('Add', 'Contains', 'ContainsKey', 'Kill', 'Replace', 'StartsWith', 'Substring',
    'ToLowerInvariant', 'ToString', 'ToUniversalTime', 'ToUpperInvariant', 'Trim', 'WaitForExit')
$rootVariables = @('Work', 'Pub', 'Cache', 'AgentRoot', 'Root')
$canonicalWork = @("Join-Path `$AgentRoot `"work\`$JobId`"", "Join-Path 'C:\mlvtmp' `$JobId")
$allowedEnv = @('TEMP', 'TMP')

$violations = [System.Collections.Generic.List[object]]::new()
$scanned = [System.Collections.Generic.List[string]]::new()

function Add-Violation([string]$Source, $Ast, [string]$Rule, [string]$Reason) {
    $text = ($Ast.Extent.Text -replace '\s+', ' ')
    if ($text.Length -gt 160) { $text = $text.Substring(0, 160) }
    $violations.Add([ordered]@{ source = $Source; line = $Ast.Extent.StartLineNumber; rule = $Rule; text = $text; reason = $Reason })
}

function Get-ModuleFunctionNames([string]$Path) {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path -LiteralPath $Path).Path, [ref]$tokens, [ref]$errors)
    @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false) | ForEach-Object { $_.Name })
}

function Get-VariableName($VariableAst) {
    # UserPath carries any scope qualifier ("script:x", "env:TEMP"); split it off.
    $user = $VariableAst.VariablePath.UserPath
    $index = $user.IndexOf(':')
    if ($index -ge 0) { return [pscustomobject]@{ scope = $user.Substring(0, $index); name = $user.Substring($index + 1) } }
    return [pscustomobject]@{ scope = ''; name = $user }
}

function Get-NamedArguments($Command, [string[]]$Names) {
    $found = [System.Collections.Generic.List[object]]::new()
    $elements = $Command.CommandElements
    for ($i = 1; $i -lt $elements.Count; $i++) {
        $element = $elements[$i]
        if ($element -isnot [System.Management.Automation.Language.CommandParameterAst]) { continue }
        foreach ($name in $Names) {
            if ($element.ParameterName.Length -ge 2 -and $name.StartsWith($element.ParameterName, [StringComparison]::OrdinalIgnoreCase)) {
                if ($null -ne $element.Argument) { $found.Add($element.Argument) }
                elseif ($i + 1 -lt $elements.Count -and $elements[$i + 1] -isnot [System.Management.Automation.Language.CommandParameterAst]) { $found.Add($elements[$i + 1]) }
                else { $found.Add($null) }
            }
        }
    }
    return , $found
}

function Get-PositionalCount($Command) {
    # Elements that are neither a parameter nor the value consumed by the preceding parameter. Switch
    # parameters (-Force, -NoTypeInformation) consume nothing, so a value following one is counted
    # as positional -- conservative for the mutators, which never need a positional argument.
    $switchNames = @('Force', 'NoTypeInformation', 'PassThru', 'Wait', 'NoNewWindow', 'Recurse', 'Confirm', 'WhatIf', 'Append', 'NoClobber')
    $elements = $Command.CommandElements
    $count = 0
    for ($i = 1; $i -lt $elements.Count; $i++) {
        $element = $elements[$i]
        if ($element -is [System.Management.Automation.Language.CommandParameterAst]) {
            $isSwitch = @($switchNames | Where-Object { $_.StartsWith($element.ParameterName, [StringComparison]::OrdinalIgnoreCase) }).Count -gt 0
            if ($null -eq $element.Argument -and -not $isSwitch -and $i + 1 -lt $elements.Count -and $elements[$i + 1] -isnot [System.Management.Automation.Language.CommandParameterAst]) { $i++ }
            continue
        }
        $count++
    }
    return $count
}

function Test-SafeChild($Ast) {
    if ($Ast -is [System.Management.Automation.Language.CommandExpressionAst]) { $Ast = $Ast.Expression }
    if ($Ast -isnot [System.Management.Automation.Language.StringConstantExpressionAst]) { return $false }
    $value = [string]$Ast.Value
    if ([string]::IsNullOrWhiteSpace($value)) { return $false }
    if ($value -match '\.\.|:|^[\\/]|[*?\[\]]') { return $false }
    return $true
}

function Get-AssignmentsTo($Root, [string]$Name) {
    @($Root.FindAll({
        param($n)
        if ($n -isnot [System.Management.Automation.Language.AssignmentStatementAst]) { return $false }
        $left = $n.Left
        if ($left -is [System.Management.Automation.Language.ConvertExpressionAst]) { $left = $left.Child }
        ($left -is [System.Management.Automation.Language.VariableExpressionAst]) -and ((Get-VariableName $left).name -ieq $Name)
    }, $true))
}

function Test-Provable($Ast, $Root, $Visiting) {
    if ($null -eq $Ast) { return $false }
    if ($Ast -is [System.Management.Automation.Language.CommandExpressionAst]) { return (Test-Provable $Ast.Expression $Root $Visiting) }
    if ($Ast -is [System.Management.Automation.Language.ParenExpressionAst]) { return (Test-Provable $Ast.Pipeline $Root $Visiting) }
    if ($Ast -is [System.Management.Automation.Language.PipelineAst]) {
        if ($Ast.PipelineElements.Count -ne 1) { return $false }
        return (Test-Provable $Ast.PipelineElements[0] $Root $Visiting)
    }
    if ($Ast -is [System.Management.Automation.Language.CommandAst]) {
        if ($Ast.GetCommandName() -cne 'Join-Path' -or $Ast.InvocationOperator -ne 'Unknown') { return $false }
        $elements = @($Ast.CommandElements | Select-Object -Skip 1)
        # Exactly `Join-Path <base> <child>` (positional), nothing else.
        if ($elements.Count -ne 2) { return $false }
        if (@($elements | Where-Object { $_ -is [System.Management.Automation.Language.CommandParameterAst] }).Count -gt 0) { return $false }
        if (-not (Test-SafeChild $elements[1])) { return $false }
        return (Test-Provable $elements[0] $Root $Visiting)
    }
    if ($Ast -is [System.Management.Automation.Language.VariableExpressionAst]) {
        if ($Ast.Splatted) { return $false }
        $v = Get-VariableName $Ast
        if ($v.scope -ne '') { return $false }
        if ($v.name -eq 'null') { return $true }
        if ($v.name -ceq 'Work') { return $true }   # R5 separately proves $Work's single canonical assignment
        if (-not $Visiting.Add($v.name.ToLowerInvariant())) { return $false }
        try {
            $assignments = @(Get-AssignmentsTo $Root $v.name)
            if ($assignments.Count -eq 0) { return $false }
            foreach ($a in $assignments) {
                if ($a.Operator -ne [System.Management.Automation.Language.TokenKind]::Equals) { return $false }
                if ($a.Left -isnot [System.Management.Automation.Language.VariableExpressionAst]) { return $false }
                if ((Get-VariableName $a.Left).scope -ne '') { return $false }
                if (-not (Test-Provable $a.Right $Root $Visiting)) { return $false }
            }
            return $true
        } finally {
            [void]$Visiting.Remove($v.name.ToLowerInvariant())
        }
    }
    return $false
}

function Invoke-Scan([string]$Source, [string]$Text, [string[]]$ModuleFunctions) {
    $tokens = $null; $errors = $null
    $root = [System.Management.Automation.Language.Parser]::ParseInput($Text, [ref]$tokens, [ref]$errors)
    foreach ($err in @($errors)) {
        $violations.Add([ordered]@{ source = $Source; line = $err.Extent.StartLineNumber; rule = 'parse'; text = $err.Message; reason = 'template does not parse' })
    }
    $scanned.Add($Source)
    $templateFunctions = @($root.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | ForEach-Object { $_.Name })
    $known = @($allowedCmdlets + $templateFunctions + $ModuleFunctions)

    # R1 / R2 / R3
    foreach ($command in $root.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)) {
        foreach ($element in $command.CommandElements) {
            if ($element -is [System.Management.Automation.Language.VariableExpressionAst] -and $element.Splatted) {
                Add-Violation $Source $command 'R2' 'splatting hides parameter names'
            }
        }
        $first = $command.CommandElements[0]
        if ($command.InvocationOperator -eq 'Dot') { Add-Violation $Source $command 'R3' 'dot-sourcing is not allowed'; continue }
        if ($command.InvocationOperator -eq 'Ampersand') {
            $ok = $false
            if ($first -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
                $ok = ([string]$first.Value) -match '\.exe$'
            } elseif ($first -is [System.Management.Automation.Language.ExpandableStringExpressionAst]) {
                $ok = ([string]$first.Value) -match '\.exe$'
            } elseif ($first -is [System.Management.Automation.Language.VariableExpressionAst]) {
                $v = Get-VariableName $first
                $ok = ($v.scope -eq '' -and $childExecutables -ccontains $v.name)
            } elseif ($first -is [System.Management.Automation.Language.MemberExpressionAst] -and -not $first.Static -and
                      $first.Expression -is [System.Management.Automation.Language.VariableExpressionAst]) {
                $v = Get-VariableName $first.Expression
                $ok = ($v.scope -eq '' -and $childExecutables -ccontains $v.name -and $first.Member.Extent.Text -ceq 'FullName')
            }
            if (-not $ok) { Add-Violation $Source $command 'R3' 'call operator on something other than an allowlisted child executable' }
            continue
        }
        $name = $command.GetCommandName()
        if ([string]::IsNullOrEmpty($name) -or $first -isnot [System.Management.Automation.Language.StringConstantExpressionAst] -or
            $first.StringConstantType -ne 'BareWord') {
            Add-Violation $Source $command 'R1' 'command name is not a bare literal'
            continue
        }
        if ($known -cnotcontains $name) {
            Add-Violation $Source $command 'R1' "'$name' is not an allowlisted cmdlet, template function or module function (exact case)"
            continue
        }
        $key = $name.ToLowerInvariant()
        if ($key -eq 'foreach-object') {
            # `ForEach-Object Delete` / `-MemberName Delete` invokes a method by NAME, invisible to R4.
            $arguments = @($command.CommandElements | Select-Object -Skip 1)
            $onlyBlocks = @($arguments | Where-Object {
                -not ($_ -is [System.Management.Automation.Language.ScriptBlockExpressionAst])
            }).Count -eq 0
            if (-not $onlyBlocks) {
                Add-Violation $Source $command 'R2' 'ForEach-Object may only take script blocks (no -MemberName, no member-name string)'
            }
        }
        if ($key -eq 'new-item') {
            # Only a plain directory or file; a SymbolicLink/Junction/HardLink under $Work would let a
            # later provable write land outside it.
            $types = Get-NamedArguments $command @('ItemType')
            $typeOk = $types.Count -eq 1 -and $null -ne $types[0] -and
                ($types[0] -is [System.Management.Automation.Language.StringConstantExpressionAst]) -and
                (@('Directory', 'File') -ccontains [string]$types[0].Value)
            if (-not $typeOk) {
                Add-Violation $Source $command 'R2' "New-Item must name -ItemType 'Directory' or 'File' literally"
            }
        }
        if ($mutatorParams.ContainsKey($key)) {
            $destinations = Get-NamedArguments $command $mutatorParams[$key]
            if ($key -ne 'start-process') {
                if ((Get-PositionalCount $command) -gt 0) {
                    Add-Violation $Source $command 'R2' "$name has a positional argument; its destination must be named"
                }
                if ($destinations.Count -eq 0) {
                    Add-Violation $Source $command 'R2' "$name has no named destination"
                }
            }
            foreach ($destination in $destinations) {
                $visiting = [System.Collections.Generic.HashSet[string]]::new()
                if (-not (Test-Provable $destination $root $visiting)) {
                    Add-Violation $Source $command 'R2' "$name destination is not provably under `$Work"
                }
            }
        }
    }

    # R4 .NET members
    foreach ($member in $root.FindAll({ param($n) $n -is [System.Management.Automation.Language.MemberExpressionAst] }, $true)) {
        $memberName = $member.Member.Extent.Text
        if ($member.Static) {
            $typeText = if ($member.Expression -is [System.Management.Automation.Language.TypeExpressionAst]) {
                $member.Expression.TypeName.FullName
            } else {
                '<non-type-expression>'
            }
            if ($allowedStatic -cnotcontains "${typeText}::$memberName") {
                Add-Violation $Source $member 'R4' "static member [$typeText]::$memberName is not allowlisted"
            }
        } elseif ($member -is [System.Management.Automation.Language.InvokeMemberExpressionAst]) {
            if ($member.Member -isnot [System.Management.Automation.Language.StringConstantExpressionAst] -or $allowedInstance -cnotcontains $memberName) {
                Add-Violation $Source $member 'R4' "instance method .$memberName() is not allowlisted"
            }
        }
    }

    # R5 assignments
    $workAssignments = @(Get-AssignmentsTo $root 'Work')
    if ($workAssignments.Count -ne 1) {
        $anchor = if ($workAssignments.Count -gt 1) { $workAssignments[1] } else { $root }
        Add-Violation $Source $anchor 'R5' "`$Work must be assigned exactly once (found $($workAssignments.Count))"
    } else {
        $a = $workAssignments[0]
        $rhs = ($a.Right.Extent.Text -replace '\s+', ' ').Trim()
        if ($a.Operator -ne [System.Management.Automation.Language.TokenKind]::Equals -or
            $a.Left -isnot [System.Management.Automation.Language.VariableExpressionAst] -or
            (Get-VariableName $a.Left).scope -ne '' -or
            (Get-VariableName $a.Left).name -cne 'Work' -or
            $canonicalWork -cnotcontains $rhs -or
            $a.Parent.Parent -ne $root) {
            Add-Violation $Source $a 'R5' '$Work is not assigned once, at top level, in a canonical shape'
        }
    }
    foreach ($a in $root.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true)) {
        $left = $a.Left
        if ($left -is [System.Management.Automation.Language.ConvertExpressionAst]) { $left = $left.Child }
        if ($left -is [System.Management.Automation.Language.IndexExpressionAst]) {
            if ($left.Target -isnot [System.Management.Automation.Language.VariableExpressionAst] -or (Get-VariableName $left.Target).scope -ne '' -or
                $rootVariables -contains (Get-VariableName $left.Target).name) {
                Add-Violation $Source $a 'R5' 'index assignment target must be a plain unscoped non-root variable'
            }
            continue
        }
        if ($left -isnot [System.Management.Automation.Language.VariableExpressionAst]) {
            Add-Violation $Source $a 'R5' "assignment to $($left.GetType().Name) is not allowed"
            continue
        }
        $v = Get-VariableName $left
        if ($v.scope -eq '') {
            if ($rootVariables -contains $v.name -and $a.Operator -ne [System.Management.Automation.Language.TokenKind]::Equals) {
                Add-Violation $Source $a 'R5' "compound assignment to root variable `$$($v.name)"
            }
            continue
        }
        if ($v.scope -ieq 'script' -and $rootVariables -notcontains $v.name) { continue }
        if ($v.scope -ieq 'env' -and $allowedEnv -contains $v.name) {
            $visiting = [System.Collections.Generic.HashSet[string]]::new()
            if ($a.Operator -ne [System.Management.Automation.Language.TokenKind]::Equals -or -not (Test-Provable $a.Right $root $visiting)) {
                Add-Violation $Source $a 'R5' "`$env:$($v.name) must be assigned a provable path under `$Work"
            }
            continue
        }
        Add-Violation $Source $a 'R5' "scoped assignment `$$($v.scope):$($v.name) is not allowed"
    }

    # R6 redirections
    foreach ($redirect in $root.FindAll({ param($n) $n -is [System.Management.Automation.Language.FileRedirectionAst] }, $true)) {
        $visiting = [System.Collections.Generic.HashSet[string]]::new()
        if (-not (Test-Provable $redirect.Location $root $visiting)) {
            Add-Violation $Source $redirect 'R6' 'redirection target is not $null or provably under $Work'
        }
    }
}

$moduleFunctions = @(Get-ModuleFunctionNames $ModulePath)

foreach ($generator in $GeneratorPath) {
    $text = [IO.File]::ReadAllText($generator)
    $match = [regex]::Match($text, "(?s)\`$template = @'\r?\n(.*?)\r?\n'@")
    if (-not $match.Success) {
        $violations.Add([ordered]@{ source = $generator; line = 0; rule = 'template'; text = ''; reason = 'no $template here-string found' })
        continue
    }
    # Placeholders (__NAME__) are substituted by the generator, some outside quotes; a neutral
    # variable keeps the template parseable without granting it any proof.
    Invoke-Scan $generator ([regex]::Replace($match.Groups[1].Value, '__[A-Z0-9_]+__', '$attrCudaPlaceholder')) $moduleFunctions
}
foreach ($file in $TemplateFile) {
    Invoke-Scan $file ([IO.File]::ReadAllText($file)) $moduleFunctions
}

[ordered]@{ templates = @($scanned); violations = @($violations) } | ConvertTo-Json -Depth 6
