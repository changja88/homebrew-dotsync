cask "dotsync-app" do
  version "0.2.5"
  sha256 "2b245a735aa683ca46d471ec43504e7acec7acf2829eee75539b021e5b7971d5"

  url "https://github.com/changja88/homebrew-dotsync/releases/download/v#{version}/DotSync-#{version}-macOS.zip"
  name "DotSync"
  desc "Menu bar companion for DotSync config sync and Codex subscription usage"
  homepage "https://github.com/changja88/homebrew-dotsync"

  depends_on macos: :ventura
  depends_on formula: "changja88/dotsync/dotsync"

  app "DotSync.app"
end
