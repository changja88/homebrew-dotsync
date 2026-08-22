cask "dotsync-app" do
  version "0.2.4"
  sha256 "20e8d2d616bac478513ec56354672351176bbd80e842075ca3c13c1b0a59bfc9"

  url "https://github.com/changja88/homebrew-dotsync/releases/download/v#{version}/DotSync-#{version}-macOS.zip"
  name "DotSync"
  desc "Menu bar companion for DotSync config sync and Codex subscription usage"
  homepage "https://github.com/changja88/homebrew-dotsync"

  depends_on macos: :ventura
  depends_on formula: "changja88/dotsync/dotsync"

  app "DotSync.app"
end
