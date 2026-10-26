cask "dotsync-app" do
  version "0.7.0"
  sha256 "1508ada595e12395947aa0f99b78f7473e7f21b184574aa867dbfb3dfd273234"

  url "https://github.com/changja88/homebrew-dotsync/releases/download/v#{version}/dotsync-app-#{version}.zip"
  name "dotsync"
  desc "Claude Code account usage and switching, with desktop widgets"
  homepage "https://github.com/changja88/homebrew-dotsync"

  depends_on formula: "changja88/dotsync/dotsync"
  depends_on macos: :golden_gate

  app "dotsync.app"

  # An update replaces the app while its old widget may still be running; macOS
  # then refuses what that widget draws, and the widgets stay blank.
  postflight_steps do
    terminate_process "dotsyncWidget"
  end

  # Auto switch's LaunchAgent runs this app's executable. Uninstall and every
  # upgrade (only `signal:` is skipped on upgrade) take it out: until the new
  # app is opened and allowed, launchd running it would raise macOS's "not
  # opened" dialog. Opening the app puts the agent back. A script, not
  # `launchctl:`/`delete:`, which also run sudo and ask for a password.
  uninstall script: {
    executable:   "/bin/sh",
    args:         [
      "-c",
      "launchctl bootout gui/$(id -u)/com.changja88.dotsync.autoswitch; " \
      "rm -f \"$HOME/Library/LaunchAgents/com.changja88.dotsync.autoswitch.plist\"; exit 0",
    ],
    sudo:         false,
    must_succeed: false,
  }

  caveats <<~EOS
    dotsync.app is signed but not notarized. The first time you open it, and
    after each update: System Settings → Privacy & Security → "Open Anyway".
    Then add a widget from Edit Widgets: "Claude 계정", "지금 사용 중" or
    "Claude 계정 목록". Turning on 자동 교체 adds a background item; allow
    dotsync if macOS asks (System Settings → General → Login Items).
  EOS
end
