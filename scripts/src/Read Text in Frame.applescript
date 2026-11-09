-- Trip Local AI: Read Text in Frame
-- Acts on the selected variants in Capture One (or, for testing, on variant names passed as arguments).
-- Runs ~/localai/scripts_run.py ocr in the background at low priority; a notification appears when done.
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
		display notification "Select one or more images first" with title "Read Text in Frame"
		return "nothing selected"
	end if
	set AppleScript's text item delimiters to linefeed
	set listText to thePaths as text
	set AppleScript's text item delimiters to ""
	set stamp to do shell script "date +%Y%m%d-%H%M%S"
	set listFile to (POSIX path of (path to home folder)) & "localai/logs/scripts/selection-" & stamp & "-ocr.txt"
	set fh to open for access (POSIX file listFile) with write permission
	set eof of fh to 0
	write listText to fh as «class utf8»
	close access fh
	set logFile to (POSIX path of (path to home folder)) & "localai/logs/scripts/" & stamp & "-ocr.log"
	do shell script "nohup nice -n 10 $HOME/localai/venv/bin/python $HOME/localai/scripts_run.py ocr " & quoted form of listFile & " > " & quoted form of logFile & " 2>&1 &"
	display notification ((count of thePaths) as text) & " image(s) queued" with title "Read Text in Frame"
	return logFile
end run
