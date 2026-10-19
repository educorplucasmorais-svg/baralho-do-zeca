@echo off
rem Remove a tarefa diaria (pausa a serie)
schtasks /Delete /TN "Baralho do Zeca" /F
pause
