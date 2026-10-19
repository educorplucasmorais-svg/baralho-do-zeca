@echo off
rem Cria uma tarefa no Agendador do Windows: todo dia as 16h roda postar-agora.bat
schtasks /Create /TN "Baralho do Zeca" /SC DAILY /ST 16:00 /TR "\"%~dp0postar-agora.bat\"" /F
echo.
echo Pronto. Para conferir: abra o Agendador de Tarefas e procure "Baralho do Zeca".
pause
