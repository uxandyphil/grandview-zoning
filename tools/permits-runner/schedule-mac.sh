#!/bin/bash
# Schedules run.mjs to run every Sunday at 9:07 PM on this Mac (or right after it wakes, if it was asleep then).
#   bash schedule-mac.sh          turn the schedule on (or update it)
#   bash schedule-mac.sh remove   turn it off
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.grandview-zoning.permits"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ "$1" = "remove" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Schedule removed."
  exit 0
fi
NODE="$(command -v node || true)"
if [ -z "$NODE" ]; then echo "Node.js isn't installed (see README.md, step 1)."; exit 1; fi
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$NODE</string><string>$DIR/run.mjs</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$(dirname "$NODE"):/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>7</integer></dict>
  <key>StandardOutPath</key><string>$DIR/launchd.log</string>
  <key>StandardErrorPath</key><string>$DIR/launchd.log</string>
</dict>
</plist>
PLISTEOF
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Scheduled: every Sunday at 9:07 PM (or right after the Mac wakes up if it was asleep)."
echo "Run it now:   launchctl kickstart gui/$(id -u)/$LABEL"
echo "Progress:     $DIR/runner.log"
