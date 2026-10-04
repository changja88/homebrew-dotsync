cask "dotsync-app" do
  version "0.5.0"
  sha256 "1a6056c37c144451b092508afa7a0c8723c1a5c612a099a3d88d55a322ca6839"

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

  caveats <<~EOS
    dotsync.app is signed but not notarized. The first time you open it, and
    after each update: System Settings → Privacy & Security → "Open Anyway".
    Then add a widget from Edit Widgets: "Claude 계정", "지금 사용 중" or
    "Claude 계정 목록".
  EOS
end
