cask "dotsync-app" do
  version "0.2.6"
  sha256 "9f6ad547d9603515767a3c22e2683bc28002240ae21ceb36f32097d4f39722b1"

  url "https://github.com/changja88/homebrew-dotsync/releases/download/v#{version}/DotSync-#{version}-macOS.zip"
  name "DotSync"
  desc "Menu bar companion for DotSync config sync and Codex subscription usage"
  homepage "https://github.com/changja88/homebrew-dotsync"

  depends_on macos: :ventura
  depends_on formula: "changja88/dotsync/dotsync"

  app "DotSync.app"
end
