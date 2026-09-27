# Job Finder

Personal job discovery project using Python, MySQL, Flask, Docker, and SearXNG.

## Setup

Install Python packages:

```powershell
& "C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe" -m pip install requests beautifulsoup4 mysql-connector-python Flask tzdata python-dotenv
```

Set your MySQL password in the included `.env` file (leave it blank if your local MySQL user has no password). Keep the generated SearXNG secret private.

Do not commit `.env` to GitHub.

## Start Everything

```powershell
.\starter.ps1
```

This launches:

- Flask dashboard
- MySQL
- Docker Desktop
- SearXNG
- Job Finder

Dashboard:

```text
http://127.0.0.1:5000
```

SearXNG:

```text
http://localhost:8080
```

## Run Job Finder Directly

```powershell
& "C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe" .\job_finder.py
```

## Run Dashboard Directly

```powershell
& "C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe" .\dashboard.py
```

Stop a running Python program with:

```text
Ctrl+C
```

## Docker / SearXNG

Start manually:

```powershell
docker compose --env-file ".\.env" -f ".\searxng\docker-compose.yml" up -d
```

Stop:

```powershell
docker compose --env-file ".\.env" -f ".\searxng\docker-compose.yml" down
```

Status:

```powershell
docker ps
```

Restart:

```powershell
docker restart searxng
```

Logs:

```powershell
docker logs --tail 50 searxng
```

## MySQL

Database:

```text
job_finder
```

Table:

```text
companies
```

Default port:

```text
3306
```

Manual start:

```powershell
& "C:\xampp\mysql_start.bat"
```

Manual stop:

```powershell
& "C:\xampp\mysql_stop.bat"
```

## Project Structure

```text
job-finder/
├── .env
├── .gitignore
├── starter.ps1
├── job_finder.py
├── dashboard.py
├── settings.json
├── blocked_domains.txt
├── blocked_country_domains.txt
├── templates/
│   └── index.html
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── charts.js
└── searxng/
    ├── docker-compose.yml
    └── settings.yml
```

## Useful PowerShell

Show files:

```powershell
Get-ChildItem
```

Show project tree:

```powershell
tree /F
```

Go up one folder:

```powershell
cd ..
```
