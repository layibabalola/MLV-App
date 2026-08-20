Set-StrictMode -Version Latest

function Test-ReviewedReleaseRuntimeExtraName {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string]$Name
    )

    $exactNames = @(
        'pixel_maps',
        'releases',
        'cudart64_12.dll',
        'igpu_recon_cuda.dll',
        'igpu_recon_cuda.arch.json'
    )
    if ($exactNames -ccontains $Name) {
        return $true
    }
    return [bool]($Name -cmatch '^(?:avcodec|avformat|avutil|swresample|swscale)-[0-9]+\.dll$')
}

Export-ModuleMember -Function Test-ReviewedReleaseRuntimeExtraName
