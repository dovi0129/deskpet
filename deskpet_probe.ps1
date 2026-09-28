# DeskPet sensor worker
# Emits one compact JSON object every ~2 seconds.
# Uses Windows built-in CIM/WMI providers and, when already available,
# LibreHardwareMonitor/OpenHardwareMonitor WMI sensors.

param([switch]$Once)

$ErrorActionPreference = 'SilentlyContinue'
# Avoid retaining large histories of expected absent-provider errors.
$MaximumErrorCount = 32
$script:MissingProviders = @{}
$script:ProbeClock = [System.Diagnostics.Stopwatch]::StartNew()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function NumOrNull($obj, $prop) {
    try {
        if ($null -ne $obj -and $null -ne $obj.$prop -and "$($obj.$prop)" -ne "") {
            return [double]$obj.$prop
        }
    } catch {}
    return $null
}

function BoolOrNull($obj, $prop) {
    try {
        if ($null -ne $obj -and $null -ne $obj.$prop) {
            return [bool]$obj.$prop
        }
    } catch {}
    return $null
}

$script:TempDiagnostics = @()
$script:AsusWmiDisabled = $false
$script:AsusWmiDisableReason = ''

function Reset-TempDiagnostics() {
    $script:TempDiagnostics = @()
}

function Add-TempDiagnostic($text) {
    try { $script:TempDiagnostics += [string]$text } catch {}
}


function Get-AsusWmiCpuTemperature() {
    if ($script:AsusWmiDisabled) {
        Add-TempDiagnostic "ASUS WMI DSTS: disabled ($script:AsusWmiDisableReason)"
        return $null
    }
    try {
        $asus = Get-CimInstance -Namespace root/wmi -ClassName AsusAtkWmi_WMNB -ErrorAction Stop | Select-Object -First 1
        if ($null -eq $asus) {
            Add-TempDiagnostic 'ASUS WMI DSTS: AsusAtkWmi_WMNB class empty'
            return $null
        }

        # Read-only DSTS query of ASUS firmware endpoint Temp_CPU (0x00120094).
        # ASUS firmware usually returns a 0x10000 validity flag plus the value.
        $result = Invoke-CimMethod -InputObject $asus -MethodName DSTS -Arguments @{ Device_ID = [uint32]0x00120094 } -ErrorAction Stop
        if ($null -eq $result -or $null -eq $result.device_status) {
            Add-TempDiagnostic 'ASUS WMI DSTS: no device_status returned'
            return $null
        }

        $raw = [int64]$result.device_status
        if ($raw -eq 4294967294 -or $raw -eq 4294967295) {
            $script:AsusWmiDisabled = $true
            $script:AsusWmiDisableReason = "unsupported sentinel raw=$raw"
            Add-TempDiagnostic "ASUS WMI DSTS: unsupported sentinel (raw=$raw)"
            return $null
        }
        $value = [double]($raw - 65536)
        if ($value -lt 10.0 -or $value -gt 120.0) {
            # Some firmware exposes only the low 16 bits as the value.
            $low = [double]($raw -band 0xFFFF)
            if ($low -ge 10.0 -and $low -le 120.0) { $value = $low }
        }

        if ($value -ge 10.0 -and $value -le 120.0) {
            Add-TempDiagnostic "ASUS WMI DSTS: OK (Temp_CPU = $([Math]::Round($value,1)) C, raw=$raw)"
            return [pscustomobject]@{
                cpu_c = [Math]::Round($value, 1)
                source = 'ASUS firmware (WMI DSTS)'
                sensor_name = 'Temp_CPU 0x00120094'
                reliable = $true
            }
        }

        Add-TempDiagnostic "ASUS WMI DSTS: implausible value $value C (raw=$raw)"
    } catch {
        $msg = [string]$_.Exception.Message
        if ($msg -match '(?i)access.*denied|액세스.*거부') {
            $script:AsusWmiDisabled = $true
            $script:AsusWmiDisableReason = 'access denied'
        }
        Add-TempDiagnostic "ASUS WMI DSTS: unavailable ($msg)"
    }
    return $null
}

function Get-HardwareMonitorCpuTemperature($namespace, $sourceName) {
    $retryAt = $script:MissingProviders[$namespace]
    if ($null -ne $retryAt -and $script:ProbeClock.Elapsed.TotalSeconds -lt $retryAt) {
        Add-TempDiagnostic "${sourceName}: absent-provider backoff (retry within 60s)"
        return $null
    }
    try {
        $rows = @(Get-CimInstance -Namespace $namespace -ClassName Sensor -Property Name,Identifier,SensorType,Value -ErrorAction Stop | Where-Object {
            [string]$_.SensorType -eq 'Temperature' -and $null -ne $_.Value
        })
        if ($rows.Count -eq 0) {
            $script:MissingProviders[$namespace] = $script:ProbeClock.Elapsed.TotalSeconds + 60
            Add-TempDiagnostic "${sourceName}: no temperature sensors"
            return $null
        }

        $cpuRows = @($rows | Where-Object {
            $id = [string]$_.Identifier
            $name = [string]$_.Name
            $id -match '(?i)(intelcpu|amdcpu|/cpu/|/cpu$)' -or
            $name -match '(?i)CPU Package|CPU Core|Core #|Core Max|Tctl|Tdie'
        })
        if ($cpuRows.Count -eq 0) {
            $script:MissingProviders[$namespace] = $script:ProbeClock.Elapsed.TotalSeconds + 60
            Add-TempDiagnostic "${sourceName}: namespace present, no CPU temp sensor"
            return $null
        }

        $preferred = @($cpuRows | Where-Object {
            [string]$_.Name -match '(?i)CPU Package|Core Max|Tctl|Tdie'
        })
        $pool = if ($preferred.Count -gt 0) { $preferred } else { $cpuRows }

        $best = $null
        $bestValue = -999.0
        foreach ($row in $pool) {
            try {
                $v = [double]$row.Value
                if ($v -ge 10.0 -and $v -le 120.0 -and $v -gt $bestValue) {
                    $best = $row
                    $bestValue = $v
                }
            } catch {}
        }
        if ($null -eq $best) {
            Add-TempDiagnostic "${sourceName}: CPU sensors had no plausible value"
            return $null
        }

        $script:MissingProviders.Remove($namespace)
        Add-TempDiagnostic "${sourceName}: OK ($([string]$best.Name) = $([Math]::Round($bestValue,1)) C)"
        return [pscustomobject]@{
            cpu_c = [Math]::Round($bestValue, 1)
            source = $sourceName
            sensor_name = [string]$best.Name
            reliable = $true
        }
    } catch {
        $script:MissingProviders[$namespace] = $script:ProbeClock.Elapsed.TotalSeconds + 60
        Add-TempDiagnostic "${sourceName}: unavailable ($($_.Exception.Message))"
    }
    return $null
}

function Get-PerfThermalZoneTemperature() {
    try {
        $zones = @(Get-CimInstance -Namespace root/cimv2 -ClassName Win32_PerfFormattedData_Counters_ThermalZoneInformation -ErrorAction Stop)
        if ($zones.Count -eq 0) {
            Add-TempDiagnostic 'Windows perf thermal zones: none'
            return $null
        }

        $candidates = @()
        foreach ($z in $zones) {
            try {
                $name = [string]$z.Name
                $c = $null
                if ($null -ne $z.HighPrecisionTemperature -and [double]$z.HighPrecisionTemperature -gt 0) {
                    $c = ([double]$z.HighPrecisionTemperature / 10.0) - 273.15
                } elseif ($null -ne $z.Temperature -and [double]$z.Temperature -gt 0) {
                    $c = [double]$z.Temperature - 273.15
                }
                if ($null -eq $c -or $c -lt 10.0 -or $c -gt 120.0) { continue }

                $priority = 9
                if ($name -match '(?i)CPUZ|THRM|CPU|DTSZ') { $priority = 0 }
                elseif ($name -match '(?i)TZ00|TZ01|HPTZ|TSZ') { $priority = 2 }
                elseif ($name -match '(?i)BAT|GFX|CHG') { $priority = 20 }

                $candidates += [pscustomobject]@{ Name=$name; C=[double]$c; Priority=$priority }
            } catch {}
        }

        $valid = @($candidates | Where-Object { $_.Priority -lt 20 } | Sort-Object Priority, @{Expression='C';Descending=$true})
        if ($valid.Count -gt 0) {
            $best = $valid | Select-Object -First 1
            Add-TempDiagnostic "Windows perf thermal zone: OK ($($best.Name) = $([Math]::Round($best.C,1)) C)"
            return [pscustomobject]@{
                cpu_c = [Math]::Round([double]$best.C, 1)
                source = 'Windows thermal performance counter'
                sensor_name = [string]$best.Name
                reliable = $false
            }
        }
        Add-TempDiagnostic 'Windows perf thermal zones: no plausible CPU-like zone'
    } catch {
        Add-TempDiagnostic "Windows perf thermal zones: unavailable ($($_.Exception.Message))"
    }
    return $null
}

function Get-AcpiCpuTemperature() {
    try {
        $zones = @(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop)
        if ($zones.Count -eq 0) {
            Add-TempDiagnostic 'Windows ACPI thermal zones: none'
            return $null
        }
        $bestC = -999.0
        $bestName = ''
        foreach ($z in $zones) {
            try {
                $raw = [double]$z.CurrentTemperature
                $c = ($raw / 10.0) - 273.15
                $name = [string]$z.InstanceName
                if ($name -match '(?i)BAT|GFX|CHG') { continue }
                if ($c -ge 10.0 -and $c -le 120.0 -and $c -gt $bestC) {
                    $bestC = $c
                    $bestName = $name
                }
            } catch {}
        }
        if ($bestC -gt -900.0) {
            Add-TempDiagnostic "Windows ACPI thermal zone: OK ($bestName = $([Math]::Round($bestC,1)) C)"
            return [pscustomobject]@{
                cpu_c = [Math]::Round($bestC, 1)
                source = 'Windows ACPI thermal zone'
                sensor_name = $bestName
                reliable = $false
            }
        }
        Add-TempDiagnostic 'Windows ACPI thermal zones: no plausible CPU-like zone'
    } catch {
        Add-TempDiagnostic "Windows ACPI thermal zones: unavailable ($($_.Exception.Message))"
    }
    return $null
}

function Get-DirectCpuTemperature() {
    # First try ASUS' read-only WMI DSTS endpoint. This uses the same
    # firmware Temp_CPU device id but avoids the direct ATKACPI IOCTL path,
    # which is unsupported on some newer ASUS System Control Interface builds.
    $temp = Get-AsusWmiCpuTemperature
    if ($null -ne $temp) { return $temp }

    $temp = Get-HardwareMonitorCpuTemperature 'root/LibreHardwareMonitor' 'LibreHardwareMonitor'
    if ($null -ne $temp) { return $temp }

    return Get-HardwareMonitorCpuTemperature 'root/OpenHardwareMonitor' 'OpenHardwareMonitor'
}

function Get-RawThermalZoneTemperature() {
    $temp = Get-PerfThermalZoneTemperature
    if ($null -ne $temp) { return $temp }
    return Get-AcpiCpuTemperature
}

# Mostly static GPU metadata: read once. Windows does not expose one perfect
# integrated/discrete flag through Win32_VideoController, so we use a
# conservative name/vendor heuristic only for presentation. GPU utilization
# below still comes from Windows GPU Engine counters and therefore represents
# overall GPU activity without waking a sleeping dGPU with vendor utilities.
function Get-GpuKind($gpu) {
    $name = [string]$gpu.Name
    $pnp = [string]$gpu.PNPDeviceID
    if ($name -match '(?i)Microsoft Basic|Remote Display|Virtual Display') { return 'virtual' }

    if ($pnp -match '(?i)VEN_10DE' -or $name -match '(?i)NVIDIA') {
        return 'discrete'
    }

    if ($pnp -match '(?i)VEN_8086' -or $name -match '(?i)Intel') {
        # Intel Arc A/B-series are discrete; Core Ultra Arc 130V/140V and
        # Iris/UHD are integrated.
        if ($name -match '(?i)\bArc(?:\(TM\))?\s+[AB]\d{3,4}\b') { return 'discrete' }
        return 'integrated'
    }

    if ($pnp -match '(?i)VEN_1002' -or $name -match '(?i)AMD|Radeon') {
        if ($name -match '(?i)Radeon\s+(RX\b|Pro\s+W|VII\b|Instinct)|FirePro') { return 'discrete' }
        if ($name -match '(?i)Radeon(?:\(TM\))?\s+Graphics|Radeon\s+[6789]\d{2}M\b|Vega\s+\d+\s+Graphics') { return 'integrated' }
        return 'unknown'
    }

    return 'unknown'
}

$gpuAdapters = @()
try {
    $rawGpus = @(Get-CimInstance Win32_VideoController -Property Name,PNPDeviceID,AdapterRAM | Where-Object { $_.Name })
    foreach ($g in $rawGpus) {
        $ram = 0.0
        try { if ($null -ne $g.AdapterRAM) { $ram = [double]$g.AdapterRAM } } catch {}
        $gpuAdapters += [pscustomobject]@{
            Name = [string]$g.Name
            Kind = Get-GpuKind $g
            AdapterRAM = $ram
        }
    }
} catch {}

$usableGpuAdapters = @($gpuAdapters | Where-Object { $_.Kind -ne 'virtual' })
$discreteGpuAdapters = @($usableGpuAdapters | Where-Object { $_.Kind -eq 'discrete' })
$preferredGpu = $null
if ($discreteGpuAdapters.Count -gt 0) {
    $preferredGpu = $discreteGpuAdapters | Sort-Object AdapterRAM -Descending | Select-Object -First 1
} elseif ($usableGpuAdapters.Count -gt 0) {
    $preferredGpu = $usableGpuAdapters | Select-Object -First 1
}

$gpuNameText = ''
$gpuKindText = 'unknown'
$gpuHasDiscrete = ($discreteGpuAdapters.Count -gt 0)
$gpuOtherCount = 0
if ($null -ne $preferredGpu) {
    $gpuNameText = [string]$preferredGpu.Name
    $gpuKindText = [string]$preferredGpu.Kind
    $gpuOtherCount = [Math]::Max(0, $usableGpuAdapters.Count - 1)
}


# NPU discovery. Newer Windows 11 builds expose an "NPU Engine" performance
# counter set. Discover it once so the normal probe loop stays lightweight.
$script:NpuPresent = $false
$script:NpuCounterPath = $null
$script:NpuSource = ''

try {
    $npuDevices = @(Get-CimInstance Win32_PnPEntity -Property Name -ErrorAction Stop | Where-Object {
        $name = [string]$_.Name
        $name -match '(?i)Intel.*AI Boost|Neural Processing Unit|\bNPU\b|Neural Processor'
    })
    if ($npuDevices.Count -gt 0) { $script:NpuPresent = $true }
} catch {}

try {
    $npuSets = @()
    try { $npuSets += @(Get-Counter -ListSet 'NPU Engine' -ErrorAction Stop) } catch {}
    if ($npuSets.Count -eq 0) {
        try {
            $npuSets += @(Get-Counter -ListSet * -ErrorAction Stop | Where-Object {
                ([string]$_.CounterSetName) -match '(?i)NPU|Neural'
            })
        } catch {}
    }
    foreach ($set in $npuSets) {
        $paths = @($set.Paths | Where-Object { $_ -match '(?i)Utilization|Usage' })
        if ($paths.Count -eq 0) {
            $paths = @($set.PathsWithInstances | Where-Object { $_ -match '(?i)Utilization|Usage' })
        }
        if ($paths.Count -gt 0) {
            $script:NpuCounterPath = [string]$paths[0]
            $script:NpuPresent = $true
            $script:NpuSource = "Windows performance counter: $($set.CounterSetName)"
            break
        }
    }
} catch {}

function Get-NpuUsage() {
    $value = $null
    if ($null -ne $script:NpuCounterPath -and $script:NpuCounterPath -ne '') {
        try {
            $sample = Get-Counter -Counter $script:NpuCounterPath -MaxSamples 1 -ErrorAction Stop
            $vals = @($sample.CounterSamples | ForEach-Object {
                try { [double]$_.CookedValue } catch { $null }
            } | Where-Object { $null -ne $_ -and $_ -ge 0 })
            if ($vals.Count -gt 0) {
                $value = [Math]::Min(100.0, [Math]::Max(0.0, ($vals | Measure-Object -Maximum).Maximum))
            }
        } catch {}
    }
    return [pscustomobject]@{
        present = [bool]$script:NpuPresent
        overall = $value
        source = [string]$script:NpuSource
    }
}

$design = $null
$full = $null
$cycle = $null
$win32Bat = $null
try { $design = Get-CimInstance -Namespace root/wmi -ClassName BatteryStaticData | Select-Object -First 1 } catch {}
try { $full = Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity | Select-Object -First 1 } catch {}
try { $cycle = Get-CimInstance -Namespace root/wmi -ClassName BatteryCycleCount | Select-Object -First 1 } catch {}
try { $win32Bat = Get-CimInstance Win32_Battery | Select-Object -First 1 } catch {}

$designMwh = NumOrNull $design 'DesignedCapacity'
$fullMwh = NumOrNull $full 'FullChargedCapacity'
$cycleCount = NumOrNull $cycle 'CycleCount'

if ($null -eq $designMwh) { $designMwh = NumOrNull $win32Bat 'DesignCapacity' }
if ($null -eq $fullMwh) { $fullMwh = NumOrNull $win32Bat 'FullChargeCapacity' }
if ($null -eq $cycleCount) { $cycleCount = NumOrNull $win32Bat 'CycleCount' }

while ($true) {
    $cats = [ordered]@{
        '3D' = 0.0
        'Compute' = 0.0
        'VideoDecode' = 0.0
        'VideoEncode' = 0.0
        'Copy' = 0.0
        'Other' = 0.0
    }

    $gpuValid = $false
    $luidUse = @{}
    try {
        $rows = @(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -Property Name,UtilizationPercentage -ErrorAction Stop)
        $gpuValid = $true
        $physical = @{}

        foreach ($row in $rows) {
            $u = 0.0
            try { $u = [double]$row.UtilizationPercentage } catch { $u = 0.0 }
            $name = [string]$row.Name
            # Every adapter LUID seen, idle ones too, so DeskPet can tell 0% from a missing GPU.
            if ($name -match 'luid_(0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)_phys_') {
                if (-not $luidUse.ContainsKey($matches[1])) { $luidUse[$matches[1]] = 0.0 }
            }
            if ($u -le 0) { continue }

            $key = $name
            $etype = 'Other'

            if ($name -match 'luid_(.+?)_phys_(\d+)_eng_(\d+)_engtype_(.+)$') {
                $key = "$($matches[1])|$($matches[2])|$($matches[3])"
                $etype = $matches[4]
            } elseif ($name -match 'engtype_(.+)$') {
                $etype = $matches[1]
            }

            if (-not $physical.ContainsKey($key)) {
                $physical[$key] = [ordered]@{ Type = $etype; Util = 0.0 }
            }
            $physical[$key].Util = [double]$physical[$key].Util + $u
        }

        foreach ($entry in $physical.GetEnumerator()) {
            $etype = [string]$entry.Value.Type
            $u = [Math]::Min(100.0, [double]$entry.Value.Util)
            $cat = 'Other'

            switch -Regex ($etype) {
                '^3D$' { $cat = '3D'; break }
                '^Compute' { $cat = 'Compute'; break }
                '^VideoDecode' { $cat = 'VideoDecode'; break }
                '^VideoEncode' { $cat = 'VideoEncode'; break }
                '^Copy' { $cat = 'Copy'; break }
            }

            if ($u -gt [double]$cats[$cat]) {
                $cats[$cat] = $u
            }
            $lk = ([string]$entry.Key).Split('|')[0]
            if ($luidUse.ContainsKey($lk) -and $u -gt [double]$luidUse[$lk]) {
                $luidUse[$lk] = $u
            }
        }
    } catch {}

    $luidOut = [ordered]@{}
    foreach ($k in ($luidUse.Keys | Sort-Object)) { $luidOut[[string]$k] = [Math]::Round([double]$luidUse[$k], 1) }

    $overall = 0.0
    foreach ($v in $cats.Values) {
        if ([double]$v -gt $overall) { $overall = [double]$v }
    }

    $status = $null
    try { $status = Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus | Select-Object -First 1 } catch {}

    if ((Get-Random -Minimum 0 -Maximum 30) -eq 0) {
        try {
            $fullTmp = Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity | Select-Object -First 1
            $tmpVal = NumOrNull $fullTmp 'FullChargedCapacity'
            if ($null -ne $tmpVal) { $fullMwh = $tmpVal }
        } catch {}
    }

    Reset-TempDiagnostics
    $directTemp = Get-DirectCpuTemperature
    $thermalZone = Get-RawThermalZoneTemperature
    $temp = if ($null -ne $directTemp) { $directTemp } else { $thermalZone }

    $tempObj = [ordered]@{
        cpu_c = $null
        source = ''
        sensor_name = ''
        reliable = $false
        diagnostics = ($script:TempDiagnostics -join ' | ')
    }
    if ($null -ne $temp) {
        $tempObj.cpu_c = $temp.cpu_c
        $tempObj.source = [string]$temp.source
        $tempObj.sensor_name = [string]$temp.sensor_name
        $tempObj.reliable = [bool]$temp.reliable
    }

    $thermalObj = [ordered]@{
        cpu_c = $null
        source = ''
        sensor_name = ''
    }
    if ($null -ne $thermalZone) {
        $thermalObj.cpu_c = $thermalZone.cpu_c
        $thermalObj.source = [string]$thermalZone.source
        $thermalObj.sensor_name = [string]$thermalZone.sensor_name
    }

    $npu = Get-NpuUsage

    $obj = [ordered]@{
        npu = [ordered]@{
            present = [bool]$npu.present
            overall = $npu.overall
            source = [string]$npu.source
        }
        gpu_name = $gpuNameText
        gpu_kind = $gpuKindText
        gpu_has_discrete = $gpuHasDiscrete
        gpu_other_count = $gpuOtherCount
        gpu_usage_scope = 'all-adapters'
        gpu = [ordered]@{
            valid = $gpuValid
            overall = [Math]::Round($overall, 1)
            '3d' = [Math]::Round([double]$cats['3D'], 1)
            compute = [Math]::Round([double]$cats['Compute'], 1)
            video_decode = [Math]::Round([double]$cats['VideoDecode'], 1)
            video_encode = [Math]::Round([double]$cats['VideoEncode'], 1)
            copy = [Math]::Round([double]$cats['Copy'], 1)
        }
        gpu_luids = $luidOut
        temperature = $tempObj
        thermal_zone = $thermalObj
        battery = [ordered]@{
            remaining_mwh = NumOrNull $status 'RemainingCapacity'
            design_mwh = $designMwh
            full_mwh = $fullMwh
            cycle_count = $cycleCount
            voltage_mv = NumOrNull $status 'Voltage'
            charge_rate_mw = NumOrNull $status 'ChargeRate'
            discharge_rate_mw = NumOrNull $status 'DischargeRate'
            power_online = BoolOrNull $status 'PowerOnline'
            charging = BoolOrNull $status 'Charging'
            discharging = BoolOrNull $status 'Discharging'
        }
    }

    try {
        $obj | ConvertTo-Json -Compress -Depth 5
        [Console]::Out.Flush()
    } catch {}

    if ($Once) { break }
    Start-Sleep -Milliseconds 2000
}
