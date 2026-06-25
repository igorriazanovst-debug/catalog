# setup.ps1 - Первоначальная настройка окружения для Windows 11

Write-Host "=== Настройка окружения для проекта Catalog ===" -ForegroundColor Green

# 1. Проверка установленных компонентов
Write-Host "`nПроверка установленных компонентов..." -ForegroundColor Yellow

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Git не установлен. Установите Git с https://git-scm.com/download/win" -ForegroundColor Red
    exit 1
}
Write-Host "✓ Git установлен" -ForegroundColor Green

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python не установлен. Установите Python 3.11+ с https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}
$pythonVersion = python --version
Write-Host "✓ $pythonVersion установлен" -ForegroundColor Green

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "Node.js не установлен. Установите Node.js 18+ с https://nodejs.org/" -ForegroundColor Red
    exit 1
}
$nodeVersion = node --version
Write-Host "✓ Node.js $nodeVersion установлен" -ForegroundColor Green

# 2. Настройка Git
Write-Host "`n=== Настройка Git ===" -ForegroundColor Yellow
Write-Host "Введите ваше имя для Git:" -ForegroundColor Cyan
$gitName = Read-Host
Write-Host "Введите ваш email для Git:" -ForegroundColor Cyan
$gitEmail = Read-Host

git config --global user.name "$gitName"
git config --global user.email "$gitEmail"
Write-Host "✓ Git настроен" -ForegroundColor Green

# 3. Создание структуры проекта
Write-Host "`n=== Создание структуры проекта ===" -ForegroundColor Yellow

$directories = @(
    "backend/app/api",
    "backend/app/core",
    "backend/app/models",
    "backend/app/services",
    "backend/app/utils",
    "backend/scripts",
    "backend/tests",
    "frontend/src/app",
    "frontend/src/components",
    "frontend/src/lib",
    "frontend/public",
    "database",
    "docker",
    "data/input",
    "data/output",
    "data/temp"
)

foreach ($dir in $directories) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}
Write-Host "✓ Структура проекта создана" -ForegroundColor Green

# 4. Создание виртуального окружения Python
Write-Host "`n=== Настройка Python окружения ===" -ForegroundColor Yellow
Set-Location "backend"
python -m venv venv
Write-Host "✓ Виртуальное окружение создано" -ForegroundColor Green

Write-Host "`n=== Настройка завершена! ===" -ForegroundColor Green
Write-Host "`nСледующие шаги:" -ForegroundColor Cyan
Write-Host "1. cd backend" -ForegroundColor White
Write-Host "2. .\venv\Scripts\Activate.ps1" -ForegroundColor White
Write-Host "3. pip install -r requirements.txt" -ForegroundColor White
Write-Host "4. cd ..\frontend" -ForegroundColor White
Write-Host "5. npm install" -ForegroundColor White