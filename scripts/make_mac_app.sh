#!/bin/bash
# Creates "Local AI Workspace.app" in ~/Applications, so the app starts from Launchpad, Spotlight or the Dock.
# The bundle is a small launcher: it runs desktop.py from this project with the project's .venv,
# so code changes apply on the next launch. Re-run this script if you move the project folder.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Applications/Local AI Workspace.app"
PYTHON="$PROJECT/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "No virtual environment at $PROJECT/.venv. Create it first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Local AI Workspace</string>
  <key>CFBundleDisplayName</key><string>Local AI Workspace</string>
  <key>CFBundleIdentifier</key><string>local.ai.workspace</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/launcher" <<LAUNCH
#!/bin/bash
cd "$PROJECT"
exec "$PYTHON" "$PROJECT/desktop.py" >> "$PROJECT/logs/desktop.log" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/launcher"

# Optional icon: put a 1024x1024 PNG at assets/icon.png and it is converted here.
if [ -f "$PROJECT/assets/icon.png" ]; then
  ICONSET="$(mktemp -d)/AppIcon.iconset"
  mkdir -p "$ICONSET"
  for size in 16 32 128 256 512; do
    sips -z $size $size "$PROJECT/assets/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    sips -z $((size * 2)) $((size * 2)) "$PROJECT/assets/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
fi

touch "$APP"
echo "Created: $APP"
echo "Open it from Launchpad or Spotlight, then right-click its Dock icon > Options > Keep in Dock."
