# ==============================================================================
# EARS -- Native Windows GUI Setup & Uninstall Wizard (Pure 7-Bit ASCII)
# Uses built-in .NET WinForms & GDI+ with the Ears Dark Obsidian & Royal Purple
# theme so non-technical users can install or uninstall with zero terminal usage.
# ==============================================================================
param(
    [ValidateSet("install", "uninstall")]
    [string]$Mode = "install"
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
Set-Location -LiteralPath $AppDir
$ManifestFile = Join-Path $AppDir ".install_manifest"

# Theme Colors (Dark Obsidian & Royal Purple)
$BgDark       = [System.Drawing.Color]::FromArgb(13, 10, 23)
$SurfaceCard  = [System.Drawing.Color]::FromArgb(24, 18, 40)
$PurpleBtn    = [System.Drawing.Color]::FromArgb(109, 40, 217)
$AccentPurple = [System.Drawing.Color]::FromArgb(157, 123, 234)
$EmeraldGreen = [System.Drawing.Color]::FromArgb(52, 211, 153)
$DangerRed    = [System.Drawing.Color]::FromArgb(220, 38, 38)
$TextPrimary  = [System.Drawing.Color]::FromArgb(241, 245, 249)
$TextMuted    = [System.Drawing.Color]::FromArgb(157, 148, 184)

# Generate Dynamic 48x48 Waveform Icon in Memory
$Bmp = New-Object System.Drawing.Bitmap(48, 48)
$Gfx = [System.Drawing.Graphics]::FromImage($Bmp)
$Gfx.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$BgBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(24, 18, 40))
$RingPen = New-Object System.Drawing.Pen($AccentPurple, 2.5)
$BarBrush = New-Object System.Drawing.SolidBrush($AccentPurple)
$DotBrush = New-Object System.Drawing.SolidBrush($EmeraldGreen)
$Gfx.FillEllipse($BgBrush, 2, 2, 43, 43)
$Gfx.DrawEllipse($RingPen, 2, 2, 43, 43)
$Gfx.FillRectangle($BarBrush, 11, 19, 3, 10)
$Gfx.FillRectangle($BarBrush, 17, 13, 3, 22)
$Gfx.FillRectangle($BarBrush, 23, 9,  3, 30)
$Gfx.FillRectangle($BarBrush, 29, 15, 3, 18)
$Gfx.FillRectangle($BarBrush, 35, 19, 3, 10)
$Gfx.FillEllipse($DotBrush, 32, 32, 13, 13)
$Gfx.Dispose()

# Main Window
$Form = New-Object System.Windows.Forms.Form
$Form.Text = if ($Mode -eq "install") { "Ears Audio Captioner -- Setup Wizard" } else { "Ears Audio Captioner -- Uninstaller" }
$Form.ClientSize = New-Object System.Drawing.Size(540, 320)
$Form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
$Form.MaximizeBox = $false
$Form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$Form.BackColor = $BgDark
$Form.ForeColor = $TextPrimary
$Form.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)
try {
    $Form.Icon = [System.Drawing.Icon]::FromHandle($Bmp.GetHicon())
} catch {}

# Header Logo & Title
$LogoBox = New-Object System.Windows.Forms.PictureBox
$LogoBox.Image = $Bmp
$LogoBox.Size = New-Object System.Drawing.Size(48, 48)
$LogoBox.Location = New-Object System.Drawing.Point(22, 18)
$Form.Controls.Add($LogoBox)

$TitleLabel = New-Object System.Windows.Forms.Label
$TitleLabel.Text = if ($Mode -eq "install") { "Install Ears Audio Captioner" } else { "Uninstall Ears Audio Captioner" }
$TitleLabel.Font = New-Object System.Drawing.Font("Segoe UI", 15, [System.Drawing.FontStyle]::Bold)
$TitleLabel.ForeColor = $TextPrimary
$TitleLabel.Location = New-Object System.Drawing.Point(82, 16)
$TitleLabel.Size = New-Object System.Drawing.Size(430, 30)
$Form.Controls.Add($TitleLabel)

$SubLabel = New-Object System.Windows.Forms.Label
$SubLabel.UseMnemonic = $false
$SubLabel.Text = if ($Mode -eq "install") {
    "100% local CCTV & IP camera speech-to-text captioning in your System Tray."
} else {
    "Cleanly remove Ears while leaving your pre-existing system packages untouched."
}
$SubLabel.ForeColor = $TextMuted
$SubLabel.Location = New-Object System.Drawing.Point(84, 46)
$SubLabel.Size = New-Object System.Drawing.Size(435, 22)
$Form.Controls.Add($SubLabel)

# Options Card Panel
$Card = New-Object System.Windows.Forms.Panel
$Card.BackColor = $SurfaceCard
$Card.Location = New-Object System.Drawing.Point(22, 78)
$Card.Size = New-Object System.Drawing.Size(496, 112)
$Form.Controls.Add($Card)

$Chk1 = New-Object System.Windows.Forms.CheckBox
$Chk1.Location = New-Object System.Drawing.Point(16, 16)
$Chk1.Size = New-Object System.Drawing.Size(460, 24)
$Chk1.ForeColor = $TextPrimary

$Chk2 = New-Object System.Windows.Forms.CheckBox
$Chk2.Location = New-Object System.Drawing.Point(16, 48)
$Chk2.Size = New-Object System.Drawing.Size(460, 24)
$Chk2.ForeColor = $TextPrimary

$MandatoryLabel = New-Object System.Windows.Forms.Label
$MandatoryLabel.Location = New-Object System.Drawing.Point(18, 80)
$MandatoryLabel.Size = New-Object System.Drawing.Size(460, 22)
$MandatoryLabel.ForeColor = $AccentPurple

if ($Mode -eq "install") {
    $Chk1.Text = "Create a Desktop shortcut for Ears Audio Captioner"
    $Chk1.Checked = $true

    $Chk2.Text = "Start Ears automatically in the System Tray when I log into Windows"
    $Chk2.Checked = $true

    $MandatoryLabel.Text = "Note: The Web Setup Wizard (127.0.0.1:8765) will open automatically."
    $Card.Controls.AddRange(@($Chk1, $Chk2, $MandatoryLabel))
} else {
    $WingetAdded = @()
    if (Test-Path $ManifestFile) {
        foreach ($L in (Get-Content $ManifestFile -ErrorAction SilentlyContinue)) {
            if ($L -like "WINGET_PKG:*") {
                $WingetAdded += $L.Substring(11)
            }
        }
    }
    $Chk1.Text = "Also delete my saved transcripts, audio clips, and config.json"
    $Chk1.Checked = $false

    if ($WingetAdded.Count -gt 0) {
        $Chk2.Text = "Also uninstall packages that Ears installed ($($WingetAdded -join ', '))"
        $Chk2.Checked = $true
    } else {
        $Chk2.Text = "System Python & FFmpeg were pre-existing (will not be touched)"
        $Chk2.Checked = $false
        $Chk2.Enabled = $false
        $Chk2.ForeColor = $TextMuted
    }
    $Card.Controls.AddRange(@($Chk1, $Chk2))
}

# Status Label & Progress Bar
$StatusLabel = New-Object System.Windows.Forms.Label
$StatusLabel.Text = "Folder: $AppDir"
$StatusLabel.ForeColor = $AccentPurple
$StatusLabel.Location = New-Object System.Drawing.Point(22, 202)
$StatusLabel.Size = New-Object System.Drawing.Size(496, 20)
$Form.Controls.Add($StatusLabel)

$ProgressBar = New-Object System.Windows.Forms.ProgressBar
$ProgressBar.Location = New-Object System.Drawing.Point(22, 226)
$ProgressBar.Size = New-Object System.Drawing.Size(496, 20)
$ProgressBar.Minimum = 0
$ProgressBar.Maximum = 100
$ProgressBar.Value = 0
$Form.Controls.Add($ProgressBar)

# Collapsible Log Box
$LogBox = New-Object System.Windows.Forms.TextBox
$LogBox.Multiline = $true
$LogBox.ReadOnly = $true
$LogBox.ScrollBars = [System.Windows.Forms.ScrollBars]::Vertical
$LogBox.BackColor = [System.Drawing.Color]::FromArgb(8, 6, 15)
$LogBox.ForeColor = $TextMuted
$LogBox.Font = New-Object System.Drawing.Font("Consolas", 8.5)
$LogBox.Location = New-Object System.Drawing.Point(22, 315)
$LogBox.Size = New-Object System.Drawing.Size(496, 175)
$LogBox.Visible = $false
$Form.Controls.Add($LogBox)

# Buttons Row
$BtnDetails = New-Object System.Windows.Forms.Button
$BtnDetails.Text = "Show Technical Log"
$BtnDetails.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$BtnDetails.BackColor = $SurfaceCard
$BtnDetails.ForeColor = $TextMuted
$BtnDetails.Location = New-Object System.Drawing.Point(22, 262)
$BtnDetails.Size = New-Object System.Drawing.Size(145, 36)
$BtnDetails.Add_Click({
    if ($LogBox.Visible) {
        $LogBox.Visible = $false
        $Form.ClientSize = New-Object System.Drawing.Size(540, 320)
        $BtnDetails.Text = "Show Technical Log"
    } else {
        $LogBox.Visible = $true
        $Form.ClientSize = New-Object System.Drawing.Size(540, 508)
        $BtnDetails.Text = "Hide Technical Log"
    }
})
$Form.Controls.Add($BtnDetails)

$BtnPrimary = New-Object System.Windows.Forms.Button
$BtnPrimary.Text = if ($Mode -eq "install") { "Install Ears Now" } else { "Uninstall Ears" }
$BtnPrimary.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$BtnPrimary.BackColor = if ($Mode -eq "install") { $PurpleBtn } else { $DangerRed }
$BtnPrimary.ForeColor = [System.Drawing.Color]::White
$BtnPrimary.Font = New-Object System.Drawing.Font("Segoe UI", 9.5, [System.Drawing.FontStyle]::Bold)
$BtnPrimary.Location = New-Object System.Drawing.Point(250, 262)
$BtnPrimary.Size = New-Object System.Drawing.Size(165, 36)
$Form.Controls.Add($BtnPrimary)

$BtnClose = New-Object System.Windows.Forms.Button
$BtnClose.Text = "Close"
$BtnClose.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$BtnClose.BackColor = $SurfaceCard
$BtnClose.ForeColor = $TextPrimary
$BtnClose.Location = New-Object System.Drawing.Point(425, 262)
$BtnClose.Size = New-Object System.Drawing.Size(93, 36)
$BtnClose.Add_Click({ $Form.Close() })
$Form.Controls.Add($BtnClose)

# Background Process Runner
$Script:Proc = $null
$Script:OutFile = Join-Path ([System.IO.Path]::GetTempPath()) "ears_gui_wizard.log"
$Script:ReadOffset = 0
$Script:Finished = $false

$Timer = New-Object System.Windows.Forms.Timer
$Timer.Interval = 180
$Timer.Add_Tick({
    if (Test-Path $Script:OutFile) {
        try {
            $Fs = New-Object System.IO.FileStream($Script:OutFile, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
            $Sr = New-Object System.IO.StreamReader($Fs)
            $Content = $Sr.ReadToEnd()
            $Sr.Close()
            $Fs.Close()
            if ($Content) {
                $AllLines = $Content -split "\r?\n"
                if ($AllLines.Count -gt 0 -and $AllLines[$AllLines.Count - 1] -eq "") {
                    $AllLines = $AllLines[0..($AllLines.Count - 2)]
                }
                if ($AllLines.Count -gt $Script:ReadOffset) {
                    $NewLines = $AllLines[$Script:ReadOffset..($AllLines.Count - 1)]
                    $Script:ReadOffset = $AllLines.Count
                    foreach ($Line in $NewLines) {
                        $LogBox.AppendText("$Line`r`n")
                        if ($Line -match "^\[1/") {
                            $ProgressBar.Value = 20
                            $StatusLabel.Text = $Line
                        } elseif ($Line -match "^\[2/") {
                            $ProgressBar.Value = 45
                            $StatusLabel.Text = $Line
                        } elseif ($Line -match "Installing Ears Python dependencies") {
                            $ProgressBar.Value = 60
                            $StatusLabel.Text = $Line.Trim()
                        } elseif ($Line -match "^\[3/") {
                            $ProgressBar.Value = 80
                            $StatusLabel.Text = $Line
                        } elseif ($Line -match "^\[4/" -or $Line -match "^\[5/") {
                            $ProgressBar.Value = 92
                            $StatusLabel.Text = $Line
                        } elseif ($Line -match "Selected Profile:") {
                            $StatusLabel.Text = $Line.Trim()
                        }
                    }
                }
            }
        } catch {}
    }

    if ($Script:Proc -and $Script:Proc.HasExited) {
        $Timer.Stop()
        $Script:Finished = $true
        $ExitCode = $Script:Proc.ExitCode
        if ($ExitCode -eq 0) {
            $ProgressBar.Value = 100
            if ($Mode -eq "install") {
                $StatusLabel.ForeColor = $EmeraldGreen
                $StatusLabel.Text = "Installation Complete! Ears is ready at http://127.0.0.1:8765"
                $BtnPrimary.Text = "Open Dashboard"
                $BtnPrimary.BackColor = $EmeraldGreen
                $BtnPrimary.ForeColor = $BgDark
                $BtnPrimary.Enabled = $true
            } else {
                $StatusLabel.ForeColor = $EmeraldGreen
                $StatusLabel.Text = "Ears has been cleanly uninstalled from Windows."
                $BtnPrimary.Text = "Uninstalled"
            }
        } else {
            $StatusLabel.ForeColor = $DangerRed
            $StatusLabel.Text = "Process exited with code $ExitCode. Click 'Show Technical Log' for details."
            if (-not $LogBox.Visible) {
                $BtnDetails.PerformClick()
            }
            $BtnPrimary.Enabled = $true
            $BtnPrimary.Text = "Retry"
        }
    }
})

$BtnPrimary.Add_Click({
    if ($Script:Finished -and $Mode -eq "install") {
        Start-Process "http://127.0.0.1:8765"
        $Form.Close()
        return
    }

    $BtnPrimary.Enabled = $false
    $Chk1.AutoCheck = $false
    $Chk2.AutoCheck = $false
    $StatusLabel.ForeColor = $AccentPurple
    $ProgressBar.Value = 10
    $LogBox.Clear()
    $Script:ReadOffset = 0
    if (Test-Path $Script:OutFile) {
        Remove-Item $Script:OutFile -Force -ErrorAction SilentlyContinue
    }

    if ($Mode -eq "install") {
        $TargetScript = Join-Path $ScriptDir "install_windows.ps1"
        $ExtraArgs = @()
        if (-not $Chk1.Checked) { $ExtraArgs += "-NoShortcut" }
        if ($Chk2.Checked)      { $ExtraArgs += "-EnableAutostart" }
        $StatusLabel.Text = "Starting Ears Windows setup..."
    } else {
        $TargetScript = Join-Path $ScriptDir "uninstall_windows.ps1"
        $ExtraArgs = @("-NonInteractive")
        if ($Chk1.Checked) { $ExtraArgs += "-DeleteData" }
        if ($Chk2.Checked) { $ExtraArgs += "-RemoveWinget" }
        $StatusLabel.Text = "Uninstalling Ears..."
    }

    $CmdLine = "/c powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$TargetScript`" $($ExtraArgs -join ' ') > `"$($Script:OutFile)`" 2>&1"
    $Psi = New-Object System.Diagnostics.ProcessStartInfo
    $Psi.FileName = "cmd.exe"
    $Psi.Arguments = $CmdLine
    $Psi.UseShellExecute = $false
    $Psi.CreateNoWindow = $true
    $Script:Proc = [System.Diagnostics.Process]::Start($Psi)
    $Timer.Start()
})

[void]$Form.ShowDialog()
