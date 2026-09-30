$ErrorActionPreference = "Stop"
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.lock
if ($LASTEXITCODE -ne 0) { throw "Dependency install failed" }
& .\.venv\Scripts\python.exe -m pip install --no-deps -e backend
Push-Location frontend
npm ci --no-fund --no-audit
if ($LASTEXITCODE -ne 0) { throw "Frontend install failed" }
Pop-Location
Write-Output "Installed. Generate admin configuration, copy .env.example, then docker compose up --build."
