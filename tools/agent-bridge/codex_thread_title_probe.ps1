param(
    [string]$ProcessName = "Codex",
    [int]$MaxTitleLength = 160
)

$ErrorActionPreference = "Stop"

function ConvertTo-BridgeTimestamp {
    return (Get-Date).ToUniversalTime().ToString("s") + "+00:00"
}

function ConvertTo-CleanCodexTitle {
    param([string]$Title)
    $text = ([string]$Title).Replace("`r", " ").Replace("`n", " ").Trim()
    $text = [regex]::Replace($text, "\s+", " ")
    if ([string]::IsNullOrWhiteSpace($text)) {
        return ""
    }
    # Codex sidebar accessibility names sometimes append relative ages without
    # a separator, for example "Agent Bridge18h".
    $text = [regex]::Replace($text, "(?<=\S)(?:\d+\s*(?:s|m|h|d|w)|\d+\s*(?:mo|y))$", "").Trim()
    return $text
}

function Test-CodexTitleCandidate {
    param(
        [string]$Title,
        [int]$Limit
    )
    $text = ConvertTo-CleanCodexTitle -Title $Title
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $false
    }
    if ($text.Length -gt $Limit) {
        return $false
    }
    $blocked = @(
        "Archive chat",
        "Archive chat Pin chat",
        "Automations",
        "Back",
        "Chats",
        "Close",
        "Collapse all",
        "Codex",
        "Edit",
        "File",
        "Filter sidebar chats",
        "Forward",
        "Help",
        "Hide sidebar",
        "Maximize",
        "Minimize",
        "New chat",
        "Plugins",
        "Projects",
        "Search",
        "Show less",
        "Show more",
        "Update",
        "View",
        "Window"
    )
    return $blocked -notcontains $text
}

function Get-CodexSelectedSidebarTitle {
    param(
        [System.Windows.Automation.AutomationElement]$Root,
        [int]$Limit
    )
    $rootRect = $Root.Current.BoundingRectangle
    $all = $Root.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    for ($i = 0; $i -lt $all.Count; $i++) {
        $element = $all.Item($i)
        $controlType = $element.Current.ControlType.ProgrammaticName -replace "^ControlType\.", ""
        if ($controlType -ne "ListItem") {
            continue
        }
        $name = ConvertTo-CleanCodexTitle -Title ([string]$element.Current.Name)
        if (-not (Test-CodexTitleCandidate -Title $name -Limit $Limit)) {
            continue
        }
        $className = [string]$element.Current.ClassName
        if ($className -notlike "*after:block*") {
            continue
        }
        $rect = $element.Current.BoundingRectangle
        if ($rect.Width -le 0 -or $rect.Height -le 0) {
            continue
        }
        if ($rect.X -lt ($rootRect.X - 4) -or $rect.X -gt ($rootRect.X + $rootRect.Width)) {
            continue
        }
        if ($rect.Y -lt ($rootRect.Y - 4) -or $rect.Y -gt ($rootRect.Y + $rootRect.Height)) {
            continue
        }
        $children = $element.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition
        )
        for ($j = 0; $j -lt $children.Count; $j++) {
            $child = $children.Item($j)
            $childType = $child.Current.ControlType.ProgrammaticName -replace "^ControlType\.", ""
            if ($childType -ne "Button") {
                continue
            }
            $childClass = [string]$child.Current.ClassName
            if ($childClass -match "(^|\s)bg-token-list-hover-background(\s|$)") {
                return $name
            }
        }
    }
    return ""
}

function Get-CodexVisibleSidebarTitles {
    param(
        [System.Windows.Automation.AutomationElement]$Root,
        [int]$Limit
    )
    $rootRect = $Root.Current.BoundingRectangle
    $all = $Root.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    $rows = New-Object System.Collections.Generic.List[object]
    $seen = @{}
    for ($i = 0; $i -lt $all.Count; $i++) {
        $element = $all.Item($i)
        $controlType = $element.Current.ControlType.ProgrammaticName -replace "^ControlType\.", ""
        if ($controlType -ne "ListItem") {
            continue
        }
        $className = [string]$element.Current.ClassName
        if ($className -notlike "*after:block*") {
            continue
        }
        $rect = $element.Current.BoundingRectangle
        if ($rect.Width -le 0 -or $rect.Height -le 0) {
            continue
        }
        if ($rect.X -lt ($rootRect.X - 4) -or $rect.X -gt ($rootRect.X + $rootRect.Width)) {
            continue
        }
        if ($rect.Y -lt ($rootRect.Y - 4) -or $rect.Y -gt ($rootRect.Y + $rootRect.Height)) {
            continue
        }
        $rawName = ([string]$element.Current.Name).Replace("`r", " ").Replace("`n", " ").Trim()
        $name = ConvertTo-CleanCodexTitle -Title $rawName
        if (-not (Test-CodexTitleCandidate -Title $name -Limit $Limit)) {
            continue
        }
        $key = $name.ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            continue
        }
        $selected = $false
        $children = $element.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition
        )
        for ($j = 0; $j -lt $children.Count; $j++) {
            $child = $children.Item($j)
            $childType = $child.Current.ControlType.ProgrammaticName -replace "^ControlType\.", ""
            if ($childType -ne "Button") {
                continue
            }
            $childClass = [string]$child.Current.ClassName
            if ($childClass -match "(^|\s)bg-token-list-hover-background(\s|$)") {
                $selected = $true
                break
            }
        }
        $seen[$key] = $true
        $rows.Add([pscustomobject]@{
            title = $name
            raw_name = $rawName
            selected = [bool]$selected
            x = [int]$rect.X
            y = [int]$rect.Y
        }) | Out-Null
    }
    return @($rows | Sort-Object y, x)
}

function Get-CodexTopHeaderTitle {
    param(
        [System.Windows.Automation.AutomationElement]$Root,
        [int]$Limit
    )
    $rootRect = $Root.Current.BoundingRectangle
    $all = $Root.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    for ($i = 0; $i -lt $all.Count; $i++) {
        $element = $all.Item($i)
        $controlType = $element.Current.ControlType.ProgrammaticName -replace "^ControlType\.", ""
        if ($controlType -ne "Text") {
            continue
        }
        $rect = $element.Current.BoundingRectangle
        if ($rect.Width -le 0 -or $rect.Height -le 0) {
            continue
        }
        if ($rect.Y -lt $rootRect.Y -or $rect.Y -gt ($rootRect.Y + 260)) {
            continue
        }
        $name = ConvertTo-CleanCodexTitle -Title ([string]$element.Current.Name)
        if (Test-CodexTitleCandidate -Title $name -Limit $Limit) {
            return $name
        }
    }
    return ""
}

$payload = [ordered]@{
    ok = $false
    observed_at = ConvertTo-BridgeTimestamp
    process_name = $ProcessName
}

try {
    Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
    Add-Type -AssemblyName UIAutomationTypes -ErrorAction Stop

    $process = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } |
        Select-Object -First 1
    if ($null -eq $process) {
        $payload.error = "process_not_found"
        $payload | ConvertTo-Json -Compress -Depth 6
        exit 0
    }

    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$process.MainWindowHandle)
    if ($null -eq $root) {
        $payload.error = "uia_root_not_found"
        $payload.pid = $process.Id
        $payload.window_title = [string]$process.MainWindowTitle
        $payload | ConvertTo-Json -Compress -Depth 6
        exit 0
    }

    $visibleTitles = @(Get-CodexVisibleSidebarTitles -Root $root -Limit $MaxTitleLength)
    $selectedRows = @($visibleTitles | Where-Object { $_.selected })
    $sidebarTitle = if ($selectedRows.Count -gt 0) { [string]$selectedRows[0].title } else { "" }
    if ([string]::IsNullOrWhiteSpace($sidebarTitle)) {
        $sidebarTitle = Get-CodexSelectedSidebarTitle -Root $root -Limit $MaxTitleLength
    }
    $headerTitle = ""
    if ([string]::IsNullOrWhiteSpace($sidebarTitle)) {
        $headerTitle = Get-CodexTopHeaderTitle -Root $root -Limit $MaxTitleLength
    }
    $rootName = ConvertTo-CleanCodexTitle -Title ([string]$root.Current.Name)
    $windowTitle = ConvertTo-CleanCodexTitle -Title ([string]$process.MainWindowTitle)
    $title = if (-not [string]::IsNullOrWhiteSpace($sidebarTitle)) {
        $sidebarTitle
    } elseif (-not [string]::IsNullOrWhiteSpace($headerTitle)) {
        $headerTitle
    } elseif (-not [string]::IsNullOrWhiteSpace($rootName)) {
        $rootName
    } else {
        $windowTitle
    }
    $source = if (-not [string]::IsNullOrWhiteSpace($sidebarTitle)) {
        "codex_app_dom_sidebar_selected_thread"
    } elseif (-not [string]::IsNullOrWhiteSpace($headerTitle)) {
        "codex_app_dom_top_header"
    } elseif (-not [string]::IsNullOrWhiteSpace($rootName)) {
        "uia_root_name"
    } else {
        "win32_window_text"
    }

    $payload.ok = -not [string]::IsNullOrWhiteSpace($title)
    $payload.pid = $process.Id
    $payload.hwnd = [string]$process.MainWindowHandle
    $payload.title = $title
    $payload.source = $source
    $payload.visible_thread_titles = @($visibleTitles | Select-Object title, selected)
    $payload.visible_thread_title_count = @($visibleTitles).Count
    $payload.window_title = $windowTitle
    $payload.uia_root_name = $rootName
} catch {
    $payload.error = $_.Exception.Message
}

$payload | ConvertTo-Json -Compress -Depth 6
