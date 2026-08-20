@echo off
title Project LINK - Start Nav2
ssh orin /home/wte/wheeltec_robot/scripts/standalone/start_nav2.sh
if errorlevel 1 echo Nav2 startup failed. Review the status above.
pause
