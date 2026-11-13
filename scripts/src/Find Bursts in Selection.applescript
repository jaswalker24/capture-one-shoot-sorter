-- Capture One Shoot Sorter: Find Bursts in Selection
-- Acts on the selected variants in Capture One (or, for testing, on variant names passed as arguments).
-- Runs scripts_run.py in the runtime folder bursts in the background at low priority; a notification appears when done.
on run argv
	set thePaths to {}
	set useNames to false
	try
		if (count of argv) > 0 then set useNames to true
	end try
	if useNames then
		repeat with n in argv
			set nm to (n as text)
			tell application "Capture One"
				set vs to (variants of current document whose name is nm)
				if (count of vs) > 0 then set end of thePaths to (path of parent image of (item 1 of vs))
			end tell
		end repeat
	else
		tell application "Capture One"
			set sv to selected variants
			repeat with i from 1 to (count of sv)
				set end of thePaths to (path of parent image of (item i of sv))
			end repeat
		end tell
	end if
	if (count of thePaths) is 0 then
		display notification "Select one or more images first" with title "Find Bursts in Selection"
		return "nothing selected"
	end if
	set AppleScript's text item delimiters to linefeed
	set listText to thePaths as text
	set AppleScript's text item delimiters to ""
	set stamp to do shell script "date +%Y%m%d-%H%M%S"
	set runtimeHome to do shell script "if [ -n \"${LOCALAI_HOME:-}\" ]; then printf '%s' \"$LOCALAI_HOME\"; elif [ -n \"${SHOOT_SORTER_C1_HOME:-}\" ]; then printf '%s' \"$SHOOT_SORTER_C1_HOME\"; elif [ -d \"$HOME/localai\" ]; then printf '%s' \"$HOME/localai\"; else printf '%s' \"$HOME/shoot-sorter-c1\"; fi"
	do shell script "mkdir -p " & quoted form of (runtimeHome & "/logs/scripts")
	set listFile to runtimeHome & "/logs/scripts/selection-" & stamp & "-bursts.txt"
	set fh to open for access (POSIX file listFile) with write permission
	set eof of fh to 0
	write listText to fh as «class utf8»
	close access fh
	set logFile to runtimeHome & "/logs/scripts/" & stamp & "-bursts.log"
	do shell script "nohup nice -n 10 " & quoted form of (runtimeHome & "/venv/bin/python") & " " & quoted form of (runtimeHome & "/scripts_run.py") & " bursts " & quoted form of listFile & " > " & quoted form of logFile & " 2>&1 &"
	display notification ((count of thePaths) as text) & " image(s) queued" with title "Find Bursts in Selection"
	return logFile
end run
