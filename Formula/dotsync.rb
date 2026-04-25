class Dotsync < Formula
  desc "Sync app configs with a local folder"
  homepage "https://github.com/changja88/homebrew-dotsync"
  url "https://github.com/changja88/homebrew-dotsync/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"
  depends_on "python@3.12"

  def install
    libexec.install "lib/dotsync"
    # Install the entry script and pin its shebang to python@3.12 so users
    # don't accidentally run dotsync under an older system python3.
    bin.install "bin/dotsync"
    py = Formula["python@3.12"].opt_bin/"python3.12"
    inreplace bin/"dotsync", %r{^#!.*python.*$}, "#!#{py}"
    bin.env_script_all_files(libexec/"bin", PYTHONPATH: libexec)
  end

  def caveats
    <<~EOS
      dotsync needs a one-time setup before any sync command works:

        $ dotsync welcome     # quickstart guide
        $ dotsync init        # pick a folder + auto-detect installed apps

      To use dotsync from any directory, add this to your shell rc:

        export DOTSYNC_DIR="$HOME/your-sync-folder"

      Or just run dotsync from inside that folder — it auto-discovers
      dotsync.toml by walking up.
    EOS
  end

  test do
    assert_match "dotsync 0.1.0", shell_output("#{bin}/dotsync --version")
  end
end
