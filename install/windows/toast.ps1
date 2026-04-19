param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][string]$Body
)
Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Visible = $true
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.ShowBalloonTip(12000, $Title, $Body, [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Seconds 13
$n.Visible = $false
$n.Dispose()
