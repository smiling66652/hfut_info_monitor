# ============================================================
# hfut_info_monitor 便捷启动脚本
# 自动激活项目 venv，解决 Python 环境不匹配问题
# ============================================================

param(
    [Parameter(Position=0)]
    [string]$Script = "main_controller.py",
    
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$RemainingArgs
)

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"

# 检查 venv 是否存在
if (-not (Test-Path $VenvPython)) {
    Write-Host "❌ .venv 不存在，请先运行 setup_venv.ps1" -ForegroundColor Red
    exit 1
}

# 激活并执行
$Args = @($Script) + $RemainingArgs
& $VenvPython $Args
exit $LASTEXITCODE
