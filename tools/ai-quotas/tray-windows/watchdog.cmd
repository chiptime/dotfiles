@echo off
curl -s -m 5 http://localhost:47623/api/health >nul 2>&1 || wsl.exe -d Ubuntu -u bruno systemctl --user restart ai-quotas.service
