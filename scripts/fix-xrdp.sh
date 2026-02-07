#!/bin/bash
# Fix XRDP configuration for better Windows Remote Desktop experience
# Run with: sudo bash scripts/fix-xrdp.sh

set -e

echo "=== Fixing XRDP Configuration ==="

# 1. Add xrdp user to ssl-cert group for TLS support
echo "1. Adding xrdp to ssl-cert group..."
if ! groups xrdp | grep -q ssl-cert; then
    usermod -a -G ssl-cert xrdp
    echo "   Added xrdp to ssl-cert group"
else
    echo "   xrdp already in ssl-cert group"
fi

# 2. Create polkit rules for common XRDP issues
echo "2. Creating polkit rules..."

# Polkit rules directory (for newer polkit)
POLKIT_DIR="/etc/polkit-1/rules.d"
if [ -d "$POLKIT_DIR" ]; then
    cat > "$POLKIT_DIR/45-allow-colord.rules" << 'EOF'
polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.color-manager.create-device" ||
         action.id == "org.freedesktop.color-manager.create-profile" ||
         action.id == "org.freedesktop.color-manager.delete-device" ||
         action.id == "org.freedesktop.color-manager.delete-profile" ||
         action.id == "org.freedesktop.color-manager.modify-device" ||
         action.id == "org.freedesktop.color-manager.modify-profile") &&
        subject.isInGroup("users")) {
        return polkit.Result.YES;
    }
});
EOF
    echo "   Created colord polkit rules"
fi

# Legacy polkit directory (for older polkit)
POLKIT_LEGACY_DIR="/etc/polkit-1/localauthority/50-local.d"
if [ -d "$POLKIT_LEGACY_DIR" ]; then
    cat > "$POLKIT_LEGACY_DIR/45-allow-colord.pkla" << 'EOF'
[Allow Colord for all users]
Identity=unix-user:*
Action=org.freedesktop.color-manager.create-device;org.freedesktop.color-manager.create-profile;org.freedesktop.color-manager.delete-device;org.freedesktop.color-manager.delete-profile;org.freedesktop.color-manager.modify-device;org.freedesktop.color-manager.modify-profile
ResultAny=no
ResultInactive=no
ResultActive=yes
EOF
    echo "   Created legacy colord polkit rules"

    # Allow package-kit refresh without auth (prevents popup on login)
    cat > "$POLKIT_LEGACY_DIR/46-allow-packagekit.pkla" << 'EOF'
[Allow Package Management]
Identity=unix-user:*
Action=org.freedesktop.packagekit.system-sources-refresh
ResultAny=no
ResultInactive=no
ResultActive=yes
EOF
    echo "   Created packagekit polkit rules"
fi

# 3. Install tmux if not present
echo "3. Checking tmux..."
if ! command -v tmux &> /dev/null; then
    apt-get update && apt-get install -y tmux
    echo "   Installed tmux"
else
    echo "   tmux already installed"
fi

# 4. Restart XRDP to apply changes
echo "4. Restarting XRDP..."
systemctl restart xrdp
systemctl restart xrdp-sesman
echo "   XRDP restarted"

# 5. Verify TLS is now working
echo ""
echo "=== Verification ==="
if groups xrdp | grep -q ssl-cert; then
    echo "OK: xrdp user is in ssl-cert group"
else
    echo "WARN: xrdp user not in ssl-cert group"
fi

if systemctl is-active --quiet xrdp; then
    echo "OK: XRDP service is running"
else
    echo "WARN: XRDP service not running"
fi

echo ""
echo "=== Done ==="
echo "Reconnect with Windows Remote Desktop to test TLS connection."
echo "You should no longer see 'Permission denied' errors for the SSL key."
