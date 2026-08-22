cask "dotsync-app" do
  version "0.2.3"
  sha256 "64b6e66691704094b9c43d379e17b4437b5f9041ee7c0933ad995ec7b157d6d6"

  url "https://github.com/changja88/homebrew-dotsync/releases/download/v0.2.3/DotSync-0.2.3-macOS.zip"
  name "DotSync"
  desc "Menu bar companion for DotSync config sync and Codex subscription usage"
  homepage "https://github.com/changja88/homebrew-dotsync"

  depends_on macos: :ventura
  depends_on formula: "changja88/dotsync/dotsync"

  app "DotSync.app"
end
